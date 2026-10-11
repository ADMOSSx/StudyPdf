"""Home screen: hero header, three action cards and a gallery of recent documents with page thumbnails."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QMouseEvent, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QVBoxLayout, QWidget

from client.ui import theme
from client.ui.icons import icon, logo_pixmap


class ActionCard(QFrame):
    clicked = Signal()

    def __init__(self, icon_name: str, title: str, hint: str, primary: bool = False) -> None:
        super().__init__()
        self.primary = primary
        self.icon_name = icon_name
        self.setObjectName("actioncard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(220, 124)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(3)
        badge = QLabel()
        self.badge = badge
        badge.setFixedSize(40, 40)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(icon(icon_name, theme.INK if primary else theme.ACCENT, 22).pixmap(22, 22))
        badge.setStyleSheet(f"background: {'rgba(255,255,255,60)' if primary else theme.BG3}; border-radius: 11px;")
        self.title = QLabel(title)
        self.hint = QLabel(hint)
        self.hint.setWordWrap(True)
        fg, sub = (theme.INK, "rgba(255,255,255,215)") if primary else (theme.TEXT, theme.MUTED)
        self.title.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {fg}; background: transparent;")
        self.hint.setStyleSheet(f"font-size: 12px; color: {sub}; background: transparent;")
        lay.addWidget(badge)
        lay.addSpacing(6)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        self._style(False)

    def refresh_theme(self) -> None:
        fg, sub = (theme.INK, "rgba(255,255,255,215)") if self.primary else (theme.TEXT, theme.MUTED)
        self.badge.setPixmap(icon(self.icon_name, theme.INK if self.primary else theme.ACCENT, 22).pixmap(22, 22))
        self.badge.setStyleSheet(f"background: {'rgba(255,255,255,60)' if self.primary else theme.BG3}; border-radius: 11px;")
        self.title.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {fg}; background: transparent;")
        self.hint.setStyleSheet(f"font-size: 12px; color: {sub}; background: transparent;")
        self._style(False)

    def _style(self, hover: bool) -> None:
        if self.primary:
            bg = theme.GRADIENT_HOVER if hover else theme.GRADIENT
            self.setStyleSheet(f"QFrame#actioncard {{ background: {bg}; border: none; border-radius: 16px; }}")
        else:
            self.setStyleSheet(f"QFrame#actioncard {{ background: {theme.BG3 if hover else theme.BG2}; "
                               f"border: 1px solid {theme.ACCENT if hover else theme.BORDER}; border-radius: 16px; }}")

    def enterEvent(self, e) -> None:
        self._style(True)

    def leaveEvent(self, e) -> None:
        self._style(False)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class HomeScreen(QWidget):
    open_pdf = Signal()
    create_room = Signal()
    join_room = Signal()
    open_recent = Signal(str)
    forget_recent = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        outer = QHBoxLayout(self)
        outer.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(16)
        col.addStretch(1)

        hero = QFrame()
        hero.setObjectName("hero")
        head = QHBoxLayout(hero)
        head.setContentsMargins(28, 24, 28, 24)
        head.setSpacing(18)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(68))
        logo.setFixedSize(68, 68)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("StudyPDF")
        title.setObjectName("h1")
        sub = QLabel("Solve worksheets together, in real time.")
        self.subtitle = sub
        sub.setStyleSheet(f"font-size: 16px; color: {theme.ACCENT_HOVER};")
        tag = QLabel("Draw, highlight and write on the same PDF with your friends, with live cursors, chat and a shared timer.")
        tag.setObjectName("muted")
        tag.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        titles.addWidget(tag)
        head.addWidget(logo)
        head.addLayout(titles, 1)
        col.addWidget(hero)
        col.addSpacing(6)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        for ic, text, hint, sig, primary in (
                ("folder", "Open PDF", "Read and annotate on your own. Works offline.", self.open_pdf, False),
                ("plus", "Create Study Room", "Share a PDF and invite friends with a code.", self.create_room, True),
                ("join", "Join Study Room", "Enter a room code and solve together.", self.join_room, False)):
            c = ActionCard(ic, text, hint, primary)
            c.clicked.connect(sig)
            cards.addWidget(c, 1)
        col.addLayout(cards)
        col.addSpacing(12)

        lab = QLabel("RECENT DOCUMENTS")
        lab.setObjectName("section")
        col.addWidget(lab)
        self.recent = QListWidget()
        self.recent.setViewMode(QListWidget.ViewMode.IconMode)
        self.recent.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.recent.setMovement(QListWidget.Movement.Static)
        self.recent.setIconSize(QSize(112, 150))
        self.recent.setGridSize(QSize(150, 218))
        self.recent.setWordWrap(True)
        self.recent.setSpacing(4)
        self.recent.setMinimumHeight(236)
        self.recent.itemDoubleClicked.connect(lambda it: self.open_recent.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.recent.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent.customContextMenuRequested.connect(self._menu)
        col.addWidget(self.recent, 1)
        self.empty = QLabel("Nothing here yet. Open a PDF, or drop one anywhere in this window.")
        self.empty.setObjectName("muted")
        col.addWidget(self.empty)
        tip = QLabel("Tip: press Ctrl+K anywhere to search every command")
        tip.setObjectName("muted")
        tip.setStyleSheet("font-size: 11px;")
        col.addWidget(tip)
        col.addStretch(1)

        wrap = QFrame()
        wrap.setMinimumWidth(760)
        wrap.setMaximumWidth(960)
        wrap.setLayout(col)
        outer.addWidget(wrap, 6)
        outer.addStretch(1)

    def refresh_theme(self) -> None:
        self.subtitle.setStyleSheet(f"font-size: 16px; color: {theme.ACCENT_HOVER};")
        for card in self.findChildren(ActionCard):
            card.refresh_theme()

    def _menu(self, pos) -> None:
        it = self.recent.itemAt(pos)
        if it:
            m = QMenu(self)
            m.addAction(icon("trash"), "Remove from list", lambda: self.forget_recent.emit(it.data(Qt.ItemDataRole.UserRole)))
            m.exec(self.recent.mapToGlobal(pos))

    def set_recent(self, items: list[dict[str, Any]], thumb_dir: Path) -> None:
        self.recent.clear()
        for r in items:
            exists = os.path.exists(r["path"])
            when = datetime.fromtimestamp(r["opened"]).strftime("%b %d")
            thumb = thumb_dir / f"{r['sha']}.png"
            pm = QPixmap(str(thumb)) if thumb.exists() else QPixmap()
            ic = QIcon(pm.scaled(112, 150, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)) \
                if not pm.isNull() else icon("pages", "#a3abbd", 64)
            name = r["name"] if len(r["name"]) <= 22 else r["name"][:19] + "…"
            it = QListWidgetItem(ic, f"{name}\n{r['pages']} pages · {when}" + ("" if exists else "\n(file missing)"))
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            it.setData(Qt.ItemDataRole.UserRole, r["path"])
            it.setToolTip(r["path"])
            if not exists:
                it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(it)
        self.empty.setVisible(not items)
        self.recent.setVisible(bool(items))
