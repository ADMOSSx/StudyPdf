"""Worksheet question tracker (Solved / In progress / Difficult). Local in offline mode, shared in a room."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from client.annotations.model import new_id

STATUS_ORDER = ("todo", "progress", "solved", "difficult")


class QuestionStore(QObject):
    changed = Signal()
    op = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[str, dict[str, Any]] = {}

    def ordered(self) -> list[dict[str, Any]]:
        return sorted(self.items.values(), key=lambda q: (q["page"], q["label"].lower()))

    def add(self, label: str, page: int) -> None:
        q = {"id": new_id(), "label": label.strip()[:60], "page": page, "status": "todo"}
        if q["label"]:
            self.items[q["id"]] = q
            self.op.emit({"t": "question_set", "question": dict(q)})
            self.changed.emit()

    def set_status(self, qid: str, status: str) -> None:
        q = self.items.get(qid)
        if q and status in STATUS_ORDER:
            q["status"] = status
            self.op.emit({"t": "question_set", "question": dict(q)})
            self.changed.emit()

    def cycle(self, qid: str) -> None:
        q = self.items.get(qid)
        if q:
            self.set_status(qid, STATUS_ORDER[(STATUS_ORDER.index(q["status"]) + 1) % len(STATUS_ORDER)])

    def remove(self, qid: str) -> None:
        if self.items.pop(qid, None) is not None:
            self.op.emit({"t": "question_delete", "id": qid})
            self.changed.emit()

    # remote / bulk changes never re-emit ops
    def apply_remote(self, q: dict[str, Any]) -> None:
        self.items[q["id"]] = q
        self.changed.emit()

    def remove_remote(self, qid: str) -> None:
        self.items.pop(qid, None)
        self.changed.emit()

    def replace_all(self, items: list[dict[str, Any]]) -> None:
        self.items = {q["id"]: q for q in items}
        self.changed.emit()
