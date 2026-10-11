"""Whiteboard pages ("boards"). Local when offline, server-ordered in a room so everybody agrees on board indexes."""
from __future__ import annotations

import uuid
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from shared.protocol import MAX_BOARDS


class BoardStore(QObject):
    changed = Signal()
    op = Signal(dict)                      # outgoing board_add (only emitted in a room)

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, Any]] = []
        self._pending: dict[str, Callable[[int], None] | None] = {}

    def full(self) -> bool:
        return len(self.items) >= MAX_BOARDS

    def add(self, title: str, online: bool, then: Callable[[int], None] | None = None) -> None:
        """Create a board. In a room the server decides its index, so `then` runs when the server confirms."""
        title = " ".join(title.split())[:40] or f"Board {len(self.items) + 1}"
        if self.full():
            return
        if online:
            cid = uuid.uuid4().hex[:12]
            self._pending[cid] = then
            self.op.emit({"t": "board_add", "title": title, "cid": cid})
        else:
            self.items.append({"id": uuid.uuid4().hex[:10], "title": title})
            self.changed.emit()
            if then:
                then(len(self.items) - 1)

    def apply_remote(self, board: dict[str, Any], index: int, cid: str = "") -> None:
        if not any(b["id"] == board["id"] for b in self.items):
            if index >= len(self.items):
                self.items.append(board)
            else:                                       # we missed an earlier board: put it where the server says
                self.items.insert(index, board)
            self.changed.emit()
        then = self._pending.pop(cid, None) if cid else None
        if then:
            then(next(i for i, b in enumerate(self.items) if b["id"] == board["id"]))

    def replace_all(self, items: list[dict[str, Any]]) -> None:
        self.items = [dict(b) for b in items][:MAX_BOARDS]
        self.changed.emit()
