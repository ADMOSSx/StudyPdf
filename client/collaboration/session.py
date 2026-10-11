"""Collaboration session: binds the local annotation store to the server and tracks room state."""
from __future__ import annotations

import base64
import copy
import hashlib
import math
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from client.annotations.model import AnnotationStore
from client.collaboration.net import CollabClient
from client.collaboration.questions import QuestionStore
from shared.protocol import DEFAULT_SETTINGS, PDF_CHUNK_BYTES

ERROR_TEXT = {
    "room_not_found": "That room doesn't exist. Check the code and try again.",
    "room_expired": "That room has expired. Ask the host to create a new one.",
    "wrong_password": "Wrong room password.",
    "room_locked": "The host has locked this room, so nobody new can join.",
    "room_full": "This room is full.",
    "room_ended": "The host has ended this session.",
    "too_many_attempts": "Too many failed attempts. Wait a few minutes and try again.",
    "server_busy": "The server is busy right now. Try again in a few minutes.",
    "pdf_too_large": "That PDF is too large to share (limit 500 MB).",
}
FATAL = set(ERROR_TEXT) | {"replaced"}


class SessionController(QObject):
    status = Signal(str)             # online | connecting | reconnecting | syncing | offline
    entered = Signal(dict)           # first successful create/join (full snapshot)
    resynced = Signal()
    members_changed = Signal()
    settings_changed = Signal()
    chat_added = Signal(dict)
    chat_loaded = Signal(list)
    timer_changed = Signal(dict, bool)
    follow_page = Signal(int)
    pdf_progress = Signal(int, int, str)    # done, total, "upload" | "download"
    pdf_ready = Signal(str, str)            # cached file path, display name
    pdf_failed = Signal(str)
    notice = Signal(str)
    fatal = Signal(str, str)                # code, friendly message
    cursors_changed = Signal()
    live_changed = Signal(int)
    remote_cleared = Signal()

    def __init__(self, store: AnnotationStore, questions: QuestionStore, cache_dir: Path, boards: Any = None) -> None:
        super().__init__()
        self.store, self.questions, self.cache_dir, self.boards = store, questions, cache_dir, boards
        self.client = CollabClient(self)
        self.client.opened.connect(self._on_open)
        self.client.closed.connect(lambda: self.status.emit("reconnecting"))
        self.client.status.connect(self._on_client_status)
        self.client.received.connect(self._on_message)
        self.client.failed.connect(self._on_failed)
        self._reset()
        self._upload_timer = QTimer(self, interval=25)
        self._upload_timer.timeout.connect(self._pump_upload)
        self._live_gc = QTimer(self, interval=3000)
        self._live_gc.timeout.connect(self._gc_live)

    # ---- state -----------------------------------------------------------------
    def _reset(self) -> None:
        self.active = False
        self.joined_once = False
        self.code = self.room_name = self.me = self.token = ""
        self.url = ""
        self.hello: dict[str, Any] = {}
        self.members: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, bool] = dict(DEFAULT_SETTINGS)
        self.locked = False
        self.host_id = ""
        self.pdf: dict[str, Any] = {}
        self.local_pdf_path = ""
        if hasattr(self, "cursors"):          # keep the same dict objects: the canvas holds references to them
            self.cursors.clear()
            self.live.clear()
        else:
            self.cursors: dict[str, dict[str, Any]] = {}
            self.live: dict[str, dict[str, Any]] = {}
        self.outbox: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self.follow_host = True
        self._upload: dict[str, Any] | None = None
        self._download: dict[str, Any] | None = None
        self._pdf_delivered = False
        self._last_cursor = 0.0
        self._last_resync = 0.0
        self._pending_initial: list[dict[str, Any]] = []
        self._pending_boards: list[dict[str, Any]] = []

    @property
    def is_host(self) -> bool:
        return bool(self.members.get(self.me, {}).get("is_host"))

    def me_info(self) -> tuple[str, str]:
        m = self.members.get(self.me)
        return (self.me, m["name"]) if m else ("local", "You")

    # permissions mirror the server's rules for UI purposes only; the server is the authority
    def can_draw(self) -> bool:
        return not self.active or self.is_host or self.settings["can_draw"]

    def can_modify(self, ann: dict[str, Any]) -> bool:
        if not self.active or self.is_host:
            return True
        return self.settings["can_draw"] and (ann.get("owner") == self.me or self.settings["can_edit_others"])

    def can_chat(self) -> bool:
        return self.is_host or self.settings["can_chat"]

    def can_timer(self) -> bool:
        return not self.active or self.is_host or self.settings["can_control_timer"]

    def can_download(self) -> bool:
        return not self.active or self.is_host or self.settings["allow_download"]

    # ---- starting / leaving ---------------------------------------------------------
    def start_create(self, url: str, room_name: str, user_name: str, password: str, pdf_path: str,
                     pdf_info: dict[str, Any]) -> None:
        self._reset()
        self.active, self.url, self.local_pdf_path = True, url, pdf_path
        self.hello = {"t": "create_room", "room_name": room_name, "user_name": user_name,
                      "password": password, "pdf": pdf_info}
        self._pending_initial = self.store.all()
        self._pending_boards = list(self.boards.items) if self.boards is not None else []
        self._begin()

    def start_join(self, url: str, code: str, user_name: str, password: str) -> None:
        self._reset()
        self.active, self.url = True, url
        self.hello = {"t": "join_room", "code": code, "user_name": user_name, "password": password}
        self._begin()

    def _begin(self) -> None:
        self._live_gc.start()
        self.client.connect_to(self.url)

    def leave(self) -> None:
        self._upload_timer.stop()
        self._live_gc.stop()
        self.client.close()
        self._reset()
        self.status.emit("offline")

    # ---- connection events ---------------------------------------------------------------
    def _on_client_status(self, s: str) -> None:
        if s in ("connecting", "reconnecting", "offline") and self.active:
            self.status.emit(s)

    def _on_failed(self, reason: str) -> None:
        self.active = False
        self.fatal.emit("unreachable", f"Couldn't reach the server at {self.url}. Check the address, your internet "
                                       "connection, and that the server is running.")

    def _on_open(self) -> None:
        if self.joined_once and self.token:
            self.status.emit("syncing")
            self.client.send({"t": "join_room", "code": self.code, "token": self.token, "user_name": ""})
        else:
            self.client.send(self.hello)

    # ---- outgoing ------------------------------------------------------------------------------
    def send_op(self, op: dict[str, Any]) -> None:
        if not self.active:
            return
        op = dict(op, op=uuid.uuid4().hex[:12])
        self.outbox[op["op"]] = op
        self.client.send(op)

    def send_question_op(self, op: dict[str, Any]) -> None:
        if self.active:
            self.client.send(op)

    def send_cursor(self, page: int, x: float, y: float) -> None:
        now = time.monotonic()
        if self.active and self.joined_once and now - self._last_cursor > 0.033:
            self._last_cursor = now
            self.client.send({"t": "cursor_moved", "page": page, "x": round(x, 1), "y": round(y, 1)})

    def send_live(self, payload: dict[str, Any]) -> None:
        if self.active and self.joined_once:
            self.client.send({"t": "live_stroke", **payload})

    def send_page(self, page: int) -> None:
        if self.active and self.joined_once:
            if self.me in self.members:
                self.members[self.me]["page"] = page
            self.client.send({"t": "page_changed", "page": page})

    def send_chat(self, text: str) -> None:
        if self.active and text.strip():
            self.client.send({"t": "chat", "text": text})

    def timer_action(self, action: str, **kw: Any) -> None:
        self.client.send({"t": "timer", "action": action, **kw})

    def host_action(self, t: str, **kw: Any) -> None:
        self.client.send({"t": t, **kw})

    def request_resync(self) -> None:
        if time.monotonic() - self._last_resync > 1.0:
            self._last_resync = time.monotonic()
            self.client.send({"t": "resync"})

    # ---- incoming ----------------------------------------------------------------------------------
    def _on_message(self, msg: dict[str, Any]) -> None:
        if not self.active:
            return
        fn = getattr(self, "m_" + str(msg.get("t")), None)
        if fn:
            fn(msg)

    def _apply_state(self, msg: dict[str, Any]) -> None:
        room = msg["room"]
        self.code, self.room_name, self.me = room["code"], room["name"], msg["you"]
        self.settings.update(room["settings"])
        self.locked, self.host_id = room["locked"], room["host_id"]
        self.members = {m["id"]: m for m in msg["members"]}
        self.pdf = msg["pdf"]
        self.questions.replace_all(msg["questions"])
        if self.boards is not None:
            self.boards.replace_all(msg.get("boards", []))
        self.chat_loaded.emit(msg["chat"])
        self.timer_changed.emit(msg["timer"], False)
        self.members_changed.emit()
        self.settings_changed.emit()

    def m_joined(self, msg: dict[str, Any]) -> None:
        first = not self.joined_once
        if msg.get("token"):
            self.token = msg["token"]
        self._apply_state(msg)
        self.joined_once = True
        if first and msg.get("created"):
            for board in self._pending_boards:                  # re-create the host's local boards first, in order
                self.client.send({"t": "board_add", "title": board.get("title", "Board")})
            self._pending_boards = []
            for ann in self._pending_initial:                  # share what the host already drew
                ann["owner"], ann["owner_name"] = self.me_info()
                self.send_op({"t": "annotation_created", "ann": copy.deepcopy(ann)})
            self._pending_initial = []
            if self.questions.items:
                for q in self.questions.items.values():
                    self.client.send({"t": "question_set", "question": dict(q)})
        else:
            self._replace_annotations(msg["annotations"])
        self.status.emit("online")
        (self.entered if first else self.resynced).emit(msg)
        if self.is_host:
            if not self.pdf.get("ready"):
                self._start_upload()
        elif self.pdf.get("ready") and not self._pdf_delivered:
            self._request_pdf()

    def m_snapshot(self, msg: dict[str, Any]) -> None:
        self._apply_state(msg)
        self._replace_annotations(msg["annotations"])
        self.status.emit("online")

    def _replace_annotations(self, server_items: list[dict[str, Any]]) -> None:
        """Adopt server truth, then replay our un-acknowledged edits so nothing the user drew is lost."""
        self.store.replace_all(server_items)
        pending = list(self.outbox.values())
        for op in pending:
            t = op["t"]
            if t == "annotation_created" and op["ann"]["id"] not in self.store.anns:
                self.store.add(copy.deepcopy(op["ann"]))
            elif t == "annotation_updated":
                self.store.update(op["id"], copy.deepcopy(op["patch"]))
            elif t == "annotation_deleted":
                self.store.remove(op["id"])
            self.client.send(op)

    def m_ack(self, msg: dict[str, Any]) -> None:
        op = self.outbox.pop(msg.get("op", ""), None)
        if msg.get("ok"):
            if op and op["t"] == "annotation_created" and msg.get("z"):
                self.store.set_z(op["ann"]["id"], int(msg["z"]))
        else:
            self.request_resync()          # rejected (forbidden / deleted elsewhere): adopt server state

    def m_annotation_created(self, msg: dict[str, Any]) -> None:
        self.live.pop(msg["ann"]["id"], None)
        self.store.add(msg["ann"])
        self.live_changed.emit(msg["ann"]["page"])

    def m_annotation_updated(self, msg: dict[str, Any]) -> None:
        self.store.update(msg["id"], msg["patch"])

    def m_annotation_deleted(self, msg: dict[str, Any]) -> None:
        self.store.remove(msg["id"])

    def m_annotations_cleared(self, msg: dict[str, Any]) -> None:
        self.outbox.clear()
        self.store.clear()
        self.notice.emit("The host cleared all annotations.")

    def m_document_reset(self, msg: dict[str, Any]) -> None:
        self.outbox.clear()
        self._apply_state(msg)
        self.store.replace_all(msg["annotations"])
        self.follow_page.emit(0)
        self.notice.emit("The host reset the document.")

    def m_live_stroke(self, msg: dict[str, Any]) -> None:
        cur = self.live.setdefault(msg["id"], {"uid": msg["uid"], "kind": msg["kind"], "page": msg["page"],
                                               "style": msg["style"], "pts": [], "id": msg["id"], "t": 0.0})
        cur["pts"].extend(msg["append"])
        cur["t"] = time.monotonic()
        self.live_changed.emit(msg["page"])

    def m_cursor_moved(self, msg: dict[str, Any]) -> None:
        m = self.members.get(msg["uid"])
        if m:
            self.cursors[msg["uid"]] = {"page": msg["page"], "x": msg["x"], "y": msg["y"], "name": m["name"],
                                        "color": m["color"], "t": time.monotonic()}
            self.cursors_changed.emit()

    def m_page_changed(self, msg: dict[str, Any]) -> None:
        m = self.members.get(msg["uid"])
        if m:
            m["page"] = msg["page"]
            self.members_changed.emit()
            if m.get("is_host") and self.settings["sync_pages"] and self.follow_host and msg["uid"] != self.me:
                self.follow_page.emit(msg["page"])

    def m_user_joined(self, msg: dict[str, Any]) -> None:
        self.members[msg["member"]["id"]] = msg["member"]
        self.members_changed.emit()
        if not msg.get("reconnect"):
            self.notice.emit(f"{msg['member']['name']} joined")

    def m_user_left(self, msg: dict[str, Any]) -> None:
        mid = msg["id"]
        if msg.get("removed"):
            self.members.pop(mid, None)
        elif mid in self.members:
            self.members[mid]["connected"] = False
        self.cursors.pop(mid, None)
        self.members_changed.emit()
        self.cursors_changed.emit()

    def m_chat(self, msg: dict[str, Any]) -> None:
        self.chat_added.emit(msg["msg"])

    def m_board_added(self, msg: dict[str, Any]) -> None:
        if self.boards is not None:
            self.boards.apply_remote(msg["board"], int(msg.get("index", 0)), msg.get("cid", ""))

    def m_question_updated(self, msg: dict[str, Any]) -> None:
        self.questions.apply_remote(msg["question"])

    def m_question_deleted(self, msg: dict[str, Any]) -> None:
        self.questions.remove_remote(msg["id"])

    def m_timer_state(self, msg: dict[str, Any]) -> None:
        self.timer_changed.emit(msg["timer"], bool(msg.get("finished")))

    def m_settings_changed(self, msg: dict[str, Any]) -> None:
        self.settings.update(msg["settings"])
        self.locked = msg["locked"]
        self.settings_changed.emit()

    def m_kicked(self, msg: dict[str, Any]) -> None:
        self._end("kicked", "The host removed you from this room.")

    def m_session_ended(self, msg: dict[str, Any]) -> None:
        self._end("ended", "The host ended the session.")

    def m_pong(self, msg: dict[str, Any]) -> None:
        pass

    def m_error(self, msg: dict[str, Any]) -> None:
        code = msg.get("code", "")
        if code in FATAL:
            self._end(code, ERROR_TEXT.get(code, "You were disconnected from the room."))
        elif code in ("forbidden", "invalid"):
            if msg.get("message"):
                self.notice.emit(msg["message"])
            self.request_resync()
        elif code == "pdf_invalid":
            self.pdf_failed.emit(msg.get("message") or "The PDF transfer failed.")
        elif msg.get("message"):
            self.notice.emit(msg["message"])

    def _end(self, code: str, text: str) -> None:
        self._upload_timer.stop()
        self.client.close()
        self.active = False
        self.fatal.emit(code, text)

    # ---- PDF transfer --------------------------------------------------------------------------------
    def _start_upload(self) -> None:
        # Keep only one chunk in memory; PDFs may be hundreds of megabytes.
        try:
            path = Path(self.local_pdf_path)
            size = path.stat().st_size
            total = max(1, math.ceil(size / PDF_CHUNK_BYTES))
            fh = path.open("rb")
        except OSError:
            self.pdf_failed.emit("Couldn't read the PDF to share it. Was the file moved?")
            return
        self._upload = {"fh": fh, "total": total, "i": 0}
        self._upload_timer.start()

    def _pump_upload(self) -> None:
        up = self._upload
        if not up or not self.client.online:
            return
        # Send a small burst of larger chunks each timer tick. This reduces JSON/WebSocket
        # overhead on big PDFs without loading the whole file or blocking the Qt event loop.
        for _ in range(4):
            i = up["i"]
            chunk = up["fh"].read(PDF_CHUNK_BYTES)
            if not chunk:
                up["fh"].close()
                self._upload = None
                self._upload_timer.stop()
                self.pdf_progress.emit(up["total"], up["total"], "upload")
                return
            self.client.send({"t": "pdf_chunk", "seq": i, "total": up["total"], "data": base64.b64encode(chunk).decode()})
            up["i"] += 1
            if up["i"] >= up["total"]:
                up["fh"].close()
                self._upload = None
                self._upload_timer.stop()
                self.pdf_progress.emit(up["total"], up["total"], "upload")
                return
        self.pdf_progress.emit(up["i"], up["total"], "upload")

    def m_pdf_ready(self, msg: dict[str, Any]) -> None:
        self.pdf = msg["pdf"]
        if not self.is_host and not self._pdf_delivered:
            self._request_pdf()
        elif self.is_host:
            self.members_changed.emit()

    def _request_pdf(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{self.code}.pdf.part"
        try:
            fh = path.open("wb")
        except OSError:
            self.pdf_failed.emit("Couldn't create a temporary file for the downloaded PDF.")
            return
        self._download = {"fh": fh, "path": path, "next": 0, "total": None, "sha": hashlib.sha256()}
        self.client.send({"t": "pdf_request"})

    def m_pdf_chunk(self, msg: dict[str, Any]) -> None:
        down = self._download
        if down is None:
            return
        try:
            seq, total = int(msg["seq"]), int(msg["total"])
            chunk = base64.b64decode(msg["data"], validate=True)
            if seq != down["next"] or total < 1 or seq >= total:
                raise ValueError("out-of-order PDF chunk")
            if down["total"] is not None and total != down["total"]:
                raise ValueError("inconsistent PDF chunk count")
            down["total"] = total
            down["fh"].write(chunk)
            down["sha"].update(chunk)
            down["next"] += 1
        except (ValueError, KeyError, OSError):
            try:
                down["fh"].close()
                down["path"].unlink(missing_ok=True)
            except OSError:
                pass
            self._download = None
            self.pdf_failed.emit("The PDF transfer was interrupted or corrupted. Rejoin the room to try again.")
            return
        self.pdf_progress.emit(down["next"], total, "download")

    def m_pdf_complete(self, msg: dict[str, Any]) -> None:
        down, self._download = self._download, None
        if not down:
            return
        try:
            down["fh"].flush()
            down["fh"].close()
            expected_total = down["total"] or 0
            if down["next"] != expected_total or down["sha"].hexdigest() != self.pdf.get("sha256"):
                down["path"].unlink(missing_ok=True)
                self.pdf_failed.emit("The downloaded PDF was corrupted. Rejoin the room to try again.")
                return
            path = self.cache_dir / f"{self.code}.pdf"
            down["path"].replace(path)
        except OSError:
            self.pdf_failed.emit("Couldn't save the downloaded PDF. Check your disk space and permissions.")
            return
        self._pdf_delivered = True
        self.pdf_ready.emit(str(path), self.pdf.get("name", "document.pdf"))

    # ---- housekeeping -----------------------------------------------------------------------------------
    def _gc_live(self) -> None:
        now = time.monotonic()
        stale = [k for k, v in self.live.items() if now - v["t"] > 8]
        for k in stale:
            page = self.live.pop(k)["page"]
            self.live_changed.emit(page)
