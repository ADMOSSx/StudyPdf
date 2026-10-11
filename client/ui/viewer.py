"""Scrollable page viewer with zoom, fit modes and wheel-to-flip at page edges."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QFrame, QScrollArea

from client.ui.canvas import MARGIN, PageCanvas

ZOOMS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]


class Viewer(QScrollArea):
    zoom_changed = Signal(float)
    flip = Signal(int)

    def __init__(self, canvas: PageCanvas) -> None:
        super().__init__()
        self.canvas = canvas
        self.setWidget(canvas)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.viewport().setStyleSheet("background: transparent;")
        self._last_flip = 0.0
        self.canvas.pan_delta.connect(self._pan_by)

    def _pan_by(self, dx: float, dy: float) -> None:
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(dx))
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(dy))

    def set_zoom(self, z: float) -> None:
        sb_v, sb_h = self.verticalScrollBar(), self.horizontalScrollBar()
        fy = (sb_v.value() + self.viewport().height() / 2) / max(self.canvas.height(), 1)
        fx = (sb_h.value() + self.viewport().width() / 2) / max(self.canvas.width(), 1)
        self.canvas.set_zoom(z)
        sb_v.setValue(int(fy * self.canvas.height() - self.viewport().height() / 2))
        sb_h.setValue(int(fx * self.canvas.width() - self.viewport().width() / 2))
        self.zoom_changed.emit(self.canvas.zoom)

    def zoom_step(self, direction: int) -> None:
        z = self.canvas.zoom
        if direction > 0:
            nxt = next((v for v in ZOOMS if v > z + 0.005), ZOOMS[-1])
        else:
            nxt = next((v for v in reversed(ZOOMS) if v < z - 0.005), ZOOMS[0])
        self.set_zoom(nxt)

    def fit_width(self) -> None:
        if self.canvas.doc:
            w, _ = self.canvas.doc.page_size(self.canvas.page)
            self.set_zoom((self.viewport().width() - 2 * MARGIN - 4) / w)

    def fit_page(self) -> None:
        if self.canvas.doc:
            w, h = self.canvas.doc.page_size(self.canvas.page)
            self.set_zoom(min((self.viewport().width() - 2 * MARGIN - 4) / w, (self.viewport().height() - 2 * MARGIN - 4) / h))

    def scroll_to(self, where: str) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum() if where == "bottom" else 0)

    def wheelEvent(self, e: QWheelEvent) -> None:
        dy = e.angleDelta().y()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_step(1 if dy > 0 else -1)
            e.accept()
            return
        sb = self.verticalScrollBar()
        now = time.monotonic()
        if dy < 0 and sb.value() >= sb.maximum() and now - self._last_flip > 0.4:
            self._last_flip = now
            self.flip.emit(1)
        elif dy > 0 and sb.value() <= sb.minimum() and now - self._last_flip > 0.4:
            self._last_flip = now
            self.flip.emit(-1)
        else:
            super().wheelEvent(e)
