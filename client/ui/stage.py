"""Stage: the canvas area with floating controls (tool rail, style bar, page/zoom pill) layered over the PDF."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QVBoxLayout, QWidget

from client.ui.viewer import Viewer

RAIL_SPACE = 74


class Stage(QWidget):
    def __init__(self, viewer: Viewer, rail: QWidget, props: QWidget, viewbar: QWidget) -> None:
        super().__init__()
        self.setObjectName("stagebg")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(viewer)
        viewer.setViewportMargins(RAIL_SPACE, 54, 12, 70)      # keep the page clear of the floating controls
        self.rail, self.props, self.viewbar = rail, props, viewbar
        for w in (rail, props, viewbar):
            shadow = QGraphicsDropShadowEffect(w)           # makes the controls float above the page
            shadow.setBlurRadius(26)
            shadow.setOffset(0, 5)
            shadow.setColor(QColor(24, 40, 38, 70))
            w.setGraphicsEffect(shadow)
            w.setParent(self)
            w.installEventFilter(self)
            w.show()
            w.raise_()
        QTimer.singleShot(0, self._place)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show, QEvent.Type.Hide):
            QTimer.singleShot(0, self._place)
        return False

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place()

    def _place(self) -> None:
        for w in (self.rail, self.props, self.viewbar):
            if w.isVisible():
                w.adjustSize()
        if self.rail.isVisible():
            self.rail.move(14, max(10, (self.height() - self.rail.height()) // 2))
        if self.props.isVisible():
            self.props.move(max(RAIL_SPACE, (self.width() - self.props.width()) // 2), 12)
        if self.viewbar.isVisible():
            self.viewbar.move((self.width() - self.viewbar.width()) // 2, self.height() - self.viewbar.height() - 14)
        for w in (self.rail, self.props, self.viewbar):
            w.raise_()
