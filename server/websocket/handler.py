"""WebSocket connection handling: authentication, validation, dispatch and broadcasting."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import math
import time
from typing import Any, Awaitable, Callable

from server.config import ServerConfig
from server.models.room import Member, Room
from server.rooms.manager import RoomManager, ServerBusy
from shared.protocol import (MAX_FRAME_BYTES, MAX_PAGES, PDF_CHUNK_BYTES, PROTOCOL_VERSION, ValidationError,
                             clean_name, clean_text, normalize_room_code, valid_id, validate_live,
                             validate_pdf_info, validate_settings, MAX_ROOM_NAME)

log = logging.getLogger("studypdf.server")

try:  # websockets raises this when a peer goes away
    from websockets.exceptions import ConnectionClosed
except Exception:  # pragma: no cover
    ConnectionClosed = Exception  # type: ignore

DROPPABLE = {"cursor_moved", "live_stroke"}


class TokenBucket:
    def __init__(self, rate: float, burst: float) -> None:
        self.rate, self.burst, self.tokens, self.t = rate, burst, burst, time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now

    def try_take(self) -> bool:
        self._refill()
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    async def wait(self) -> None:
        while not self.try_take():
            await asyncio.sleep(0.02)


class Peer:
    """One websocket connection with a bounded outbound queue (a slow client can't stall the room)."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        addr = getattr(ws, "remote_address", None)
        self.ip = str(addr[0]) if addr else "?"
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=4000)
        self.room: Room | None = None
        self.member: Member | None = None
        self.bucket = TokenBucket(rate=250, burst=600)
        self.closed = False
        self.writer = asyncio.create_task(self._run_writer())
        self.streaming: asyncio.Task | None = None

    async def _run_writer(self) -> None:
        try:
            while True:
                data = await self.queue.get()
                if data is None:
                    break
                await self.ws.send(data)
        except Exception:
            self.closed = True

    def send_raw(self, raw: str, droppable: bool = False) -> None:
        if self.closed:
            return
        if droppable and self.queue.qsize() > 500:
            return
        try:
            self.queue.put_nowait(raw)
        except asyncio.QueueFull:
            self.closed = True
            asyncio.ensure_future(self.close())

    def send(self, msg: dict[str, Any], droppable: bool = False) -> None:
        self.send_raw(json.dumps(msg, separators=(",", ":")), droppable)

    def error(self, code: str, message: str = "") -> None:
        self.send({"t": "error", "code": code, "message": message})

    async def close(self, delay: float = 0.0) -> None:
        if delay:
            await asyncio.sleep(delay)
        self.closed = True
        try:
            await self.ws.close()
        except Exception:
            pass


