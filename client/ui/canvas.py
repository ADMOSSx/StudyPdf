"""PageCanvas: shows one PDF page plus live annotations, handles every drawing/selection tool."""
from __future__ import annotations

import math
import time
from collections import OrderedDict
from typing import Any, Callable

from PySide6.QtCore import QPointF, QRect, QRectF, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from client.pdf.render_worker import PageRenderTask

from client.annotations.editor import Editor
from client.annotations.model import (POINT_KINDS, AnnotationStore, bounds, hit_test, new_annotation, resized, translated)
from client.annotations.render import (NOTE_PAD, TEXT_PAD, comment_card_rect, make_font, paint_annotation, text_height)
from client.pdf.engine import PdfDocument

MARGIN = 28
SHAPES = ("rect", "ellipse", "arrow", "line")
DRAW_TOOLS = ("pen", "highlighter", "text", "note", "comment", "underline", "strike", "eraser", "step") + SHAPES


class InlineEditor(QPlainTextEdit):
    committed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._done = False
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self._finish(False)
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._finish(True)
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        if e.reason() == Qt.FocusReason.PopupFocusReason:        # e.g. the math-symbols menu: keep editing
            return
        self._finish(True)

    def _finish(self, commit: bool) -> None:
        if self._done:
            return
        self._done = True
        (self.committed.emit(self.toPlainText()) if commit else self.cancelled.emit())


