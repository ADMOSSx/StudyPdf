"""Side panels: page thumbnails, users, chat, questions and the study timer."""
from __future__ import annotations

import html
import time
from datetime import datetime
from typing import Any

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QStyledItemDelegate, QStyleOptionViewItem,
                               QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from client.collaboration.questions import STATUS_ORDER, QuestionStore
from client.pdf.engine import PdfDocument
from client.ui import theme
from client.ui.icons import icon
from client.ui.toolbar import tbtn
from shared.timer import TimerState

ROLE = Qt.ItemDataRole.UserRole


def section_label(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("section")
    return lab


class PagesPanel(QListWidget):
    page_selected = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.doc: PdfDocument | None = None
        self.setObjectName("pages")
        self.setIconSize(QSize(120, 160))
        self.setSpacing(4)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.currentRowChanged.connect(lambda r: r >= 0 and self.page_selected.emit(r))
        # Render only thumbnails that are visible. Rendering every page on the UI
        # thread makes large books appear frozen even when the user never scrolls.
        self._timer = QTimer(self, interval=70)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render_next)
        self.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnails)
        self._pending: set[int] = set()

    def set_document(self, doc: PdfDocument | None) -> None:
        self.doc = doc
        self.blockSignals(True)
        self.clear()
        self._pending.clear()
        if doc:
            w, h = doc.page_size(0)
            ph = QPixmap(120, max(60, int(120 * h / max(w, 1))))
            ph.fill(QColor("#e3e7f1"))
            placeholder = QIcon(ph)                       # shared by every row: one small pixmap, not one per page
            hint = QSize(136, ph.height() + 30)
            for i in range(doc.page_count):
                it = QListWidgetItem(str(i + 1))
                it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
                it.setIcon(placeholder)
                it.setSizeHint(hint)
                self.addItem(it)
                self._pending.add(i)
            self.setCurrentRow(0)
            self._schedule_visible_thumbnails()
        self.blockSignals(False)

    def set_current(self, page: int) -> None:
        if page != self.currentRow():
            self.blockSignals(True)
            self.setCurrentRow(page)
            self.blockSignals(False)
            self.scrollToItem(self.item(page))

    def _schedule_visible_thumbnails(self, *_args) -> None:
        if self.doc and self._pending and not self._timer.isActive():
            self._timer.start()

    def _render_next(self) -> None:
        if not self.doc or not self._pending:
            return

        top = self.indexAt(QPoint(10, 2)).row()
        if top < 0:
            top = max(self.currentRow(), 0)
        bottom = self.indexAt(QPoint(10, max(2, self.viewport().height() - 2))).row()
        if bottom < top:
            bottom = min(top + 5, self.count() - 1)

        # Never fall back to the first off-screen page: that behavior eventually
        # rendered the entire document and caused UI stalls for large PDFs.
        i = next((row for row in range(top, bottom + 1) if row in self._pending), None)
        if i is None:
            return

        self._pending.discard(i)
        try:
            pm = QPixmap.fromImage(self.doc.thumbnail(i, 120))
        except Exception:
            # Keep the page pending so a later scroll can retry it.
            self._pending.add(i)
            return
        it = self.item(i)
        if it:
            it.setIcon(QIcon(pm))
            it.setSizeHint(QSize(136, pm.height() + 30))

        # Yield to the UI between thumbnails, and continue only while there are
        # still unrendered thumbnails in the visible viewport.
        if any(row in self._pending for row in range(top, bottom + 1)):
            self._timer.start()


class UserDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(100, 44)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        m: dict[str, Any] = index.data(ROLE)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(4, 3, -4, -3)
        online = m["connected"]
        color = QColor(m["color"])
        if not online:
            color.setAlpha(110)
        c = QRectF(r.left() + 2, r.top() + 3, 30, 30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(c)
        p.setPen(QColor("#111827"))
        f = QFont(option.font)
        f.setBold(True)
        p.setFont(f)
        p.drawText(c, int(Qt.AlignmentFlag.AlignCenter), (m["name"][:1] or "?").upper())
        p.setBrush(QColor(theme.GOOD if online else "#b3bacb"))
        p.setPen(QPen(QColor(theme.BG1), 2))
        p.drawEllipse(QRectF(c.right() - 8, c.bottom() - 8, 10, 10))
        name = m["name"] + ("  (You)" if m.get("me") else "")
        p.setPen(QColor(theme.TEXT if online else theme.MUTED))
        f.setBold(False)
        p.setFont(f)
        p.drawText(QRectF(r.left() + 42, r.top() + 2, r.width() - 80, 18), int(Qt.AlignmentFlag.AlignVCenter), name)
        sub = ("Host · " if m["is_host"] else "") + (f"page {m['page'] + 1}" if online else "offline")
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(r.left() + 42, r.top() + 20, r.width() - 50, 16), int(Qt.AlignmentFlag.AlignVCenter), sub)
        p.restore()


class UsersPanel(QWidget):
    kick = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 6)
        lay.setSpacing(6)
        self.header = section_label("People")
        lay.addWidget(self.header)
        lay.setContentsMargins(12, 12, 12, 8)
        self.list = QListWidget()
        self.list.setItemDelegate(UserDelegate(self.list))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list)
        self.me = ""
        self.is_host = False

    def set_members(self, members: dict[str, dict[str, Any]], me: str, is_host: bool) -> None:
        self.me, self.is_host = me, is_host
        self.list.clear()
        ordered = sorted(members.values(), key=lambda m: (not m["connected"], not m["is_host"], m["name"].lower()))
        for m in ordered:
            it = QListWidgetItem()
            it.setData(ROLE, dict(m, me=m["id"] == me))
            self.list.addItem(it)
        online = sum(1 for m in members.values() if m["connected"])
        self.header.setText(f"PEOPLE · {online} ONLINE")

    def _menu(self, pos: QPoint) -> None:
        it = self.list.itemAt(pos)
        if not it or not self.is_host:
            return
        m = it.data(ROLE)
        if m["id"] == self.me:
            return
        menu = QMenu(self)
        menu.addAction(icon("leave"), f"Remove {m['name']} from room", lambda: self.kick.emit(m["id"]))
        menu.exec(self.list.mapToGlobal(pos))


class ChatPanel(QWidget):
    send = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.document().setDefaultStyleSheet(f"p {{ margin: 0 0 10px 0; }}")
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Message the room…")
        self.edit.setMaxLength(1000)
        self.me = ""
        self.edit.returnPressed.connect(self._send)
        btn = tbtn("send", "Send")
        btn.clicked.connect(self._send)
        row.addWidget(self.edit, 1)
        row.addWidget(btn)
        lay.addLayout(row)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        lay.addWidget(self.hint)
        self.set_enabled(False, "Join or create a room to chat.")

    def set_enabled(self, enabled: bool, hint: str = "") -> None:
        self.edit.setEnabled(enabled)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))

    def _send(self) -> None:
        text = self.edit.text().strip()
        if text:
            self.send.emit(text)
            self.edit.clear()

    def clear(self) -> None:
        self.view.clear()

    def load(self, messages: list[dict[str, Any]]) -> None:
        self.view.clear()
        for m in messages:
            self.add(m)

    def add(self, m: dict[str, Any]) -> None:
        # plain-text rendering: everything user-supplied is HTML-escaped
        body = html.escape(m["text"]).replace("\n", "<br>")
        ts = datetime.fromtimestamp(m.get("ts", time.time())).strftime("%H:%M")
        stamp = f'<span style="color:{theme.MUTED}; font-size:10px">{ts}</span>'
        if m.get("uid") == self.me and self.me:
            self.view.append(
                f'<table width="100%" cellspacing="0" cellpadding="3"><tr><td width="22%"></td><td align="right">'
                f'<table cellpadding="8" cellspacing="0" bgcolor="#e4e0ff"><tr><td>{body}</td></tr></table>'
                f'<div align="right">{stamp}</div></td></tr></table>')
        else:
            color = html.escape(m.get("color", "#fff"))
            self.view.append(
                f'<table width="100%" cellspacing="0" cellpadding="3"><tr><td>'
                f'<b style="color:{color}">{html.escape(m["name"])}</b> {stamp}<br>'
                f'<table cellpadding="8" cellspacing="0" bgcolor="{theme.BG3}"><tr><td>{body}</td></tr></table>'
                f'</td><td width="22%"></td></tr></table>')

    def system(self, text: str) -> None:
        self.view.append(f'<p style="color:{theme.MUTED}"><i>{html.escape(text)}</i></p>')


