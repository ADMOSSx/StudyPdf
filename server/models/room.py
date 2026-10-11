"""Room state and all rules (permissions, conflict handling). Pure logic: no networking here."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from database.server_db import ServerDatabase
from shared.protocol import (BOARD_BASE, DEFAULT_SETTINGS, MAX_ANNOTATIONS, MAX_BOARDS, MAX_CHAT, TEXT_KINDS,
                             ValidationError, clean_text, validate_annotation, validate_board_title, validate_patch,
                             validate_question, valid_id)
from shared.timer import TimerState

COLORS = ["#ff6b6b", "#4dabf7", "#51cf66", "#fcc419", "#cc5de8", "#ff922b", "#22b8cf", "#f06595", "#94d82d", "#9775fa"]
MAX_TOMBSTONES = 50000


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


@dataclass
class Result:
    ok: bool
    reason: str = ""
    ann: dict[str, Any] | None = None
    patch: dict[str, Any] | None = None
    dup: bool = False


@dataclass
class Member:
    id: str
    name: str
    color: str
    token_hash: str
    is_host: bool = False
    connected: bool = False
    last_seen: float = field(default_factory=time.time)
    page: int = 0

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "color": self.color, "is_host": self.is_host,
                "connected": self.connected, "page": self.page}

    def row(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "color": self.color, "token_hash": self.token_hash,
                "is_host": self.is_host, "last_seen": self.last_seen, "page": self.page}


class Room:
    def __init__(self, code: str, name: str, password: str | None = None, db: ServerDatabase | None = None) -> None:
        self.code, self.name, self.db = code, name, db
        self.salt = secrets.token_bytes(16)
        self.pw_hash = hash_password(password, self.salt) if password else ""
        self.settings: dict[str, bool] = dict(DEFAULT_SETTINGS)
        self.locked = False
        self.ended = False
        self.members: dict[str, Member] = {}
        self.annotations: dict[str, dict[str, Any]] = {}
        self.tombstones: dict[str, None] = {}
        self.questions: dict[str, dict[str, Any]] = {}
        self.boards: list[dict[str, Any]] = []       # whiteboard pages, append-only: board i is page BOARD_BASE + i
        self.chat: list[dict[str, Any]] = []
        self.timer = TimerState()
        self.page = 0
        self.rev = 0
        self.pdf: dict[str, Any] = {"name": "", "size": 0, "sha256": "", "pages": 0, "ready": False}
        self.created = self.last_active = time.time()
        self.peers: dict[str, Any] = {}      # member id -> live connection (set by the websocket layer)
        self.upload: dict[str, Any] | None = None

    # ---- persistence -------------------------------------------------
    def save_meta(self) -> None:
        if self.db:
            self.db.save_room(self.code, {
                "name": self.name, "salt": self.salt.hex(), "pw_hash": self.pw_hash, "settings": self.settings,
                "locked": self.locked, "page": self.page, "rev": self.rev, "pdf": self.pdf, "boards": self.boards,
                "timer": self.timer.to_dict(time.time()), "created": self.created, "last_active": self.last_active})

    @classmethod
    def restore(cls, row: dict[str, Any], db: ServerDatabase) -> "Room":
        room = cls(row["code"], row.get("name", "Study room"), None, db)
        room.salt = bytes.fromhex(row.get("salt", "00" * 16))
        room.pw_hash = row.get("pw_hash", "")
        room.settings.update(row.get("settings", {}))
        room.locked, room.page, room.rev = row.get("locked", False), row.get("page", 0), row.get("rev", 0)
        room.pdf = row.get("pdf", room.pdf)
        room.boards = list(row.get("boards", []))[:MAX_BOARDS]
        room.timer = TimerState.from_dict(row.get("timer", {}), time.time())
        room.created, room.last_active = row.get("created", time.time()), row.get("last_active", time.time())
        for m in row.get("members", []):
            room.members[m["id"]] = Member(m["id"], m["name"], m["color"], m["token_hash"], m.get("is_host", False),
                                           False, m.get("last_seen", time.time()), m.get("page", 0))
        room.annotations = {a["id"]: a for a in row.get("annotations", [])}
        room.questions = {q["id"]: q for q in row.get("questions", [])}
        room.chat = row.get("chat", [])
        return room

    def touch(self) -> None:
        self.last_active = time.time()

    # ---- membership --------------------------------------------------
    def check_password(self, password: Any) -> bool:
        if not self.pw_hash:
            return True
        if not isinstance(password, str):
            return False
        return hmac.compare_digest(hash_password(password[:128], self.salt), self.pw_hash)

    def add_member(self, name: str, host: bool = False) -> tuple[Member, str]:
        used = {m.color for m in self.members.values()}
        color = next((c for c in COLORS if c not in used), COLORS[len(self.members) % len(COLORS)])
        token = secrets.token_urlsafe(24)
        member = Member(uuid.uuid4().hex[:10], name, color, hash_token(token), is_host=host)
        self.members[member.id] = member
        if self.db:
            self.db.put("members", self.code, member.id, member.row())
        return member, token

    def member_by_token(self, token: Any) -> Member | None:
        if not isinstance(token, str) or not token:
            return None
        digest = hash_token(token)
        return next((m for m in self.members.values() if hmac.compare_digest(m.token_hash, digest)), None)

    def remove_member(self, member_id: str) -> None:
        self.members.pop(member_id, None)
        if self.db:
            self.db.delete("members", self.code, member_id)

    def save_member(self, member: Member) -> None:
        if self.db:
            self.db.put("members", self.code, member.id, member.row())

    @property
    def host_id(self) -> str:
        return next((m.id for m in self.members.values() if m.is_host), "")

    # ---- permissions (always enforced here, never trusted from clients) -----
    def can_draw(self, m: Member) -> bool:
        return m.is_host or self.settings["can_draw"]

    def can_modify(self, m: Member, ann: dict[str, Any]) -> bool:
        return m.is_host or (self.settings["can_draw"] and (ann.get("owner") == m.id or self.settings["can_edit_others"]))

    def can_chat(self, m: Member) -> bool:
        return m.is_host or self.settings["can_chat"]

    def can_timer(self, m: Member) -> bool:
        return m.is_host or self.settings["can_control_timer"]

    # ---- annotations: server arrival order is the single source of truth ----
    def apply_create(self, m: Member, raw: Any) -> Result:
        if not self.can_draw(m):
            return Result(False, "forbidden")
        try:
            ann = validate_annotation(raw)
        except ValidationError:
            return Result(False, "invalid")
        if ann["page"] >= BOARD_BASE:
            if ann["page"] - BOARD_BASE >= len(self.boards):
                return Result(False, "invalid")          # the board must exist first (board_add)
        elif ann["page"] >= max(self.pdf["pages"], 1):
            return Result(False, "invalid")
        existing = self.annotations.get(ann["id"])
        if existing is not None:
            return Result(existing["owner"] == m.id, "exists", ann=existing, dup=True)
        if ann["id"] in self.tombstones:
            return Result(False, "deleted")
        if len(self.annotations) >= MAX_ANNOTATIONS:
            return Result(False, "limit")
        self.rev += 1
        ann.update(owner=m.id, owner_name=m.name, z=self.rev, rev=1)
        self.annotations[ann["id"]] = ann
        if self.db:
            self.db.put("annotations", self.code, ann["id"], ann)
        self.touch()
        return Result(True, ann=ann)

    def apply_update(self, m: Member, ann_id: Any, raw_patch: Any) -> Result:
        ann = self.annotations.get(ann_id) if valid_id(ann_id) else None
        if ann is None:
            return Result(False, "missing")           # delete wins over a concurrent edit
        if not self.can_modify(m, ann):
            return Result(False, "forbidden")
        try:
            patch = validate_patch(ann["kind"], raw_patch)
        except ValidationError:
            return Result(False, "invalid")
        if not patch:
            return Result(False, "invalid")
        if "style" in patch:
            ann["style"].update(patch["style"])
            patch["style"] = dict(ann["style"])
        rest = {k: v for k, v in patch.items() if k != "style"}
        ann.update(rest)
        ann["rev"] = ann.get("rev", 1) + 1            # field-level last-writer-wins by arrival order
        self.rev += 1
        if self.db:
            self.db.put("annotations", self.code, ann["id"], ann)
        self.touch()
        return Result(True, ann=ann, patch=patch)

    def apply_delete(self, m: Member, ann_id: Any) -> Result:
        ann = self.annotations.get(ann_id) if valid_id(ann_id) else None
        if ann is None:
            if valid_id(ann_id):
                self._tombstone(ann_id)               # remember it so late creates/updates are ignored
            return Result(True, dup=True)
        if not self.can_modify(m, ann):
            return Result(False, "forbidden")
        del self.annotations[ann_id]
        self._tombstone(ann_id)
        self.rev += 1
        if self.db:
            self.db.delete("annotations", self.code, ann_id)
        self.touch()
        return Result(True)

    def _tombstone(self, ann_id: str) -> None:
        self.tombstones[ann_id] = None
        while len(self.tombstones) > MAX_TOMBSTONES:
            self.tombstones.pop(next(iter(self.tombstones)))

    def clear_annotations(self) -> None:
        for ann_id in list(self.annotations):
            self._tombstone(ann_id)
        self.annotations.clear()
        self.rev += 1
        if self.db:
            self.db.delete("annotations", self.code)

    def reset_document(self) -> None:
        self.clear_annotations()
        for qid in list(self.questions):
            self.questions.pop(qid)
        if self.db:
            self.db.delete("questions", self.code)
        self.boards.clear()
        self.timer.reset()
        self.page = 0
        for m in self.members.values():
            m.page = 0
        self.save_meta()

    # ---- whiteboards ---------------------------------------------------------
    def add_board(self, m: Member, raw_title: Any) -> dict[str, Any] | None:
        if not self.can_draw(m) or len(self.boards) >= MAX_BOARDS:
            return None
        try:
            title = validate_board_title(raw_title)
        except ValidationError:
            return None
        board = {"id": uuid.uuid4().hex[:10], "title": title, "by": m.name}
        self.boards.append(board)
        self.save_meta()
        self.touch()
        return board

    # ---- questions & chat --------------------------------------------
    def set_question(self, m: Member, raw: Any) -> dict[str, Any] | None:
        if not self.can_draw(m):
            return None
        try:
            q = validate_question(raw, self.pdf["pages"])
        except ValidationError:
            return None
        if q["id"] not in self.questions and len(self.questions) >= 500:
            return None
        self.questions[q["id"]] = q
        if self.db:
            self.db.put("questions", self.code, q["id"], q)
        return q

    def delete_question(self, m: Member, qid: Any) -> bool:
        if not self.can_draw(m) or qid not in self.questions:
            return False
        del self.questions[qid]
        if self.db:
            self.db.delete("questions", self.code, qid)
        return True

    def add_chat(self, m: Member, text: Any) -> dict[str, Any] | None:
        text = clean_text(text, MAX_CHAT)
        if not text or not self.can_chat(m):
            return None
        entry = {"id": uuid.uuid4().hex[:12], "uid": m.id, "name": m.name, "color": m.color,
                 "text": text, "ts": time.time()}
        self.chat.append(entry)
        del self.chat[:-300]
        if self.db:
            self.db.add_chat(self.code, entry)
        return entry

    # ---- snapshot ------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "room": {"code": self.code, "name": self.name, "page": self.page, "settings": self.settings,
                     "locked": self.locked, "host_id": self.host_id, "protected": bool(self.pw_hash)},
            "members": [m.public() for m in self.members.values()],
            "annotations": sorted(self.annotations.values(), key=lambda a: a["z"]),
            "chat": self.chat[-100:],
            "questions": list(self.questions.values()),
            "boards": list(self.boards),
            "timer": self.timer.to_dict(time.time()),
            "pdf": dict(self.pdf),
            "rev": self.rev,
        }
