"""Annotation data model: plain dicts (JSON-ready) + geometry helpers + the observable store.

Annotation: {id, kind, page, pts:[x0,y0,x1,y1,...], style:{color,width,size}, text?, owner, owner_name, z, rev}
All coordinates are PDF points, origin top-left, so zooming never moves anything.
"""
from __future__ import annotations

import copy
import math
import uuid
from typing import Any

from PySide6.QtCore import QObject, Signal

COMMENT_W, COMMENT_H = 22.0, 20.0
STEP_SIZE = 30.0
TEXT_KINDS = ("text", "note", "comment", "step")
POINT_KINDS = ("comment", "step")          # anchored at one point, fixed size, not resizable
DEFAULT_STYLES = {
    "pen": {"color": "#ff6b6b", "width": 2.2, "opacity": 1.0}, "highlighter": {"color": "#ffe066", "width": 14.0, "opacity": 0.34},
    "shape": {"color": "#4dabf7", "width": 2.4, "opacity": 1.0}, "text": {"color": "#111827", "width": 1.0, "opacity": 1.0},
}


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def new_annotation(kind: str, page: int, pts: list[float], style: dict[str, Any], owner: str, owner_name: str,
                   text: str = "", data: str = "") -> dict[str, Any]:
    ann: dict[str, Any] = {"id": new_id(), "kind": kind, "page": page, "pts": [round(float(v), 2) for v in pts],
                           "style": dict(style), "owner": owner, "owner_name": owner_name, "z": 0, "rev": 1}
    if kind in TEXT_KINDS:
        ann["text"] = text
    if kind == "image":
        ann["data"] = data
    return ann


def bounds(ann: dict[str, Any]) -> tuple[float, float, float, float]:
    p = ann["pts"]
    if ann["kind"] == "comment":
        return p[0], p[1], p[0] + COMMENT_W, p[1] + COMMENT_H
    if ann["kind"] == "step":
        return p[0], p[1], p[0] + STEP_SIZE, p[1] + STEP_SIZE
    xs, ys = p[0::2], p[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def translated(pts: list[float], dx: float, dy: float) -> list[float]:
    return [round(v + (dx if i % 2 == 0 else dy), 2) for i, v in enumerate(pts)]


def resized(pts: list[float], old: tuple[float, float, float, float], new: tuple[float, float, float, float]) -> list[float]:
    ox0, oy0, ox1, oy1 = old
    nx0, ny0, nx1, ny1 = new
    sx = (nx1 - nx0) / max(ox1 - ox0, 1.0)
    sy = (ny1 - ny0) / max(oy1 - oy0, 1.0)
    return [round(nx0 + (v - ox0) * sx, 2) if i % 2 == 0 else round(ny0 + (v - oy0) * sy, 2) for i, v in enumerate(pts)]


def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _poly_dist(px: float, py: float, pts: list[float], closed: bool = False) -> float:
    n = len(pts) // 2
    if n == 1:
        return math.hypot(px - pts[0], py - pts[1])
    best = 1e18
    for i in range(n - (0 if closed else 1)):
        j = (i + 1) % n
        best = min(best, _seg_dist(px, py, pts[2 * i], pts[2 * i + 1], pts[2 * j], pts[2 * j + 1]))
    return best


def ellipse_points(x0: float, y0: float, x1: float, y1: float, n: int = 48) -> list[float]:
    cx, cy, a, b = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2
    out: list[float] = []
    for i in range(n):
        t = 2 * math.pi * i / n
        out += [cx + a * math.cos(t), cy + b * math.sin(t)]
    return out


def hit_test(ann: dict[str, Any], x: float, y: float, tol: float) -> bool:
    k, p = ann["kind"], ann["pts"]
    slack = tol + float(ann.get("style", {}).get("width", 2)) / 2
    if k in ("pen", "highlighter", "line", "arrow"):
        return _poly_dist(x, y, p) <= slack
    if k in ("underline", "strike"):
        return any(_seg_dist(x, y, *p[i:i + 4]) <= slack for i in range(0, len(p), 4))
    if k == "rect":
        x0, y0, x1, y1 = bounds(ann)
        return _poly_dist(x, y, [x0, y0, x1, y0, x1, y1, x0, y1], closed=True) <= slack
    if k == "ellipse":
        return _poly_dist(x, y, ellipse_points(*bounds(ann)), closed=True) <= slack
    x0, y0, x1, y1 = bounds(ann)
    return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol


class AnnotationStore(QObject):
    """Observable annotation collection. UI repaints only the page that changed."""
    changed = Signal(int)
    removed = Signal(str)
    reset = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.anns: dict[str, dict[str, Any]] = {}
        self._cache: dict[int, list[dict[str, Any]]] = {}
        self._z = 0

    def next_z(self) -> int:
        self._z += 1
        return self._z

    def page_items(self, page: int) -> list[dict[str, Any]]:
        if page not in self._cache:
            self._cache[page] = sorted((a for a in self.anns.values() if a["page"] == page), key=lambda a: a.get("z", 0))
        return self._cache[page]

    def all(self) -> list[dict[str, Any]]:
        return sorted(self.anns.values(), key=lambda a: a.get("z", 0))

    def add(self, ann: dict[str, Any]) -> None:
        if not ann.get("z"):
            ann["z"] = self.next_z()
        self._z = max(self._z, int(ann["z"]))
        self.anns[ann["id"]] = ann
        self._cache.pop(ann["page"], None)
        self.changed.emit(ann["page"])

    def update(self, ann_id: str, patch: dict[str, Any]) -> bool:
        ann = self.anns.get(ann_id)
        if ann is None:
            return False
        for k, v in patch.items():
            if k == "style":
                ann["style"].update(v)
            else:
                ann[k] = copy.deepcopy(v)
        self._cache.pop(ann["page"], None)
        self.changed.emit(ann["page"])
        return True

    def set_z(self, ann_id: str, z: int) -> None:
        ann = self.anns.get(ann_id)
        if ann:
            ann["z"] = z
            self._z = max(self._z, z)
            self._cache.pop(ann["page"], None)

    def remove(self, ann_id: str) -> bool:
        ann = self.anns.pop(ann_id, None)
        if ann is None:
            return False
        self._cache.pop(ann["page"], None)
        self.removed.emit(ann_id)
        self.changed.emit(ann["page"])
        return True

    def replace_all(self, items: list[dict[str, Any]]) -> None:
        self.anns = {a["id"]: a for a in items}
        self._cache.clear()
        self._z = max([int(a.get("z", 0)) for a in items] + [self._z if not items else 0])
        self.reset.emit()

    def clear(self) -> None:
        self.anns.clear()
        self._cache.clear()
        self.reset.emit()