class SegmentBar(QWidget):
    """Thin stacked bar showing solved / in progress / difficult / not started proportions."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(8)
        self.counts: dict[str, int] = {}

    def set_counts(self, counts: dict[str, int]) -> None:
        self.counts = counts
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BG3))
        p.drawRoundedRect(self.rect(), 4, 4)
        total = sum(self.counts.values())
        if not total:
            return
        x = 0.0
        w = self.width()
        for key in ("solved", "progress", "difficult", "todo"):
            seg = w * self.counts.get(key, 0) / total
            if seg > 0:
                p.setBrush(QColor(theme.STATUS_COLORS[key]))
                p.drawRoundedRect(QRectF(x, 0, max(seg - 1.5, 2), self.height()), 3, 3)
                x += seg


class QuestionsPanel(QWidget):
    goto_page = Signal(int)

    def __init__(self, store: QuestionStore) -> None:
        super().__init__()
        self.store = store
        self.current_page = lambda: 0
        self.can_edit = lambda: True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(8)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Add question (e.g. Q1)")
        self.edit.returnPressed.connect(self._add)
        add = tbtn("plus", "Add question on this page")
        add.clicked.connect(self._add)
        row.addWidget(self.edit, 1)
        row.addWidget(add)
        lay.addLayout(row)
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        lay.addWidget(self.summary)
        self.bar = SegmentBar()
        lay.addWidget(self.bar)
        self.list = QListWidget()
        self.list.setSpacing(2)
        self.list.itemDoubleClicked.connect(lambda it: self.goto_page.emit(it.data(ROLE)["page"]))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        tip = QLabel("Click a dot to change status · double-click to jump to the page")
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        store.changed.connect(self.refresh)
        self.refresh()

    def _add(self) -> None:
        if not self.can_edit():
            return
        self.store.add(self.edit.text(), self.current_page())
        self.edit.clear()

    def refresh(self) -> None:
        self.list.clear()
        counts = {s: 0 for s in STATUS_ORDER}
        for q in self.store.ordered():
            counts[q["status"]] += 1
            it = QListWidgetItem()
            it.setSizeHint(QSize(100, 40))
            self.list.addItem(it)
            self.list.setItemWidget(it, self._row(q))
            it.setData(ROLE, q)
        self.bar.set_counts(counts)
        self.summary.setText(f"{counts['solved']} solved · {counts['progress']} in progress · {counts['difficult']} difficult")

    def _row(self, q: dict[str, Any]) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 0, 6, 0)
        dot = QToolButton()
        dot.setFixedSize(26, 26)
        col = theme.STATUS_COLORS[q["status"]]
        dot.setStyleSheet(f"QToolButton {{ background: {col}; border-radius: 13px; border: 3px solid {theme.BG1}; }}"
                          f"QToolButton:hover {{ border-color: {theme.BG3}; }}")
        dot.setToolTip(theme.STATUS_LABELS[q["status"]] + " - click to change")
        dot.setCursor(Qt.CursorShape.PointingHandCursor)
        dot.clicked.connect(lambda _=False, qid=q["id"]: self.can_edit() and self.store.cycle(qid))
        label = QLabel(q["label"])
        page = QLabel(f"p.{q['page'] + 1}")
        page.setObjectName("muted")
        state = QLabel(theme.STATUS_LABELS[q["status"]])
        state.setStyleSheet(f"color: {col};")
        lay.addWidget(dot)
        lay.addWidget(label, 1)
        lay.addWidget(state)
        lay.addWidget(page)
        return w

    def _menu(self, pos: QPoint) -> None:
        it = self.list.itemAt(pos)
        if not it or not self.can_edit():
            return
        q = it.data(ROLE)
        menu = QMenu(self)
        for s in STATUS_ORDER:
            menu.addAction(theme.STATUS_LABELS[s], lambda s=s: self.store.set_status(q["id"], s))
        menu.addSeparator()
        menu.addAction(icon("trash"), "Delete question", lambda: self.store.remove(q["id"]))
        menu.exec(self.list.mapToGlobal(pos))


class Ring(QWidget):
    """Circular countdown: the arc shrinks as time runs out, the time sits in the middle."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(70, 70)
        self.fraction, self.text, self.color = 1.0, "25:00", theme.ACCENT

    def set_value(self, fraction: float, text: str, color: str) -> None:
        self.fraction, self.text, self.color = max(0.0, min(1.0, fraction)), text, color
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        pen = QPen(QColor(theme.BG3), 7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawEllipse(r)
        pen.setColor(QColor(self.color))
        p.setPen(pen)
        if self.fraction > 0:
            p.drawArc(r, 90 * 16, int(-self.fraction * 360 * 16))
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPixelSize(14)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self.text)