class ConnectionHandler:
    def __init__(self, config: ServerConfig, manager: RoomManager) -> None:
        self.config, self.manager = config, manager
        self._handlers: dict[str, Callable[[Peer, dict], Awaitable[None] | None]] = {
            "resync": self.h_resync, "annotation_created": self.h_ann_created,
            "annotation_updated": self.h_ann_updated, "annotation_deleted": self.h_ann_deleted,
            "live_stroke": self.h_live, "cursor_moved": self.h_cursor, "page_changed": self.h_page,
            "chat": self.h_chat, "board_add": self.h_board_add, "question_set": self.h_question_set, "question_delete": self.h_question_delete,
            "timer": self.h_timer, "pdf_chunk": self.h_pdf_chunk, "pdf_request": self.h_pdf_request,
            "kick": self.h_kick, "set_settings": self.h_set_settings, "clear_annotations": self.h_clear,
            "reset_document": self.h_reset, "end_session": self.h_end, "ping": self.h_ping,
        }

    # ---- connection lifecycle -------------------------------------------
    async def handle(self, ws: Any) -> None:
        peer = Peer(ws)
        auth_guard = asyncio.create_task(self._auth_timeout(peer))
        try:
            async for raw in ws:
                if not isinstance(raw, str) or len(raw) > MAX_FRAME_BYTES:
                    peer.error("invalid", "Unsupported message.")
                    continue
                try:
                    msg = json.loads(raw)
                    t = msg["t"]
                    if not isinstance(t, str):
                        raise TypeError
                except (ValueError, KeyError, TypeError):
                    peer.error("invalid", "Malformed message.")
                    continue
                if t in DROPPABLE:
                    if not peer.bucket.try_take():
                        continue
                else:
                    await peer.bucket.wait()
                await self.dispatch(peer, t, msg)
        except ConnectionClosed:
            pass
        except Exception:
            log.exception("connection error")
        finally:
            auth_guard.cancel()
            await self.on_disconnect(peer)

    async def _auth_timeout(self, peer: Peer) -> None:
        await asyncio.sleep(15)
        if peer.member is None:
            await peer.close()

    async def dispatch(self, peer: Peer, t: str, msg: dict) -> None:
        if t == "create_room":
            return await self.h_create(peer, msg)
        if t == "join_room":
            return await self.h_join(peer, msg)
        if peer.member is None or peer.room is None:
            return peer.error("not_joined", "Join a room first.")
        if peer.room.ended:
            return
        fn = self._handlers.get(t)
        if fn is None:
            return peer.error("invalid", "Unknown message type.")
        try:
            res = fn(peer, msg)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            log.exception("handler %s failed", t)
            peer.error("server_error", "Something went wrong on the server.")

    async def on_disconnect(self, peer: Peer) -> None:
        room, member = peer.room, peer.member
        peer.closed = True
        peer.queue.put_nowait(None) if not peer.queue.full() else None
        if peer.streaming:
            peer.streaming.cancel()
        if room and member and room.peers.get(member.id) is peer:
            del room.peers[member.id]
            member.connected = False
            member.last_seen = time.time()
            room.save_member(member)
            room.touch()
            if member.is_host and room.upload:
                self._abort_upload(room)
            if not room.ended:
                self.broadcast(room, {"t": "user_left", "id": member.id, "removed": False, "member": member.public()})

    # ---- helpers --------------------------------------------------------------
    def broadcast(self, room: Room, msg: dict, exclude: Peer | None = None, droppable: bool = False) -> None:
        raw = json.dumps(msg, separators=(",", ":"))
        for p in list(room.peers.values()):
            if p is not exclude:
                p.send_raw(raw, droppable)

    def _attach(self, peer: Peer, room: Room, member: Member) -> None:
        old = room.peers.get(member.id)
        if old is not None and old is not peer:
            old.error("replaced", "You joined from another window.")
            asyncio.ensure_future(old.close(0.2))
        room.peers[member.id] = peer
        peer.room, peer.member = room, member
        member.connected = True
        member.last_seen = time.time()
        room.save_member(member)
        room.touch()

    def _joined_payload(self, kind: str, room: Room, member: Member, token: str | None = None, **extra: Any) -> dict:
        msg = {"t": kind, "you": member.id, "protocol": PROTOCOL_VERSION, "server_time": time.time(), **room.snapshot(), **extra}
        if token:
            msg["token"] = token
        return msg

    # ---- room creation / joining ------------------------------------------
    async def h_create(self, peer: Peer, msg: dict) -> None:
        if peer.member:
            return peer.error("already_joined")
        try:
            pdf = validate_pdf_info(msg.get("pdf"))
        except ValidationError as e:
            return peer.error("pdf_too_large" if "too_large" in str(e) else "invalid", "That PDF can't be shared (too large or invalid).")
        pw = msg.get("password")
        pw = pw[:128] if isinstance(pw, str) and pw else None
        try:
            room = self.manager.create(clean_text(msg.get("room_name"), MAX_ROOM_NAME, False) or "Study room", pw, peer.ip)
        except ServerBusy:
            return peer.error("server_busy", "The server is busy right now. Try again in a few minutes.")
        room.pdf = pdf
        member, token = room.add_member(clean_name(msg.get("user_name")), host=True)
        room.save_meta()
        self._attach(peer, room, member)
        peer.send(self._joined_payload("joined", room, member, token, created=True))

    async def h_join(self, peer: Peer, msg: dict) -> None:
        if peer.member:
            return peer.error("already_joined")
        if self.manager.is_blocked(peer.ip):
            return peer.error("too_many_attempts", "Too many failed attempts. Wait a few minutes and try again.")
        code = normalize_room_code(msg.get("code"))
        room = self.manager.get(code) if code else None
        if room is None or room.ended:
            self.manager.note_failed_join(peer.ip)
            expired = code in self.manager.expired
            return peer.error("room_expired" if expired else "room_not_found")
        member = room.member_by_token(msg.get("token"))
        reconnect = member is not None
        if not reconnect:
            if room.locked:
                return peer.error("room_locked")
            if not room.check_password(msg.get("password")):
                self.manager.note_failed_join(peer.ip)
                return peer.error("wrong_password")
            if len(room.members) >= self.config.max_members:
                return peer.error("room_full")
            member, token = room.add_member(clean_name(msg.get("user_name")))
        else:
            token = None
        assert member is not None
        self._attach(peer, room, member)
        peer.send(self._joined_payload("joined", room, member, token, reconnect=reconnect))
        self.broadcast(room, {"t": "user_joined", "member": member.public(), "reconnect": reconnect}, exclude=peer)

    # ---- state sync ----------------------------------------------------------------
    def h_resync(self, peer: Peer, msg: dict) -> None:
        peer.send(self._joined_payload("snapshot", peer.room, peer.member))   # type: ignore[arg-type]

    def h_ping(self, peer: Peer, msg: dict) -> None:
        peer.send({"t": "pong"})

    def _ack(self, peer: Peer, msg: dict, res: Any, **extra: Any) -> None:
        op = msg.get("op")
        peer.send({"t": "ack", "op": op if isinstance(op, str) else "", "ok": res.ok, "reason": res.reason, **extra})

    def h_ann_created(self, peer: Peer, msg: dict) -> None:
        room, m = peer.room, peer.member
        res = room.apply_create(m, msg.get("ann"))            # type: ignore[union-attr]
        if res.ok and not res.dup:
            self.broadcast(room, {"t": "annotation_created", "ann": res.ann, "by": m.id, "rev": room.rev}, exclude=peer)
        self._ack(peer, msg, res, z=res.ann["z"] if res.ann else 0, rev=room.rev)

    def h_ann_updated(self, peer: Peer, msg: dict) -> None:
        room, m = peer.room, peer.member
        res = room.apply_update(m, msg.get("id"), msg.get("patch"))   # type: ignore[union-attr]
        if res.ok:
            self.broadcast(room, {"t": "annotation_updated", "id": msg["id"], "patch": res.patch, "by": m.id,
                                  "rev": room.rev}, exclude=peer)
        self._ack(peer, msg, res, rev=room.rev)

    def h_ann_deleted(self, peer: Peer, msg: dict) -> None:
        room, m = peer.room, peer.member
        res = room.apply_delete(m, msg.get("id"))             # type: ignore[union-attr]
        if res.ok and not res.dup:
            self.broadcast(room, {"t": "annotation_deleted", "id": msg["id"], "by": m.id, "rev": room.rev}, exclude=peer)
        self._ack(peer, msg, res, rev=room.rev)

    def h_live(self, peer: Peer, msg: dict) -> None:
        room, m = peer.room, peer.member
        if not room.can_draw(m):                                # type: ignore[union-attr]
            return
        try:
            live = validate_live(msg)
        except ValidationError:
            return
        self.broadcast(room, {"t": "live_stroke", "uid": m.id, **live}, exclude=peer, droppable=True)  # type: ignore[union-attr]

    def h_cursor(self, peer: Peer, msg: dict) -> None:
        page, x, y = msg.get("page"), msg.get("x"), msg.get("y")
        if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < MAX_PAGES:
            return
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)):
            return
        self.broadcast(peer.room, {"t": "cursor_moved", "uid": peer.member.id, "page": page,       # type: ignore[union-attr]
                                   "x": round(max(-1000, min(20000, x)), 1), "y": round(max(-1000, min(20000, y)), 1)},
                       exclude=peer, droppable=True)

    def h_page(self, peer: Peer, msg: dict) -> None:
        room, m = peer.room, peer.member
        page = msg.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < max(room.pdf["pages"], 1):   # type: ignore[union-attr]
            return
        m.page = page                                           # type: ignore[union-attr]
        if m.is_host:                                           # type: ignore[union-attr]
            room.page = page                                    # type: ignore[union-attr]
            room.save_meta()                                    # type: ignore[union-attr]
        self.broadcast(room, {"t": "page_changed", "uid": m.id, "page": page}, exclude=peer)  # type: ignore[arg-type,union-attr]

    def h_chat(self, peer: Peer, msg: dict) -> None:
        entry = peer.room.add_chat(peer.member, msg.get("text"))    # type: ignore[union-attr,arg-type]
        if entry:
            self.broadcast(peer.room, {"t": "chat", "msg": entry})   # type: ignore[arg-type]
        elif not peer.room.can_chat(peer.member):                    # type: ignore[union-attr,arg-type]
            peer.error("forbidden", "Chat is disabled by the host.")

    def h_board_add(self, peer: Peer, msg: dict) -> None:
        room = peer.room
        assert room is not None
        board = room.add_board(peer.member, msg.get("title"))             # type: ignore[arg-type]
        if board is None:
            return peer.error("forbidden", "You can't add a whiteboard in this room.")
        cid = msg.get("cid")
        # sent to everyone including the creator, so all clients agree on the board's index
        self.broadcast(room, {"t": "board_added", "board": board, "index": len(room.boards) - 1,
                              "cid": cid if isinstance(cid, str) else ""})

    def h_question_set(self, peer: Peer, msg: dict) -> None:
        q = peer.room.set_question(peer.member, msg.get("question"))     # type: ignore[union-attr,arg-type]
        if q is None:
            return peer.error("forbidden", "You can't change questions in this room.")
        self.broadcast(peer.room, {"t": "question_updated", "question": q}, exclude=peer)   # type: ignore[arg-type]

    def h_question_delete(self, peer: Peer, msg: dict) -> None:
        if not peer.room.delete_question(peer.member, msg.get("id")):    # type: ignore[union-attr,arg-type]
            return peer.error("forbidden", "You can't change questions in this room.")
        self.broadcast(peer.room, {"t": "question_deleted", "id": msg["id"]}, exclude=peer)   # type: ignore[arg-type]

    # ---- timer -------------------------------------------------------------------------
    def h_timer(self, peer: Peer, msg: dict) -> None:
        room = peer.room
        assert room is not None
        if not room.can_timer(peer.member):                     # type: ignore[arg-type]
            return peer.error("forbidden", "The host disabled timer controls.")
        action, now = msg.get("action"), time.time()
        if action == "start":
            room.timer.start(now)
        elif action == "pause":
            room.timer.pause(now)
        elif action == "reset":
            room.timer.reset()
        elif action == "config":
            try:
                room.timer.configure(str(msg.get("mode")), int(msg.get("focus_min", 25)), int(msg.get("break_min", 5)))
            except (TypeError, ValueError):
                return peer.error("invalid")
        else:
            return peer.error("invalid")
        room.save_meta()
        self.manager.schedule_timer(room, self._timer_finished)
        self.broadcast(room, {"t": "timer_state", "timer": room.timer.to_dict(now), "finished": False})

    async def _timer_finished(self, room: Room) -> None:
        if room.ended or room.code not in self.manager.rooms:
            return
        room.timer.finish(time.time())
        room.save_meta()
        self.manager.schedule_timer(room, self._timer_finished)
        self.broadcast(room, {"t": "timer_state", "timer": room.timer.to_dict(time.time()), "finished": True})

    # ---- PDF transfer -------------------------------------------------------------------
    def _abort_upload(self, room: Room) -> None:
        up, room.upload = room.upload, None
        if up:
            try:
                up["fh"].close()
            except Exception:
                pass
            self.manager.part_path(room.code).unlink(missing_ok=True)

    def h_pdf_chunk(self, peer: Peer, msg: dict) -> None:
        room, m = peer.room, peer.member
        assert room is not None and m is not None
        if not m.is_host or room.pdf["ready"]:
            return peer.error("forbidden", "Only the host can upload the document.")
        seq, total, data = msg.get("seq"), msg.get("total"), msg.get("data")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (seq, total)) or not isinstance(data, str) \
                or total < 1 or total > room.pdf["size"] // 1024 + 2 or not 0 <= seq < total:
            return peer.error("invalid")
        try:
            chunk = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return peer.error("invalid")
        if len(chunk) > PDF_CHUNK_BYTES:
            return peer.error("invalid")
        if seq == 0:
            self._abort_upload(room)
            if not chunk.startswith(b"%PDF-"):
                return peer.error("pdf_invalid", "That file is not a PDF.")
            room.upload = {"fh": open(self.manager.part_path(room.code), "wb"), "next": 0, "size": 0,
                           "sha": hashlib.sha256()}
        up = room.upload
        if up is None or seq != up["next"]:
            return peer.error("invalid", "Upload out of order.")
        up["size"] += len(chunk)
        if up["size"] > room.pdf["size"]:
            self._abort_upload(room)
            return peer.error("pdf_invalid", "Upload larger than announced.")
        up["fh"].write(chunk)
        up["sha"].update(chunk)
        up["next"] += 1
        if seq == total - 1:
            up["fh"].close()
            room.upload = None
            part = self.manager.part_path(room.code)
            if up["size"] != room.pdf["size"] or up["sha"].hexdigest() != room.pdf["sha256"]:
                part.unlink(missing_ok=True)
                return peer.error("pdf_invalid", "The upload was corrupted. Please try again.")
            part.replace(self.manager.pdf_path(room.code))
            room.pdf["ready"] = True
            room.save_meta()
            self.broadcast(room, {"t": "pdf_ready", "pdf": room.pdf})

    def h_pdf_request(self, peer: Peer, msg: dict) -> None:
        room = peer.room
        assert room is not None
        if not room.pdf["ready"]:
            return peer.error("pdf_not_ready", "The host hasn't finished uploading the PDF yet.")
        if peer.streaming and not peer.streaming.done():
            return
        peer.streaming = asyncio.create_task(self._stream_pdf(peer, room))

    async def _stream_pdf(self, peer: Peer, room: Room) -> None:
        path = self.manager.pdf_path(room.code)
        try:
            size = path.stat().st_size
            total = max(1, math.ceil(size / PDF_CHUNK_BYTES))
            with open(path, "rb") as fh:
                for seq in range(total):
                    data = base64.b64encode(fh.read(PDF_CHUNK_BYTES)).decode()
                    await peer.ws.send(json.dumps({"t": "pdf_chunk", "seq": seq, "total": total, "data": data}))
            await peer.ws.send(json.dumps({"t": "pdf_complete", "pdf": room.pdf}))
        except (ConnectionClosed, OSError):
            pass

    # ---- host-only actions (checked here, regardless of what the UI shows) -------------
    def _host(self, peer: Peer) -> bool:
        if peer.member is None or not peer.member.is_host:
            peer.error("forbidden", "Only the host can do that.")
            return False
        return True

    def h_kick(self, peer: Peer, msg: dict) -> None:
        if not self._host(peer):
            return
        room, uid = peer.room, msg.get("id")
        assert room is not None
        target = room.members.get(uid) if isinstance(uid, str) else None
        if target is None or target.is_host:
            return peer.error("invalid", "You can't remove that user.")
        victim = room.peers.get(target.id)
        room.remove_member(target.id)
        if victim:
            victim.send({"t": "kicked"})
            asyncio.ensure_future(victim.close(0.4))
            room.peers.pop(target.id, None)
        self.broadcast(room, {"t": "user_left", "id": target.id, "removed": True, "member": target.public()})

    def h_set_settings(self, peer: Peer, msg: dict) -> None:
        if not self._host(peer):
            return
        room = peer.room
        assert room is not None
        try:
            room.settings.update(validate_settings(msg.get("settings", {})))
        except ValidationError:
            return peer.error("invalid")
        if "locked" in msg:
            room.locked = bool(msg["locked"])
        room.save_meta()
        self.broadcast(room, {"t": "settings_changed", "settings": room.settings, "locked": room.locked})

    def h_clear(self, peer: Peer, msg: dict) -> None:
        if self._host(peer):
            peer.room.clear_annotations()                        # type: ignore[union-attr]
            self.broadcast(peer.room, {"t": "annotations_cleared"})   # type: ignore[arg-type]

    def h_reset(self, peer: Peer, msg: dict) -> None:
        if self._host(peer):
            room = peer.room
            room.reset_document()                                # type: ignore[union-attr]
            self.manager.schedule_timer(room, self._timer_finished)   # type: ignore[arg-type]
            self.broadcast(room, {"t": "document_reset", **room.snapshot()})   # type: ignore[arg-type,union-attr]

    async def h_end(self, peer: Peer, msg: dict) -> None:
        if not self._host(peer):
            return
        room = peer.room
        assert room is not None
        room.ended = True
        self.broadcast(room, {"t": "session_ended"})
        peers = list(room.peers.values())
        self.manager.delete(room)
        await asyncio.sleep(0.4)
        for p in peers:
            await p.close()

    # ---- background maintenance ---------------------------------------------------------
    async def maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for room in list(self.manager.rooms.values()):
                for m in [m for m in room.members.values()
                          if not m.connected and not m.is_host and now - m.last_seen > self.config.offline_member_ttl]:
                    room.remove_member(m.id)
                    self.broadcast(room, {"t": "user_left", "id": m.id, "removed": True, "member": m.public()})
            for room in self.manager.expiring_rooms():
                log.info("room %s expired", room.code)
                self.manager.delete(room, expired=True)
