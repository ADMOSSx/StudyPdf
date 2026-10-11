"""Local editing layer: applies edits to the store, emits network ops and keeps per-user undo/redo."""
from __future__ import annotations

import copy
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from client.annotations.model import AnnotationStore, new_id


class Editor(QObject):
    op = Signal(dict)               # outgoing operation (annotation_created / _updated / _deleted)
    history_changed = Signal()

    def __init__(self, store: AnnotationStore) -> None:
        super().__init__()
        self.store = store
        self.identity: Callable[[], tuple[str, str]] = lambda: ("local", "You")
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._group: list[dict[str, Any]] | None = None

    # ---- edits ------------------------------------------------------------
    def create(self, ann: dict[str, Any], record: bool = True) -> None:
        uid, name = self.identity()
        ann.setdefault("owner", uid)
        ann.setdefault("owner_name", name)
        self.store.add(ann)
        self.op.emit({"t": "annotation_created", "ann": copy.deepcopy(ann)})
        if record:
            self._push({"k": "delete", "ann": copy.deepcopy(ann)})

    def update(self, ann_id: str, patch: dict[str, Any], record: bool = True, send: bool = True) -> None:
        ann = self.store.anns.get(ann_id)
        if ann is None:
            return
        before = {k: copy.deepcopy(ann.get(k)) for k in patch}
        self.store.update(ann_id, patch)
        if send:
            self.op.emit({"t": "annotation_updated", "id": ann_id, "patch": copy.deepcopy(patch)})
        if record:
            self._push({"k": "update", "id": ann_id, "patch": before})

    def delete(self, ann_id: str, record: bool = True) -> None:
        ann = self.store.anns.get(ann_id)
        if ann is None:
            return
        snap = copy.deepcopy(ann)
        self.store.remove(ann_id)
        self.op.emit({"t": "annotation_deleted", "id": ann_id})
        if record:
            self._push({"k": "create", "ann": snap})

    def record_update(self, ann_id: str, before: dict[str, Any]) -> None:
        """Register an undo step for a drag that was streamed with send=True/record=False."""
        self._push({"k": "update", "id": ann_id, "patch": before})

    # ---- grouping (e.g. one eraser stroke = one undo step) -------------------
    def begin_group(self) -> None:
        self._group = []

    def end_group(self) -> None:
        items, self._group = self._group or [], None
        if len(items) == 1:
            self._commit(items[0])
        elif items:
            self._commit({"k": "group", "items": items})

    def _push(self, entry: dict[str, Any]) -> None:
        if self._group is not None:
            self._group.append(entry)
        else:
            self._commit(entry)

    def _commit(self, entry: dict[str, Any]) -> None:
        self._undo.append(entry)
        del self._undo[:-200]
        self._redo.clear()
        self.history_changed.emit()

    # ---- undo / redo ------------------------------------------------------------
    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> None:
        self._step(self._undo, self._redo)

    def redo(self) -> None:
        self._step(self._redo, self._undo)

    def _step(self, src: list, dst: list) -> None:
        while src:
            inverse = self._apply(src.pop())
            if inverse is not None:
                dst.append(inverse)
                break
        self.history_changed.emit()

    def clear_history(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self.history_changed.emit()

    def _apply(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        k = entry["k"]
        if k == "group":
            inverses = [inv for e in reversed(entry["items"]) if (inv := self._apply(e)) is not None]
            return {"k": "group", "items": inverses} if inverses else None
        if k == "create":              # bring an annotation back (new id: the server remembers deleted ids)
            ann = copy.deepcopy(entry["ann"])
            old, ann["id"] = ann["id"], new_id()
            ann["z"] = 0
            self._remap(old, ann["id"])
            self.create(ann, record=False)
            return {"k": "delete", "ann": copy.deepcopy(ann)}
        if k == "delete":
            ann = self.store.anns.get(entry["ann"]["id"])
            if ann is None:
                return None
            snap = copy.deepcopy(ann)
            self.delete(ann["id"], record=False)
            return {"k": "create", "ann": snap}
        if k == "update":
            cur = self.store.anns.get(entry["id"])
            if cur is None:
                return None
            before = {key: copy.deepcopy(cur.get(key)) for key in entry["patch"]}
            self.update(entry["id"], entry["patch"], record=False)
            return {"k": "update", "id": entry["id"], "patch": before}
        return None

    def _remap(self, old: str, new: str) -> None:
        def fix(e: dict[str, Any]) -> None:
            if e["k"] == "group":
                for i in e["items"]:
                    fix(i)
            elif e["k"] == "update" and e["id"] == old:
                e["id"] = new
            elif e["k"] in ("create", "delete") and e["ann"]["id"] == old:
                e["ann"]["id"] = new
        for stack in (self._undo, self._redo):
            for e in stack:
                fix(e)