class TimerWidget(QFrame):
    action = Signal(str, dict)        # start | pause | reset | config

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self.state = TimerState()
        self.local = True
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        self.ring = Ring()
        lay.addWidget(self.ring)
        side = QVBoxLayout()
        side.setSpacing(6)
        self.phase = QLabel("Focus session")
        self.phase.setObjectName("muted")
        self.phase.setWordWrap(True)
        side.addWidget(self.phase)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.toggle = tbtn("play", "Start", size=22)
        self.reset = tbtn("reset", "Reset", size=20)
        self.mode_btn = tbtn("mode", "Timer mode")
        self.mode_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.mode_btn)
        menu.addAction("Single session (25 min)", lambda: self.action.emit("config", {"mode": "focus", "focus_min": 25, "break_min": 5}))
        menu.addAction("Pomodoro · 25 min study / 5 min break", lambda: self.action.emit("config", {"mode": "pomodoro", "focus_min": 25, "break_min": 5}))
        menu.addAction("Custom…", self._custom)
        self.mode_btn.setMenu(menu)
        self.toggle.clicked.connect(lambda: self.action.emit("pause" if self.state.running else "start", {}))
        self.reset.clicked.connect(lambda: self.action.emit("reset", {}))
        for w in (self.toggle, self.reset, self.mode_btn):
            row.addWidget(w)
        row.addStretch(1)
        side.addLayout(row)
        lay.addLayout(side, 1)
        self._tick = QTimer(self, interval=250)
        self._tick.timeout.connect(self._refresh)
        self._tick.start()
        self._refresh()

    def _custom(self) -> None:
        f, ok = QInputDialog.getInt(self, "Custom timer", "Study minutes:", self.state.focus_min, 1, 180)
        if not ok:
            return
        b, ok = QInputDialog.getInt(self, "Custom timer", "Break minutes (Pomodoro):", self.state.break_min, 1, 60)
        if ok:
            self.action.emit("config", {"mode": "pomodoro", "focus_min": f, "break_min": b})

    def set_state(self, d: dict[str, Any]) -> None:
        self.state = TimerState.from_dict(d, time.time())
        self._refresh()

    def set_enabled_controls(self, enabled: bool) -> None:
        for w in (self.toggle, self.reset, self.mode_btn):
            w.setEnabled(enabled)

    def _refresh(self) -> None:
        now = time.time()
        rem = self.state.now_remaining(now)
        if self.local and self.state.running and rem <= 0:
            self.state.finish(now)
            QApplication.beep()
            rem = self.state.now_remaining(now)
        m, s = divmod(int(rem + 0.999), 60)
        brk = self.state.phase == "break"
        self.ring.set_value(rem / max(self.state.phase_seconds(), 1), f"{m:02d}:{s:02d}", theme.GOOD if brk else theme.ACCENT)
        self.toggle.setIcon(icon("pause" if self.state.running else "play", size=22))
        self.toggle.setToolTip("Pause" if self.state.running else "Start")
        if self.state.mode == "pomodoro":
            self.phase.setText("Break time - relax" if brk else f"Study · Pomodoro {self.state.focus_min}/{self.state.break_min}")
        else:
            self.phase.setText("Focus session")
