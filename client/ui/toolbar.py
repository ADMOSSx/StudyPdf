"""Bottom tool bar, top view bar and top app bar."""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QSlider, QToolButton, QVBoxLayout, QWidget)

from client.annotations.model import DEFAULT_STYLES
from client.ui import theme
from client.ui.icons import icon, logo_pixmap

TOOLS = [("select", "Select (V)"), ("hand", "Hand / pan (G)"), ("pen", "Pen (P)"), ("highlighter", "Highlighter (H)"), ("text", "Text box (T)"),
         ("shape", "Shapes (R)"), ("underline", "Underline (U)"), ("strike", "Strike-through (S)"),
         ("note", "Sticky note (N)"), ("comment", "Comment (C)"), ("step", "Step marker (M): number your solution steps"),
         ("eraser", "Eraser (E)"), ("snip", "Snip a question to the whiteboard (Q)")]
MARK_NAMES = {"underline": "Underline  (U)", "strike": "Strike-through  (S)", "note": "Sticky note  (N)", "comment": "Comment  (C)"}


def rail_tools(board: bool) -> list[tuple[str, str]]:
    """Tools shown in the rail. Underline / strike / note / comment share one 'Mark up' menu to keep the rail short."""
    out: list[tuple[str, str]] = []
    placed = False
    for key, tip in TOOLS:
        if key in MARK_NAMES:
            if not placed:
                out.append(("mark", "Mark up: underline, strike-through, sticky note, comment"))
                placed = True
            continue
        if board and key == "snip":              # nothing to snip on a blank board
            continue
        out.append((key, tip))
    return out


def mark_choices(board: bool) -> dict[str, str]:
    return {k: v for k, v in MARK_NAMES.items() if not (board and k in ("underline", "strike"))}


SYMBOLS = [("Operators", "± × ÷ ≠ ≈ ≤ ≥ < > √ ∛ ∞ ∝ ° % ‰"), ("Powers & indices", "² ³ ⁿ ⁻¹ ₁ ₂ ₃ ₙ ½ ⅓ ¼ ¾"),
           ("Greek", "π θ α β γ δ Δ λ μ σ Σ φ ω Ω"), ("Calculus & sets", "∫ ∑ ∏ ∂ ∇ ∈ ∉ ⊂ ∪ ∩ ∅ ∀ ∃"),
           ("Logic & arrows", "→ ⇒ ⇔ ← ↑ ↓ ∴ ∵ ∠ ⊥ ∥ ≅ ∼ …")]
SHAPE_NAMES = {"rect": "Rectangle", "ellipse": "Circle / ellipse", "arrow": "Arrow", "line": "Line"}
SWATCHES = ["#ef4444", "#f97316", "#fde047", "#22c55e", "#3b82f6", "#8b5cf6", "#ec4899", "#111827"]
GROUP = {"step": "shape", "snip": "shape", "hand": "pen", "pen": "pen", "highlighter": "highlighter", "text": "text", "note": "text", "comment": "text"}
NOTE_COLOR, COMMENT_COLOR = "#facc15", "#4c8dff"


def _sep() -> QFrame:
    f = QFrame()
    f.setFixedSize(1, 22)
    f.setStyleSheet(f"background: {theme.BORDER};")
    return f


def _hsep() -> QFrame:
    f = QFrame()
    f.setFixedSize(26, 1)
    f.setStyleSheet(f"background: {theme.BORDER};")
    return f


