"""Command palette (Ctrl+K): one searchable place for every action, replacing the old menu bar."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

ROLE = Qt.ItemDataRole.UserRole


class Command:
    """One entry: a title, the group it belongs to, an optional shortcut hint and the function to run."""

    def __init__(self, title: str, group: str, run: Callable[[], None], shortcut: str = "",
                 enabled: Callable[[], bool] | None = None) -> None:
        self.title, self.group, self.run, self.shortcut, self.enabled = title, group, run, shortcut, enabled

    def available(self) -> bool:
        return self.enabled is None or bool(self.enabled())

    def matches(self, query: str) -> bool:
        hay = f"{self.group} {self.title}".lower()
        return all(tok in hay for tok in query.lower().split())


class CommandPalette(QDialog):
    def __init__(self, parent: QWidget, commands: list[Command]) -> None:
        super().__init__(parent)
        self.setObjectName("palette")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setFixedWidth(560)
        self.commands = [c for c in commands if c.available()]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 8)
        lay.setSpacing(6)
        self.input = QLineEdit()
        self.input.setObjectName("paletteinput")
        self.input.setPlaceholderText("Type a command…  (open, search, whiteboard, room, zoom, pen…)")
        self.input.textChanged.connect(self._filter)
        self.input.installEventFilter(self)
        self.list = QListWidget()
        self.list.setObjectName("palettelist")
        self.list.setFixedHeight(340)
        self.list.itemActivated.connect(lambda _it: self._run())
        self.list.itemClicked.connect(lambda _it: self._run())
        lay.addWidget(self.input)
        lay.addWidget(self.list)
        self._filter("")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        p = self.parentWidget()
        if p is not None:                                   # float near the top-centre of the window
            g = p.geometry()
            self.move(p.mapToGlobal(p.rect().topLeft()).x() + (g.width() - self.width()) // 2,
                      p.mapToGlobal(p.rect().topLeft()).y() + 90)
        self.input.setFocus()

    def _filter(self, text: str) -> None:
        self.list.clear()
        for cmd in self.commands:
            if not cmd.matches(text):
                continue
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 6, 10, 6)
            name = QLabel(cmd.title)
            group = QLabel(cmd.group)
            group.setObjectName("muted")
            h.addWidget(name, 1)
            h.addWidget(group)
            if cmd.shortcut:
                key = QLabel(cmd.shortcut)
                key.setObjectName("muted")
                key.setStyleSheet("font-size: 11px; font-weight: 600;")
                h.addWidget(key)
            item = QListWidgetItem()
            item.setData(ROLE, cmd)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.input and ev.type() == QEvent.Type.KeyPress:
            key = ev.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if key == Qt.Key.Key_Down else -1
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, self.list.currentRow() + step)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run()
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, ev)

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self.reject()                                    # click outside closes it, like a menu

    def _run(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        cmd: Command = item.data(ROLE)
        self.accept()
        QTimer.singleShot(0, cmd.run)                        # run after the palette is gone so dialogs get focus