class PageCanvas(QWidget):
    cursor_moved = Signal(int, float, float)
    live_update = Signal(dict)
    denied = Signal(str)
    selection_changed = Signal()
    pan_delta = Signal(float, float)
    snip_requested = Signal(int, float, float, float, float)     # pdf page, x0, y0, x1, y1 (PDF points)

    def __init__(self, store: AnnotationStore, editor: Editor) -> None:
        super().__init__()
        self.store, self.editor = store, editor
        self.doc: PdfDocument | None = None
        self.page, self.zoom, self.tool = 0, 1.0, "select"
        self.page_offset = 0            # whiteboard canvases draw annotations on page BOARD_BASE + board index
        self.board_bg = "grid"          # grid | lines | dots | blank (whiteboard only, local view setting)
        self.selected: str | None = None
        self.hover: str | None = None
        self.can_draw: Callable[[], bool] = lambda: True
        self.can_modify: Callable[[dict], bool] = lambda a: True
        self.style_for: Callable[[str], dict[str, Any]] = lambda t: {"color": "#ff6b6b", "width": 2.2, "size": 16}
        self.identity: Callable[[], tuple[str, str]] = lambda: ("local", "You")
        self.cursors: dict[str, dict[str, Any]] = {}
        self.remote_live: Callable[[int], list[dict]] = lambda page: []
        self._cache: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        self._render_pool = QThreadPool(self)
        self._render_pool.setMaxThreadCount(2)
        self._render_pending: set[tuple] = set()
        self._render_failed: set[tuple] = set()
        # Keep QRunnable wrappers alive until their completion signal is handled.
        self._render_tasks: dict[tuple, PageRenderTask] = {}
        self._doc_generation = 0
        self._drag: dict[str, Any] | None = None
        self._draft: dict[str, Any] | None = None
        self._mouse: QPointF | None = None
        self._editing: dict[str, Any] | None = None
        self._editor_w: InlineEditor | None = None
        self._last_send = 0.0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._live_timer = QTimer(self, interval=60)
        self._live_timer.timeout.connect(self._flush_live)
        self._cursor_timer = QTimer(self, interval=1500)
        self._cursor_timer.timeout.connect(self.update)
        self._cursor_timer.start()
        store.changed.connect(self._on_store_changed)
        store.reset.connect(self._on_reset)
        store.removed.connect(self._on_removed)

    @property
    def ap(self) -> int:
        """Annotation page id for the page currently shown (differs from `page` on whiteboards)."""
        return self.page + self.page_offset

    @property
    def is_board(self) -> bool:
        return bool(getattr(self.doc, "is_board", False))

    # ---- document / view state ----------------------------------------------------------
    def set_document(self, doc: PdfDocument | None) -> None:
        self._commit_editor()
        self._doc_generation += 1
        self.doc, self.page, self.selected = doc, 0, None
        self._cache.clear()
        self._render_pending.clear()
        self._render_failed.clear()
        self._resize()

    def set_page(self, index: int) -> None:
        if self.doc and 0 <= index < self.doc.page_count:
            self._commit_editor()
            self.page, self.selected, self._draft, self._drag = index, None, None, None
            self._resize()

    def set_zoom(self, z: float) -> None:
        self._commit_editor()
        self.zoom = max(0.25, min(4.0, z))
        self._resize()

    def set_tool(self, tool: str) -> None:
        self._commit_editor()
        self.tool, self._draft = tool, None
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor if tool == "hand" else Qt.CursorShape.ArrowCursor))
        if tool != "select":
            self.selected = None
        self._update_cursor(None)
        self.update()

    def _resize(self) -> None:
        if self.doc:
            w, h = self.doc.page_size(self.page)
            self.setFixedSize(int(w * self.zoom) + 2 * MARGIN, int(h * self.zoom) + 2 * MARGIN)
        else:
            self.setFixedSize(10, 10)
        self.update()

    def _on_store_changed(self, page: int) -> None:
        if page == self.ap:
            self.update()

    def _on_reset(self) -> None:
        self.selected, self._drag, self._draft = None, None, None
        self.update()

    def _on_removed(self, ann_id: str) -> None:
        if self.selected == ann_id:
            self.selected = None
            self.selection_changed.emit()
        if self._drag and self._drag.get("id") == ann_id:
            self._drag = None
        if self._editing and self._editing.get("existing") and self._editing["existing"]["id"] == ann_id:
            self._close_editor()

    # ---- coordinates ------------------------------------------------------------------------------
    def _pt(self, pos: QPointF) -> tuple[float, float]:
        return (pos.x() - MARGIN) / self.zoom, (pos.y() - MARGIN) / self.zoom

    def _wpt(self, x: float, y: float) -> QPointF:
        return QPointF(MARGIN + x * self.zoom, MARGIN + y * self.zoom)

    def _page_pixmap(self) -> QPixmap | None:
        """Return a cached raster immediately; schedule missing rasters off the UI thread."""
        assert self.doc
        if self.is_board:
            return None
        dpr = self.devicePixelRatioF()
        w, h = self.doc.page_size(self.page)
        scale = self.zoom * dpr
        if w * h * scale * scale > 10_000_000:
            # Cap one raster to ~10 MP to reduce transient allocations on scanned/oversized pages.
            scale = math.sqrt(10_000_000 / (w * h))
        key = (self.page, round(scale, 3), round(self.zoom, 3))
        pm = self._cache.get(key)
        if pm is not None:
            self._cache.move_to_end(key)
            return pm

        if key in self._render_failed:
            return None
        if key not in self._render_pending:
            self._render_pending.add(key)
            task = PageRenderTask(self.doc.path, self.page, scale, key, self._doc_generation)
            task.signals.finished.connect(self._on_page_rendered)
            self._render_tasks[(self._doc_generation, key)] = task
            self._render_pool.start(task)

        # No full-resolution placeholder is allocated: that would defeat the memory cap.
        # paintEvent draws a plain white page until the raster arrives.
        return None

    def _on_page_rendered(self, key: tuple, generation: int, image, error: str) -> None:
        # A stale task must not clear a newer task with the same page/scale key.
        self._render_tasks.pop((generation, key), None)
        if generation != self._doc_generation or not self.doc:
            return
        self._render_pending.discard(key)
        if not image.isNull():
            pm = QPixmap.fromImage(image)
            # The cache key's scale is physical pixels per PDF point; logical display size
            # remains tied to the current zoom, including the oversized-page pixel cap.
            pm.setDevicePixelRatio(key[1] / max(key[2], 0.01))
            self._cache[key] = pm
            self._cache.move_to_end(key)
            def cache_pixels() -> int:
                return sum(item.width() * item.height() for item in self._cache.values())
            while len(self._cache) > 1 and (len(self._cache) > 3 or cache_pixels() > 8_000_000):
                self._cache.popitem(last=False)
        elif error:
            self._render_failed.add(key)
        if key[0] == self.page:
            self.update()

    # ---- painting ------------------------------------------------------------------------------------
    def paintEvent(self, ev) -> None:
        if not self.doc:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.doc.page_size(self.page)
        pw, ph = w * self.zoom, h * self.zoom
        for k in range(10, 0, -1):                                   # soft layered drop shadow
            p.fillRect(QRectF(MARGIN - k + 1, MARGIN - k + 6, pw + 2 * k, ph + 2 * k), QColor(0, 0, 0, 11))
        page_pixmap = self._page_pixmap()
        if self.is_board:
            self._paint_board_bg(p, pw, ph, ev.rect())
        elif page_pixmap is None:
            p.fillRect(QRectF(MARGIN, MARGIN, pw, ph), QColor("#ffffff"))
            stale = next((pm for k, pm in reversed(self._cache.items()) if k[0] == self.page), None)
            if stale is not None:                       # keep the page visible (slightly soft) while the sharp one renders
                p.drawPixmap(QRectF(MARGIN, MARGIN, pw, ph), stale, QRectF(stale.rect()))
            if any(k[0] == self.page and k[2] == round(self.zoom, 3) for k in self._render_failed):
                p.setPen(QColor("#64748b"))
                p.drawText(QRectF(MARGIN + 20, MARGIN + 20, max(1, pw - 40), max(1, ph - 40)),
                           Qt.AlignmentFlag.AlignCenter, "Unable to render this page")
        else:
            p.drawPixmap(QPointF(MARGIN, MARGIN), page_pixmap)
        p.save()
        p.translate(MARGIN, MARGIN)
        p.scale(self.zoom, self.zoom)
        p.setClipRect(QRectF(0, 0, w, h))
        editing_id = self._editing["existing"]["id"] if self._editing and self._editing.get("existing") else None
        for ann in self.store.page_items(self.ap):
            if ann["id"] == editing_id:
                continue
            paint_annotation(p, ann, expanded=ann["kind"] == "comment" and ann["id"] in (self.selected, self.hover))
        for live in self.remote_live(self.ap):
            paint_annotation(p, {"kind": live["kind"], "pts": live["pts"], "style": live["style"]})
        if self._draft:
            paint_annotation(p, self._draft_ann())
            if self._draft.get("rect"):
                x0, y0, x1, y1 = self._draft["rect"]
                p.setPen(QPen(QColor(109, 94, 252, 210), 1 / self.zoom, Qt.PenStyle.DashLine))
                p.setBrush(QColor(109, 94, 252, 40))
                p.drawRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
        p.restore()
        self._paint_selection(p)
        self._paint_cursors(p)
        if self.tool == "eraser" and self._mouse is not None:
            p.setPen(QPen(QColor(60, 66, 90, 200), 1.2))
            p.setBrush(QColor(109, 94, 252, 40))
            p.drawEllipse(self._mouse, 8, 8)

    def _paint_board_bg(self, p: QPainter, pw: float, ph: float, exposed: QRect) -> None:
        page = QRectF(MARGIN, MARGIN, pw, ph)
        p.fillRect(page, QColor("#ffffff"))
        if self.board_bg == "blank":
            return
        step = 40.0 * self.zoom
        if step < 8:
            return
        p.save()
        p.setClipRect(page.intersected(QRectF(exposed)))
        if self.board_bg == "dots":
            p.setPen(QPen(QColor("#c4cdd0"), max(1.6, 2.2 * self.zoom), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            x = MARGIN + step
            while x < MARGIN + pw:
                y = MARGIN + step
                while y < MARGIN + ph:
                    p.drawPoint(QPointF(x, y))
                    y += step
                x += step
        else:
            p.setPen(QPen(QColor("#e4eaec"), 1))
            y = MARGIN + step
            while y < MARGIN + ph:
                p.drawLine(QPointF(MARGIN, y), QPointF(MARGIN + pw, y))
                y += step
            if self.board_bg == "grid":
                x = MARGIN + step
                while x < MARGIN + pw:
                    p.drawLine(QPointF(x, MARGIN), QPointF(x, MARGIN + ph))
                    x += step
        p.restore()

    def _draft_ann(self) -> dict[str, Any]:
        d = self._draft
        assert d
        return {"kind": d["kind"], "pts": d["pts"], "style": d["style"]}

    def _handles(self, ann: dict[str, Any]) -> dict[str, QPointF]:
        x0, y0, x1, y1 = bounds(ann)
        return {"nw": self._wpt(x0, y0), "ne": self._wpt(x1, y0), "sw": self._wpt(x0, y1), "se": self._wpt(x1, y1)}

    def _paint_selection(self, p: QPainter) -> None:
        ann = self.store.anns.get(self.selected or "")
        if not ann or ann["page"] != self.ap:
            return
        x0, y0, x1, y1 = bounds(ann)
        r = QRectF(self._wpt(x0, y0), self._wpt(x1, y1)).adjusted(-4, -4, 4, 4)
        p.setPen(QPen(QColor("#6d5efc"), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        if ann["kind"] not in POINT_KINDS and self.can_modify(ann):
            p.setPen(QPen(QColor("#6d5efc"), 1.5))
            p.setBrush(QColor("#ffffff"))
            for pt in self._handles(ann).values():
                p.drawRect(QRectF(pt.x() - 4.5, pt.y() - 4.5, 9, 9))

    def _paint_cursors(self, p: QPainter) -> None:
        now = time.monotonic()
        arrow = [(0, 0), (0, 15), (4, 11.5), (7, 18), (9.5, 17), (6.5, 10.8), (11.5, 10.8)]
        font = QFont(self.font())
        font.setPixelSize(11)
        fm = QFontMetricsF(font)
        for c in self.cursors.values():
            if c["page"] != self.ap or now - c["t"] > 8:
                continue
            at = self._wpt(c["x"], c["y"])
            color = QColor(c["color"])
            p.setPen(QPen(QColor("#ffffff"), 1.2))
            p.setBrush(color)
            p.drawPolygon(QPolygonF([QPointF(at.x() + a, at.y() + b) for a, b in arrow]))
            tw = fm.horizontalAdvance(c["name"]) + 12
            tag = QRectF(at.x() + 12, at.y() + 16, tw, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(tag, 9, 9)
            p.setFont(font)
            p.setPen(QColor("#111827"))
            p.drawText(tag, int(Qt.AlignmentFlag.AlignCenter), c["name"])

    # ---- mouse -------------------------------------------------------------------------------------------
    def _hit(self, x: float, y: float) -> dict[str, Any] | None:
        tol = 6 / self.zoom
        for ann in reversed(self.store.page_items(self.ap)):
            if hit_test(ann, x, y, tol):
                return ann
            if ann["kind"] == "comment" and ann["id"] in (self.selected, self.hover) \
                    and comment_card_rect(ann).contains(QPointF(x, y)):
                return ann
        return None

    def _handle_at(self, ann: dict[str, Any], pos: QPointF) -> str | None:
        for name, pt in self._handles(ann).items():
            if abs(pt.x() - pos.x()) <= 7 and abs(pt.y() - pos.y()) <= 7:
                return name
        return None

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self.doc:
            return
        self.setFocus()
        self._commit_editor()
        x, y = self._pt(e.position())
        t = self.tool
        if t == "hand":
            self._drag = {"mode": "pan", "last": e.position()}
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        if t == "select":
            return self._press_select(e.position(), x, y)
        if t == "snip":
            self._draft = {"kind": "rect", "pts": [x, y, x, y], "style": {"color": "#176b66", "width": 1.8, "opacity": 1.0},
                           "start": (x, y), "snip": True}
            return
        if not self.can_draw():
            self.denied.emit("The host made this room view-only for you.")
            return
        st = self.style_for(t)
        uid, name = self.identity()
        if t in ("pen", "highlighter"):
            self._draft = {"kind": t, "pts": [x, y], "style": st, "id": self._new_draft_id(), "sent": 0}
            self._live_timer.start()
        elif t in SHAPES:
            self._draft = {"kind": t, "pts": [x, y, x, y], "style": st, "start": (x, y)}
        elif t in ("underline", "strike"):
            self._draft = {"kind": t, "pts": [x, y, x, y], "style": st, "start": (x, y), "rect": (x, y, x, y)}
            self._draft["kind"] = "line"
        elif t in ("text", "note", "comment"):
            hit = self._hit(x, y)
            if hit and hit["kind"] == t and self.can_modify(hit):
                self._open_editor(hit)
            else:
                self._open_editor(None, t, x, y)
        elif t == "step":
            self._place_step(x, y)
        elif t == "eraser":
            self.editor.begin_group()
            self._drag = {"mode": "erase"}
            self._erase(x, y)

    def _place_step(self, x: float, y: float) -> None:
        """Drop the next numbered marker (1, 2, 3...) for explaining a solution step by step."""
        nums = [int(a["text"]) for a in self.store.page_items(self.ap) if a["kind"] == "step" and str(a.get("text", "")).isdigit()]
        uid, name = self.identity()
        ann = new_annotation("step", self.ap, [x - 15, y - 15], self.style_for("step"), uid, name, str(max(nums, default=0) + 1))
        self.editor.create(ann)
        self.selected = None

    def insert_text(self, text: str) -> None:
        """Insert a symbol into the text box currently being edited."""
        if self._editor_w:
            self._editor_w.insertPlainText(text)
            self._editor_w.setFocus()

    def _new_draft_id(self) -> str:
        from client.annotations.model import new_id
        return new_id()

    def _press_select(self, wpos: QPointF, x: float, y: float) -> None:
        sel = self.store.anns.get(self.selected or "")
        if sel and sel["page"] == self.ap and sel["kind"] not in POINT_KINDS and self.can_modify(sel):
            h = self._handle_at(sel, wpos)
            if h:
                self._drag = {"mode": "resize", "id": sel["id"], "handle": h, "orig": list(sel["pts"]),
                              "bounds": bounds(sel), "before": {"pts": list(sel["pts"])}}
                return
        ann = self._hit(x, y)
        self.selected = ann["id"] if ann else None
        self.selection_changed.emit()
        if ann and self.can_modify(ann):
            self._drag = {"mode": "move", "id": ann["id"], "start": (x, y), "orig": list(ann["pts"]),
                          "before": {"pts": list(ann["pts"])}, "moved": False}
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if not self.doc:
            return
        self._mouse = e.position()
        x, y = self._pt(e.position())
        self.cursor_moved.emit(self.ap, x, y)
        d, t = self._drag, self.tool
        if e.buttons() & Qt.MouseButton.LeftButton:
            if d and d["mode"] == "pan":
                last = d["last"]
                self.pan_delta.emit(e.position().x() - last.x(), e.position().y() - last.y())
                d["last"] = e.position()
            elif d and d["mode"] == "move":
                dx, dy = x - d["start"][0], y - d["start"][1]
                if d["moved"] or math.hypot(dx, dy) * self.zoom > 3:
                    d["moved"] = True
                    self._drag_apply(d["id"], translated(d["orig"], dx, dy))
            elif d and d["mode"] == "resize":
                self._drag_apply(d["id"], resized(d["orig"], d["bounds"], self._resize_bounds(d, x, y)))
            elif d and d["mode"] == "erase":
                self._erase(x, y)
            elif self._draft:
                self._extend_draft(x, y)
        else:
            self._hover_update(x, y)
            self._update_cursor(e.position())
        if t == "eraser":
            self.update()

    def _resize_bounds(self, d: dict[str, Any], x: float, y: float) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = d["bounds"]
        h = d["handle"]
        if "w" in h:
            x0 = min(x, x1 - 8)
        else:
            x1 = max(x, x0 + 8)
        if "n" in h:
            y0 = min(y, y1 - 8)
        else:
            y1 = max(y, y0 + 8)
        return x0, y0, x1, y1

    def _drag_apply(self, ann_id: str, pts: list[float]) -> None:
        """Stream the geometry to everybody while dragging (throttled), commit history on release."""
        now = time.monotonic()
        send = now - self._last_send > 0.045
        if send:
            self._last_send = now
        self.editor.update(ann_id, {"pts": pts}, record=False, send=send)

    def _extend_draft(self, x: float, y: float) -> None:
        d = self._draft
        assert d
        if d["kind"] in ("pen", "highlighter"):
            lx, ly = d["pts"][-2], d["pts"][-1]
            if math.hypot(x - lx, y - ly) >= max(0.5, 1.0 / self.zoom):
                d["pts"] += [x, y]
                m = 12 / self.zoom
                self.update(self._rect_for(min(lx, x) - m, min(ly, y) - m, max(lx, x) + m, max(ly, y) + m))
        else:
            sx, sy = d["start"]
            d["pts"] = [sx, sy, x, y]
            if "rect" in d:
                d["rect"] = (sx, sy, x, y)
            self.update()

    def _rect_for(self, x0: float, y0: float, x1: float, y1: float) -> QRect:
        a, b = self._wpt(x0, y0), self._wpt(x1, y1)
        return QRectF(a, b).normalized().toAlignedRect().adjusted(-2, -2, 2, 2)

    def _flush_live(self) -> None:
        d = self._draft
        if not d or d["kind"] not in ("pen", "highlighter"):
            self._live_timer.stop()
            return
        new = d["pts"][d["sent"]:]
        if new:
            d["sent"] = len(d["pts"])
            self.live_update.emit({"id": d["id"], "kind": d["kind"], "page": self.ap, "style": d["style"],
                                   "append": [round(v, 1) for v in new[:600]]})
            if len(new) > 600:
                d["sent"] -= len(new) - 600

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton or not self.doc:
            return
        x, y = self._pt(e.position())
        d, draft = self._drag, self._draft
        self._drag = self._draft = None
        if self.tool == "hand":
            self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        self._live_timer.stop()
        if d:
            if d["mode"] in ("move", "resize"):
                ann = self.store.anns.get(d["id"])
                if ann and ann["pts"] != d["orig"]:
                    self.editor.update(d["id"], {"pts": list(ann["pts"])}, record=False)   # final state, always sent
                    self.editor.record_update(d["id"], d["before"])
            elif d["mode"] == "erase":
                self.editor.end_group()
        elif draft:
            self._finish_draft(draft)
        self.update()

    def _finish_draft(self, d: dict[str, Any]) -> None:
        uid, name = self.identity()
        kind, pts, style = d["kind"], d["pts"], d["style"]
        if d.get("snip"):
            if abs(pts[2] - pts[0]) * self.zoom > 12 and abs(pts[3] - pts[1]) * self.zoom > 12:
                self.snip_requested.emit(self.page, min(pts[0], pts[2]), min(pts[1], pts[3]), max(pts[0], pts[2]), max(pts[1], pts[3]))
            return
        if "rect" in d:                                    # underline / strike: snap to words under the drag box
            segs = self._snap_words(d["rect"], self.tool)
            if not segs:
                return
            ann = new_annotation(self.tool, self.ap, segs, style, uid, name)
        elif kind in ("pen", "highlighter"):
            ann = new_annotation(kind, self.ap, pts, style, uid, name)
            ann["id"] = d["id"]
        else:
            if math.hypot(pts[2] - pts[0], pts[3] - pts[1]) * self.zoom < 4:
                return
            ann = new_annotation(kind, self.ap, pts, style, uid, name)
        self.editor.create(ann)
        self.selected = None

    def _snap_words(self, rect: tuple[float, float, float, float], kind: str) -> list[float]:
        assert self.doc
        x0, y0, x1, y1 = rect
        r = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        hits = [w for w in self.doc.words(self.page)
                if w[2] >= r[0] and w[0] <= r[2] and w[3] >= r[1] and w[1] <= r[3]]
        if not hits:
            if abs(x1 - x0) * self.zoom < 8:
                return []
            yy = y1 if kind == "underline" else y1
            return [min(x0, x1), yy, max(x0, x1), yy]
        hits.sort(key=lambda w: (round((w[1] + w[3]) / 2 / 4), w[0]))
        lines: list[list[float]] = []
        for w in hits:
            y = w[3] - 1.2 if kind == "underline" else (w[1] + w[3]) / 2
            if lines and abs(lines[-1][1] - y) < 3 and w[0] - lines[-1][2] < 10:
                lines[-1][2] = max(lines[-1][2], w[2])
            else:
                lines.append([w[0], y, w[2], y])
        return [round(v, 2) for ln in lines[:200] for v in ln]

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if self.tool != "select" or not self.doc:
            return
        x, y = self._pt(e.position())
        ann = self._hit(x, y)
        if ann and ann["kind"] in ("text", "note", "comment") and self.can_modify(ann):
            self._drag = None
            self._open_editor(ann)

    def leaveEvent(self, e) -> None:
        self._mouse = None
        self.update()

    def _hover_update(self, x: float, y: float) -> None:
        ann = self._hit(x, y) if self.tool in ("select", "comment") else None
        hid = ann["id"] if ann and ann["kind"] == "comment" else None
        if hid != self.hover:
            self.hover = hid
            self.update()

    def _update_cursor(self, wpos: QPointF | None) -> None:
        t = self.tool
        if t == "hand":
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor if self._drag and self._drag.get("mode") == "pan" else Qt.CursorShape.OpenHandCursor))
            return
        if t == "select":
            shape = Qt.CursorShape.ArrowCursor
            sel = self.store.anns.get(self.selected or "")
            if wpos is not None and sel and sel["page"] == self.ap and sel["kind"] not in POINT_KINDS:
                h = self._handle_at(sel, wpos)
                if h:
                    shape = Qt.CursorShape.SizeFDiagCursor if h in ("nw", "se") else Qt.CursorShape.SizeBDiagCursor
        elif t == "text":
            shape = Qt.CursorShape.IBeamCursor
        else:
            shape = Qt.CursorShape.CrossCursor
        self.setCursor(QCursor(shape))

    def _erase(self, x: float, y: float) -> None:
        tol = 8 / self.zoom
        for ann in list(reversed(self.store.page_items(self.ap))):
            if ann["kind"] == "image":                      # question snips are removed deliberately (select + Delete)
                continue
            if self.can_modify(ann) and hit_test(ann, x, y, tol):
                self.editor.delete(ann["id"])

    # ---- keyboard --------------------------------------------------------------------------------------------
    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected:
            ann = self.store.anns.get(self.selected)
            if ann and self.can_modify(ann):
                self.editor.delete(self.selected)
        elif e.key() == Qt.Key.Key_Escape:
            self.selected, self._draft = None, None
            self.selection_changed.emit()
            self.update()
        else:
            super().keyPressEvent(e)

    # ---- inline text editing ----------------------------------------------------------------------------------
    def _open_editor(self, existing: dict[str, Any] | None, kind: str = "text", x: float = 0, y: float = 0) -> None:
        self._commit_editor()
        st = dict(existing["style"]) if existing else self.style_for(kind)
        size = float(st.get("size", 16 if kind == "text" else 14))
        if existing:
            kind = existing["kind"]
            x0, y0, x1, y1 = bounds(existing)
            if kind == "comment":
                x0, y0, x1, y1 = x0, y0 + 22, x0 + 200, y0 + 22 + 90
        elif kind == "text":
            x0, y0, x1, y1 = x, y, x + 240, y + size * 2.2
        elif kind == "note":
            x0, y0, x1, y1 = x, y, x + 180, y + 120
        else:
            x0, y0, x1, y1 = x, y + 22, x + 200, y + 22 + 90
        ed = InlineEditor(self)
        ed.setGeometry(QRectF(self._wpt(x0, y0), self._wpt(x1, y1)).toRect())
        f = make_font(size if kind != "comment" else 12)
        f.setPixelSize(max(6, int(round(f.pixelSize() * self.zoom))))
        ed.setFont(f)
        dark = kind in ("text", "comment")
        bg = "rgba(255,255,255,245)" if dark else "rgba(253,230,138,245)"
        fg = st.get("color", "#111827") if kind == "text" else "#1f2937"
        ed.setStyleSheet(f"QPlainTextEdit {{ background: {bg}; color: {fg}; border: 1.5px solid #6d5efc; border-radius: 4px;"
                         f" padding: 3px; }}")
        ed.setPlaceholderText("Type here…  (Ctrl+Enter to save, Esc to cancel)")
        if existing:
            ed.setPlainText(existing.get("text", ""))
            ed.selectAll()
        self._editing = {"existing": existing, "kind": kind, "rect": (x0, y0, x1, y1), "style": st, "at": (x, y)}
        self._editor_w = ed
        ed.committed.connect(self._on_editor_commit)
        ed.cancelled.connect(self._close_editor)
        ed.show()
        ed.setFocus()
        self.update()

    def _commit_editor(self) -> None:
        if self._editor_w:
            self._editor_w.clearFocus()

    def _close_editor(self) -> None:
        w, self._editor_w, self._editing = self._editor_w, None, None
        if w:
            w.hide()
            w.deleteLater()
        self.update()
        self.setFocus()

    def _on_editor_commit(self, text: str) -> None:
        info = self._editing
        if not info:
            return
        text = text.strip()
        existing, kind = info["existing"], info["kind"]
        x0, y0, x1, y1 = info["rect"]
        uid, name = self.identity()
        if existing and not text:
            self.editor.delete(existing["id"])
        elif existing:
            if text != existing.get("text", ""):
                patch: dict[str, Any] = {"text": text}
                if kind in ("text", "note"):
                    size = float(existing["style"].get("size", 16))
                    need = text_height(text, x1 - x0, size, TEXT_PAD if kind == "text" else NOTE_PAD)
                    if need > y1 - y0:
                        patch["pts"] = [x0, y0, x1, y0 + need]
                self.editor.update(existing["id"], patch)
        elif text:
            if kind == "comment":
                ann = new_annotation("comment", self.ap, [info["at"][0], info["at"][1]], info["style"], uid, name, text)
            else:
                size = float(info["style"].get("size", 16))
                need = max(y1 - y0, text_height(text, x1 - x0, size, TEXT_PAD if kind == "text" else NOTE_PAD))
                ann = new_annotation(kind, self.ap, [x0, y0, x1, y0 + need], info["style"], uid, name, text)
            self.editor.create(ann)
            self.selected = ann["id"]
        self._close_editor()
        self.selection_changed.emit()