def tbtn(name: str, tip: str, checkable: bool = False, size: int = 20) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon(name, size=size))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class ToolBar(QObject):
    """Owns two floating pills: `rail` (vertical tools + undo/redo) and `props` (colours + width)."""
    tool_changed = Signal(str)
    undo = Signal()
    redo = Signal()
    symbol_chosen = Signal(str)

    def __init__(self, board: bool = False) -> None:
        super().__init__()
        self.board = board
        self.shape = "rect"
        self.mark = "note" if board else "underline"
        self.tool = "select"
        self._drawing = True
        self._hist = (False, False)
        self.state = {g: dict(v) for g, v in DEFAULT_STYLES.items()}
        for style in self.state.values():
            style.setdefault("opacity", 1.0)
        self.state["highlighter"]["opacity"] = 0.34
        self.state["text"]["size"] = 16

        self.rail = QFrame()
        self.rail.setObjectName("pill")
        rl = QVBoxLayout(self.rail)
        rl.setContentsMargins(6, 8, 6, 8)
        rl.setSpacing(3)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        for name, tip in rail_tools(board):
            b = tbtn(self.shape if name == "shape" else self.mark if name == "mark" else name, tip, True, 20)
            b.setFixedSize(38, 36)
            self.group.addButton(b)
            self.buttons[name] = b
            if name in ("shape", "mark"):
                m = QMenu(b)
                for key, label in (SHAPE_NAMES if name == "shape" else mark_choices(board)).items():
                    m.addAction(icon(key), label, lambda k=key, g=name: self._pick_group(g, k))
                b.setMenu(m)
                b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            else:
                b.clicked.connect(lambda _=False, n=name: self.set_tool(n))
            rl.addWidget(b)
        self.buttons["select"].setChecked(True)
        rl.addWidget(_hsep())
        self.undo_btn, self.redo_btn = tbtn("undo", "Undo (Ctrl+Z)", size=18), tbtn("redo", "Redo (Ctrl+Y)", size=18)
        for b in (self.undo_btn, self.redo_btn):
            b.setFixedSize(38, 34)
            rl.addWidget(b)
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)

        self.props = QFrame()
        self.props.setObjectName("pill")
        pl = QHBoxLayout(self.props)
        pl.setContentsMargins(12, 7, 12, 7)
        pl.setSpacing(5)
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        pl.addWidget(self.hint)
        self.swatch_btns: list[QToolButton] = []
        for c in SWATCHES:
            b = QToolButton()
            b.setFixedSize(22, 22)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(c)
            b.clicked.connect(lambda _=False, col=c: self.set_color(col))
            self.swatch_btns.append(b)
            pl.addWidget(b)
        self.custom = tbtn("plus", "Custom colour…", size=16)
        self.custom.setFixedSize(26, 26)
        self.custom.clicked.connect(self._custom_color)
        pl.addWidget(self.custom)
        pl.addSpacing(6)
        pl.addWidget(_sep())
        pl.addSpacing(6)
        self.symbols_btn = tbtn("sigma", "Insert a math symbol into the text you are typing", size=16)
        self.symbols_btn.setFixedSize(28, 28)
        self.symbols_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        sm = QMenu(self.symbols_btn)
        for title, chars in SYMBOLS:
            sub = sm.addMenu(title)
            for ch in chars.split():
                sub.addAction(ch, lambda c=ch: self.symbol_chosen.emit(c))
        sm.addSeparator()
        sm.addAction("Fraction  ( ) / ( )", lambda: self.symbol_chosen.emit("( ) / ( )"))
        sm.addAction("Square root  √( )", lambda: self.symbol_chosen.emit("√( )"))
        sm.addAction("Equation line  =", lambda: self.symbol_chosen.emit(" = "))
        self.symbols_btn.setMenu(sm)
        pl.addWidget(self.symbols_btn)
        self.preset_btn = tbtn("gear", "Professional style presets", size=16)
        self.preset_btn.setFixedSize(28, 28)
        self.preset_menu = QMenu(self.preset_btn)
        self._preset_group: str | None = None
        self.preset_btn.setMenu(self.preset_menu)
        self.preset_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        pl.addWidget(self.preset_btn)
        pl.addSpacing(2)
        self.width_label = QLabel("2.2 pt")
        self.width_label.setObjectName("muted")
        self.width_label.setMinimumWidth(44)
        self.width_label.setToolTip("Current stroke width or text size")
        pl.addWidget(self.width_label)
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(1, 40)
        self.width_slider.setFixedWidth(100)
        self.width_slider.setToolTip("Stroke width / text size")
        self.width_slider.valueChanged.connect(self._width_changed)
        pl.addWidget(self.width_slider)
        self.opacity_label = QLabel("100%")
        self.opacity_label.setObjectName("muted")
        self.opacity_label.setToolTip("Annotation opacity")
        pl.addWidget(self.opacity_label)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setFixedWidth(76)
        self.opacity_slider.setToolTip("Opacity / transparency")
        self.opacity_slider.valueChanged.connect(self._opacity_changed)
        pl.addWidget(self.opacity_slider)
        self.set_history(False, False)
        self._refresh_style_controls()

    def setVisible(self, visible: bool) -> None:
        self.rail.setVisible(visible)
        self.props.setVisible(visible)

    # ---- tool / style state -----------------------------------------------------------------
    def _active_tool(self) -> str:
        return self.shape if self.tool == "shape" else self.mark if self.tool == "mark" else self.tool

    def _group(self) -> str:
        return GROUP.get(self._active_tool(), "shape")

    def set_tool(self, name: str) -> None:
        self.tool = name
        self.buttons[name].setChecked(True)
        self._refresh_style_controls()
        self.tool_changed.emit(self._active_tool())

    def select_tool_by_name(self, name: str) -> None:
        if name in SHAPE_NAMES:
            self._pick_group("shape", name)
        elif name in mark_choices(self.board):
            self._pick_group("mark", name)
        elif name in self.buttons:
            self.set_tool(name)

    def _pick_group(self, group: str, key: str) -> None:
        if group == "shape":
            self.shape = key
        else:
            self.mark = key
        self.buttons[group].setIcon(icon(key, size=20))
        self.set_tool(group)

    def style_for(self, tool: str) -> dict:
        g = GROUP.get(tool, "shape" if tool in ("rect", "ellipse", "arrow", "line", "underline", "strike") else "pen")
        st = dict(self.state[g])
        if tool == "note":
            st["color"] = NOTE_COLOR
        elif tool == "comment":
            st["color"] = COMMENT_COLOR
        st.setdefault("size", self.state["text"].get("size", 16))
        if g == "text":
            st["size"] = self.state["text"]["size"]
        return st

    def set_color(self, color: str) -> None:
        self.state[self._group()]["color"] = color
        self._refresh_style_controls()

    def _custom_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.state[self._group()]["color"]), None, "Pick a colour")
        if c.isValid():
            self.set_color(c.name())

    def _rebuild_presets(self) -> None:
        g = self._group()
        if self._preset_group == g and self.preset_menu.actions():
            return
        self.preset_menu.clear()
        self._preset_group = g
        if g == "highlighter":
            presets = [("Soft marker", 12.0, 0.24), ("Classic marker", 14.0, 0.34), ("Bold marker", 20.0, 0.46)]
        elif g == "text":
            presets = [("Small text", 12.0, 1.0), ("Standard text", 16.0, 1.0), ("Large text", 24.0, 1.0)]
        else:
            presets = [("Fine · 1 pt", 1.0, 1.0), ("Standard · 2.2 pt", 2.2, 1.0), ("Bold · 4 pt", 4.0, 1.0)]
        for label, width, opacity in presets:
            self.preset_menu.addAction(label, lambda w=width, o=opacity, group=g: self._apply_preset(group, w, o))

    def _apply_preset(self, group: str, width: float, opacity: float) -> None:
        if group == "text":
            self.state[group]["size"] = int(width)
        else:
            self.state[group]["width"] = float(width)
        self.state[group]["opacity"] = float(opacity)
        self._refresh_style_controls()

    def _width_changed(self, v: int) -> None:
        g = self._group()
        if g == "text":
            self.state["text"]["size"] = 8 + v
        elif g == "highlighter":
            self.state[g]["width"] = float(v)
        else:
            self.state[g]["width"] = v / 4
        self._refresh_style_controls(update_slider=False)

    def _opacity_changed(self, value: int) -> None:
        opacity = value / 100.0
        self.state[self._group()]["opacity"] = opacity
        self.opacity_label.setText(f"{value}%")

    def _refresh_style_controls(self, update_slider: bool = True) -> None:
        g = self._group()
        tool = self._active_tool()
        cur = self.state[g]["color"].lower()
        for b, c in zip(self.swatch_btns, SWATCHES):
            ring = theme.TEXT if c == cur else "#cfd4e2"
            b.setStyleSheet(f"QToolButton {{ background: {c}; border-radius: 11px; border: 2px solid {ring}; }}")
        color_ok = tool not in ("note", "comment", "eraser", "select", "hand", "snip") and self._drawing
        for b in self.swatch_btns + [self.custom]:
            b.setEnabled(color_ok)
            b.setVisible(color_ok)
        slider_ok = tool not in ("select", "hand", "eraser", "snip", "step") and self._drawing
        sym_ok = tool in ("text", "note", "comment") and self._drawing
        self.symbols_btn.setVisible(sym_ok)
        self.symbols_btn.setEnabled(sym_ok)
        self.preset_btn.setEnabled(slider_ok)
        self.preset_btn.setVisible(slider_ok)
        self._rebuild_presets()
        self.width_slider.setEnabled(slider_ok)
        self.width_slider.setVisible(slider_ok)
        self.width_label.setVisible(slider_ok)
        self.opacity_slider.setEnabled(slider_ok)
        self.opacity_slider.setVisible(slider_ok)
        self.opacity_label.setVisible(slider_ok)
        if update_slider:
            opacity = int(round(float(self.state[g].get("opacity", 1.0)) * 100))
            self.opacity_slider.blockSignals(True)
            self.opacity_slider.setValue(max(10, min(100, opacity)))
            self.opacity_slider.blockSignals(False)
            self.opacity_label.setText(f"{max(10, min(100, opacity))}%")
        hints = {"select": "Click to select · drag to move · corners to resize · double-click to edit text",
                 "hand": "Drag the page to pan · G selects hand · V returns to select",
                 "eraser": "Drag over drawings to erase them", "note": "Click to place a sticky note",
                 "comment": "Click to add a comment",
                 "step": "Click to drop numbered markers 1, 2, 3… on each step of the solution",
                 "snip": "Drag a box around a question to send it to the whiteboard"}
        self.hint.setText(hints.get(tool, ""))
        self.hint.setVisible(bool(self.hint.text()))
        if update_slider:
            self.width_slider.blockSignals(True)
            if g == "text":
                self.width_slider.setValue(int(self.state["text"]["size"]) - 8)
            elif g == "highlighter":
                self.width_slider.setValue(int(self.state[g]["width"]))
            else:
                self.width_slider.setValue(max(1, int(round(self.state[g]["width"] * 4))))
            self.width_slider.blockSignals(False)
        if g == "text":
            self.width_label.setText(f'{int(self.state["text"]["size"])} pt')
        else:
            self.width_label.setText(f'{float(self.state[g]["width"]):g} pt')

    def set_history(self, can_undo: bool, can_redo: bool) -> None:
        self._hist = (can_undo, can_redo)
        self.undo_btn.setEnabled(can_undo and self._drawing)
        self.redo_btn.setEnabled(can_redo and self._drawing)

    def set_drawing_enabled(self, enabled: bool) -> None:
        self._drawing = enabled
        for name, b in self.buttons.items():
            if name not in ("select", "hand", "snip"):
                b.setEnabled(enabled)
        self.set_history(*self._hist)
        if not enabled and self.tool != "select":
            self.set_tool("select")
        else:
            self._refresh_style_controls(update_slider=False)


class ViewBar(QFrame):
    prev_page = Signal()
    next_page = Signal()
    goto = Signal(int)
    zoom_in = Signal()
    zoom_out = Signal()
    fit_page = Signal()
    fit_width = Signal()
    reading_mode = Signal()
    follow_toggled = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("pill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(3)
        self.prev = tbtn("left", "Previous page (PageUp)", size=18)
        self.next = tbtn("right", "Next page (PageDown)", size=18)
        self.prev.clicked.connect(self.prev_page)
        self.next.clicked.connect(self.next_page)
        self.page_edit = QLineEdit("1")
        self.page_edit.setFixedWidth(50)
        self.page_edit.setStyleSheet('padding: 4px 6px;')
        self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_edit.setToolTip("Jump to page")
        self.page_edit.returnPressed.connect(self._jump)
        self.total = QLabel("/ 1")
        self.total.setObjectName("muted")
        self.follow = QPushButton("Following host")
        self.follow.setCheckable(True)
        self.follow.setChecked(True)
        self.follow.setProperty("kind", "ghost")
        self.follow.setToolTip("Stay on the page the host is viewing")
        self.follow.toggled.connect(self.follow_toggled)
        self.follow.hide()
        out, inn = tbtn("zoom_out", "Zoom out (Ctrl+-)", size=18), tbtn("zoom_in", "Zoom in (Ctrl++)", size=18)
        fp, fw = tbtn("fit_page", "Fit page", size=18), tbtn("fit_width", "Fit width", size=18)
        fs = tbtn("fullscreen", "Reading mode (F11)", size=18)
        out.clicked.connect(self.zoom_out)
        inn.clicked.connect(self.zoom_in)
        fp.clicked.connect(self.fit_page)
        fw.clicked.connect(self.fit_width)
        fs.clicked.connect(self.reading_mode)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (self.prev, self.page_edit, self.total, self.next, self.follow):
            lay.addWidget(w)
        lay.addSpacing(6)
        lay.addWidget(_sep())
        lay.addSpacing(6)
        for w in (out, self.zoom_label, inn):
            lay.addWidget(w)
        lay.addSpacing(6)
        lay.addWidget(_sep())
        lay.addSpacing(6)
        for w in (fp, fw, fs):
            lay.addWidget(w)

    def _jump(self) -> None:
        try:
            self.goto.emit(int(self.page_edit.text()) - 1)
        except ValueError:
            pass

    def set_page(self, page: int, total: int) -> None:
        self.page_edit.setText(str(page + 1))
        self.total.setText(f"/ {total}")
        self.prev.setEnabled(page > 0)
        self.next.setEnabled(page < total - 1)

    def set_zoom(self, z: float) -> None:
        self.zoom_label.setText(f"{round(z * 100)}%")


class AvatarStack(QWidget):
    """Overlapping coloured initials for everyone in the room, plus an online counter."""

    def __init__(self) -> None:
        super().__init__()
        self.members: list[dict] = []
        self.setFixedHeight(30)
        self.setMinimumWidth(110)

    def set_members(self, members: dict) -> None:
        self.members = sorted(members.values(), key=lambda m: (not m["connected"], not m["is_host"]))
        online = sum(1 for m in self.members if m["connected"])
        shown = min(len(self.members), 5)
        self.setFixedWidth(26 + 20 * max(shown - 1, 0) + (24 if len(self.members) > 5 else 0) + 74)
        self._online = online
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        shown = self.members[:5]
        x = 0
        for m in shown:
            c = QColor(m["color"])
            if not m["connected"]:
                c.setAlpha(100)
            p.setPen(QPen(QColor(theme.BG1), 2.5))
            p.setBrush(c)
            p.drawEllipse(x, 2, 26, 26)
            p.setPen(QColor("#111827"))
            p.drawText(x, 2, 26, 26, int(Qt.AlignmentFlag.AlignCenter), (m["name"][:1] or "?").upper())
            x += 20
        if len(self.members) > 5:
            p.setPen(QPen(QColor(theme.BG1), 2.5))
            p.setBrush(QColor(theme.BG3))
            p.drawEllipse(x, 2, 26, 26)
            p.setPen(QColor(theme.TEXT))
            p.drawText(x, 2, 26, 26, int(Qt.AlignmentFlag.AlignCenter), f"+{len(self.members) - 5}")
            x += 20
        p.setPen(QColor(theme.MUTED))
        p.drawText(x + 14, 0, 80, 30, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), f"{getattr(self, '_online', 0)} online")


class SegmentedSwitch(QFrame):
    """A pill-shaped two-way switch, e.g. PDF | Whiteboard."""
    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self.setObjectName("segment")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, label, ic in items:
            b = QPushButton(label)
            b.setIcon(icon(ic, size=16))
            b.setCheckable(True)
            b.setProperty("kind", "seg")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        next(iter(self.buttons.values())).setChecked(True)

    def set_value(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)


class TopBar(QFrame):
    home = Signal()
    open_pdf = Signal()
    copy_code = Signal()
    mode_changed = Signal(str)          # "pdf" | "board"

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("topbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 6)
        lay.setSpacing(8)
        self.left = QHBoxLayout()
        self.left.setSpacing(4)
        lay.addLayout(self.left)
        self.brand = QPushButton("  StudyPDF")
        self.brand.setIcon(QIcon(logo_pixmap(22)))
        self.brand.setIconSize(QSize(22, 22))
        self.brand.setProperty("kind", "ghost")
        self.brand.setToolTip("Back to the home screen")
        self.brand.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.brand.clicked.connect(self.home)
        self.doc_label = QLabel("")
        self.doc_label.setObjectName("muted")
        self.mode = SegmentedSwitch([("pdf", "PDF", "pages"), ("board", "Whiteboard", "board")])
        self.mode.changed.connect(self.mode_changed)
        self.room_chip = QPushButton()
        self.room_chip.setProperty("kind", "ghost")
        self.room_chip.setIcon(icon("copy", size=16))
        self.room_chip.setToolTip("Click to copy the room code")
        self.room_chip.clicked.connect(self.copy_code)
        self.users_chip = AvatarStack()
        lay.addWidget(self.brand)
        lay.addWidget(self.doc_label, 1)
        lay.addWidget(self.mode)
        lay.addStretch(1)
        lay.addWidget(self.room_chip)
        lay.addWidget(self.users_chip)
        self.right = QHBoxLayout()
        self.right.setSpacing(4)
        lay.addLayout(self.right)
        self.room_chip.hide()
        self.users_chip.hide()

    def add_left(self, widget: QWidget) -> None:
        self.left.addWidget(widget)

    def add_action(self, widget: QWidget) -> None:
        self.right.addWidget(widget)

    def set_mode(self, key: str) -> None:
        self.mode.set_value(key)

    def set_doc(self, name: str) -> None:
        fm = QFontMetrics(self.doc_label.font())
        self.doc_label.setText(fm.elidedText(name, Qt.TextElideMode.ElideMiddle, 320))

    def set_room(self, code: str | None, members: dict | None = None) -> None:
        self.room_chip.setVisible(bool(code))
        self.users_chip.setVisible(bool(code))
        if code:
            self.room_chip.setText(f"Room  {code}")
            self.users_chip.set_members(members or {})
