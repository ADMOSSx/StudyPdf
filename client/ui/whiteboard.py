"""Collaborative whiteboard for working through a question step by step.

Boards are extra "pages" that live next to the PDF. They use exactly the same annotation engine as the PDF
(pen, shapes, text, notes, undo, live cursors), so everything a friend draws appears instantly in the room.
Annotation page ids for board i are BOARD_BASE + i.
"""
from __future__ import annotations

from typing import Callable

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QStackedWidget, QTabBar, QToolButton,
                               QVBoxLayout, QWidget)

from client.annotations.editor import Editor
from client.annotations.model import AnnotationStore, bounds, new_annotation
from client.annotations.render import NOTE_PAD, paint_annotation, text_height
from client.collaboration.boards import BoardStore
from client.ui import theme
from client.ui.canvas import PageCanvas
from client.ui.icons import icon
from client.ui.stage import Stage
from client.ui.toolbar import ToolBar, ViewBar
from client.ui.viewer import Viewer
from shared.protocol import BOARD_BASE, MAX_BOARDS

BOARD_W, BOARD_H = 1500.0, 2100.0          # points: a wide, tall sheet that scrolls
CARD_X, CARD_W = 60.0, 900.0
BACKGROUNDS = {"grid": "Grid", "lines": "Ruled lines", "dots": "Dots", "blank": "Blank"}


class BoardDocument:
    """Minimal stand-in for PdfDocument so the shared PageCanvas/Viewer can show boards."""
    is_board = True
    path = ""
    name = "Whiteboard"

    def __init__(self, boards: BoardStore) -> None:
        self.boards = boards

    @property
    def page_count(self) -> int:
        return max(1, len(self.boards.items))

    def page_size(self, index: int) -> tuple[float, float]:
        return BOARD_W, BOARD_H

    def words(self, index: int) -> list:
        return []

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- dialogs
class QuestionTextDialog(QDialog):
    def __init__(self, parent, text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Add the question")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        head = QLabel("Type or paste the question")
        head.setObjectName("h2")
        hint = QLabel("It appears as a card at the top of the board. Use the Σ menu on the text tool for maths symbols.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        self.edit = QPlainTextEdit()
        self.edit.setPlainText(text)
        self.edit.setPlaceholderText("e.g. Solve  x² + 5x + 6 = 0  by factorising.")
        self.edit.setMinimumHeight(150)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = QPushButton("Cancel"), QPushButton("Add to board")
        ok.setProperty("kind", "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        for w in (head, hint, self.edit):
            lay.addWidget(w)
        lay.addLayout(row)

    def _accept(self) -> None:
        if self.edit.toPlainText().strip():
            self.accept()

    def value(self) -> str:
        return self.edit.toPlainText().strip()[:1800]


class SendToBoardDialog(QDialog):
    """Shown after snipping a question from the PDF."""

    def __init__(self, parent, default_title: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Send question to whiteboard")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        head = QLabel("Send this question to a new whiteboard")
        head.setObjectName("h2")
        hint = QLabel("Everyone in the room can then work through it together.")
        hint.setObjectName("muted")
        self.title = QLineEdit(default_title)
        self.title.setMaxLength(40)
        self.track = QCheckBox("Also add it to my Questions list")
        self.track.setChecked(True)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = QPushButton("Cancel"), QPushButton("Send to whiteboard")
        ok.setProperty("kind", "primary")
        ok.setDefault(True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        for w in (head, hint, QLabel("Board title"), self.title, self.track):
            lay.addWidget(w)
        lay.addLayout(row)

    def _accept(self) -> None:
        if self.title.text().strip():
            self.accept()

    def values(self) -> tuple[str, bool]:
        return self.title.text().strip(), self.track.isChecked()


# ---------------------------------------------------------------- workspace
class WhiteboardWorkspace(QWidget):
    snip_from_pdf = Signal()
    reading_requested = Signal()

    def __init__(self, store: AnnotationStore, editor: Editor, boards: BoardStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store, self.editor, self.boards = store, editor, boards
        self.request_board: Callable[[str, Callable[[int], None] | None], None] = lambda title, then: None
        self.doc = BoardDocument(boards)
        self._fitted = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QFrame()
        header.setObjectName("panel")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(8)
        title = QLabel("Boards")
        title.setObjectName("h2")
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        new_btn = QPushButton(icon("plus"), "  New board")
        new_btn.clicked.connect(lambda: self.new_board())
        hl.addWidget(title)
        hl.addSpacing(6)
        hl.addWidget(self.tabs, 1)
        hl.addWidget(new_btn)
        hl.addWidget(self._menu_button("question", "Question & solution", [
            ("Add question text…", self.add_question_text),
            ("Snip a question from the PDF…", self.snip_from_pdf.emit),
            None,
            ("Add solution template (Given / Find / Steps / Answer)", self.add_solution_template)]))
        hl.addWidget(self._menu_button("grid", "Board background", [
            (label, lambda m=mode: self.set_background(m)) for mode, label in BACKGROUNDS.items()]))
        hl.addWidget(self._menu_button("export", "Export", [
            ("Export this board as PNG…", self.export_png), ("Export all boards as one PDF…", self.export_pdf)]))
        clear = QToolButton()
        clear.setIcon(icon("trash"))
        clear.setToolTip("Clear this board")
        clear.clicked.connect(self.clear_board)
        hl.addWidget(clear)
        root.addWidget(header)

        self.toolbar = ToolBar(board=True)
        self.viewbar = ViewBar()
        self.viewbar.prev.setToolTip("Previous board (PageUp)")
        self.viewbar.next.setToolTip("Next board (PageDown)")
        self.viewbar.page_edit.setToolTip("Jump to board")
        self.canvas = PageCanvas(store, editor)
        self.canvas.page_offset = BOARD_BASE
        self.canvas.set_document(self.doc)
        self.viewer = Viewer(self.canvas)
        self.stage = Stage(self.viewer, self.toolbar.rail, self.toolbar.props, self.viewbar)

        self.body = QStackedWidget()
        self.empty = self._build_empty()
        self.body.addWidget(self.empty)
        self.body.addWidget(self.stage)
        root.addWidget(self.body, 1)

        self.toolbar.tool_changed.connect(self.canvas.set_tool)
        self.toolbar.symbol_chosen.connect(self.canvas.insert_text)
        self.viewbar.prev_page.connect(lambda: self.step_board(-1))
        self.viewbar.next_page.connect(lambda: self.step_board(1))
        self.viewbar.goto.connect(self.show_board)
        self.viewbar.zoom_in.connect(lambda: self.viewer.zoom_step(1))
        self.viewbar.zoom_out.connect(lambda: self.viewer.zoom_step(-1))
        self.viewbar.fit_page.connect(self.viewer.fit_page)
        self.viewbar.fit_width.connect(self.viewer.fit_width)
        self.viewbar.reading_mode.connect(self.reading_requested)
        self.viewer.zoom_changed.connect(self.viewbar.set_zoom)
        boards.changed.connect(self._refresh)
        self._refresh()

    # ---- building blocks
    def _menu_button(self, icon_name: str, tip: str, entries: list) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon(icon_name))
        b.setToolTip(tip)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(b)
        for entry in entries:
            if entry is None:
                m.addSeparator()
            else:
                m.addAction(entry[0], entry[1])
        b.setMenu(m)
        return b

    def _build_empty(self) -> QWidget:
        w = QFrame()
        w.setObjectName("stagebg")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        ic = QLabel()
        ic.setPixmap(icon("board", theme.ACCENT, 56).pixmap(56, 56))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head = QLabel("Work through a question together")
        head.setObjectName("h1")
        head.setStyleSheet("font-size: 24px;")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel("Snip a question from the PDF or type it in, then draw, write and number the steps of the solution.\n"
                      "Everyone in the room sees every stroke live.")
        text.setObjectName("muted")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row = QHBoxLayout()
        row.addStretch(1)
        create = QPushButton("Create a whiteboard")
        create.setProperty("kind", "primary")
        create.clicked.connect(lambda: self.new_board())
        snip = QPushButton(icon("snip"), "  Snip a question from the PDF")
        snip.clicked.connect(self.snip_from_pdf)
        row.addWidget(create)
        row.addWidget(snip)
        row.addStretch(1)
        for x in (ic, head, text):
            lay.addWidget(x)
        lay.addSpacing(8)
        lay.addLayout(row)
        lay.addStretch(2)
        return w

    # ---- boards and navigation
    def _refresh(self) -> None:
        n = len(self.boards.items)
        cur = max(0, min(self.tabs.currentIndex(), n - 1))
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for b in self.boards.items:
            self.tabs.addTab(b.get("title", "Board"))
        self.tabs.setCurrentIndex(cur)
        self.tabs.blockSignals(False)
        self.body.setCurrentWidget(self.stage if n else self.empty)
        if n:
            self.canvas.set_page(cur)
            if not self._fitted and self.isVisible():          # first board created while the workspace is open
                self._fitted = True
                QTimer.singleShot(60, self.viewer.fit_width)
        self.viewbar.set_page(cur, max(n, 1))
        self.canvas.update()

    def _tab_changed(self, index: int) -> None:
        if 0 <= index < len(self.boards.items):
            self.canvas.set_page(index)
            self.viewbar.set_page(index, len(self.boards.items))

    def current_board(self) -> int:
        return max(0, self.tabs.currentIndex())

    def show_board(self, index: int) -> None:
        if 0 <= index < len(self.boards.items):
            self.tabs.setCurrentIndex(index)

    def step_board(self, delta: int) -> None:
        self.show_board(self.current_board() + delta)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._fitted and self.boards.items:
            self._fitted = True
            QTimer.singleShot(60, self.viewer.fit_width)

    def new_board(self, title: str | None = None) -> None:
        n = len(self.boards.items)
        if n >= MAX_BOARDS:
            QMessageBox.information(self, "Whiteboard", f"A room can have up to {MAX_BOARDS} boards.")
            return
        if title is None:
            title, ok = QInputDialog.getText(self, "New whiteboard", "Board title:", text=f"Board {n + 1}")
            if not ok or not title.strip():
                return
        self.request_board(title.strip()[:40], lambda idx: self.show_board(idx))

    def ensure_board(self, then: Callable[[int], None]) -> None:
        if self.boards.items:
            then(self.current_board())
        else:
            self.request_board("Board 1", then)

    # ---- content helpers
    def _next_free_y(self, page: int) -> float:
        items = self.store.page_items(page)
        return max((bounds(a)[3] for a in items), default=30.0) + 30.0

    def add_question_text(self, text: str | None = None) -> None:
        if text is None:
            dlg = QuestionTextDialog(self)
            if not dlg.exec():
                return
            text = dlg.value()
        if text.strip():
            self.ensure_board(lambda idx: self._add_card(idx, text.strip()))

    def _add_card(self, idx: int, text: str) -> None:
        page = BOARD_BASE + idx
        uid, name = self.editor.identity()
        y = self._next_free_y(page)
        h = text_height(text, CARD_W, 20, NOTE_PAD)
        ann = new_annotation("note", page, [CARD_X, y, CARD_X + CARD_W, y + h],
                             {"color": "#dff1ee", "width": 1.0, "size": 20.0, "opacity": 1.0}, uid, name, text)
        self.editor.create(ann)
        self.show_board(idx)

    def add_question_image(self, idx: int, data_b64: str, w_pts: float, h_pts: float) -> None:
        """Place a snipped region of the PDF at the top of board `idx`."""
        page = BOARD_BASE + idx
        uid, name = self.editor.identity()
        w = max(480.0, min(1100.0, w_pts * 1.5))
        h = w * h_pts / max(w_pts, 1.0)
        y = self._next_free_y(page)
        ann = new_annotation("image", page, [CARD_X, y, CARD_X + w, y + h],
                             {"color": "#000000", "width": 1.0, "opacity": 1.0}, uid, name, data=data_b64)
        self.editor.create(ann)
        self.show_board(idx)

    def add_solution_template(self) -> None:
        def build(idx: int) -> None:
            page = BOARD_BASE + idx
            uid, name = self.editor.identity()
            y0 = self._next_free_y(page) + 10
            style = {"color": theme.ACCENT, "width": 1.0, "size": 24.0, "opacity": 1.0}
            self.editor.begin_group()
            for label, dy in (("Given:", 0), ("Find:", 130), ("Steps:", 260), ("Answer:", 900)):
                self.editor.create(new_annotation("text", page, [CARD_X, y0 + dy, CARD_X + 300, y0 + dy + 44],
                                                  dict(style), uid, name, label))
            self.editor.end_group()
            self.show_board(idx)
        self.ensure_board(build)

    def set_background(self, mode: str) -> None:
        self.canvas.board_bg = mode
        self.canvas.update()

    def clear_board(self) -> None:
        if not self.boards.items:
            return
        page = BOARD_BASE + self.current_board()
        items = [a for a in self.store.page_items(page) if self.canvas.can_modify(a)]
        if not items:
            return
        if QMessageBox.question(self, "Clear this board?", "Remove everything you are allowed to remove from this board?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.editor.begin_group()
        for a in items:
            self.editor.delete(a["id"])
        self.editor.end_group()

    # ---- export
    def _render_board(self, idx: int, scale: float = 1.4) -> tuple[QImage, float, float]:
        page = BOARD_BASE + idx
        items = self.store.page_items(page)
        h_pts = min(BOARD_H, max(900.0, max((bounds(a)[3] for a in items), default=0.0) + 80.0))
        img = QImage(int(BOARD_W * scale), int(h_pts * scale), QImage.Format.Format_RGB888)
        img.fill(QColor("#ffffff"))
        p = QPainter(img)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        p.scale(scale, scale)
        mode, step = self.canvas.board_bg, 40.0
        if mode != "blank":
            p.setPen(QPen(QColor("#e4eaec"), 1 / scale))
            for y in range(1, int(h_pts / step) + 1):
                if mode == "dots":
                    for x in range(1, int(BOARD_W / step) + 1):
                        p.drawPoint(QPointF(x * step, y * step))
                else:
                    p.drawLine(QPointF(0, y * step), QPointF(BOARD_W, y * step))
            if mode == "grid":
                for x in range(1, int(BOARD_W / step) + 1):
                    p.drawLine(QPointF(x * step, 0), QPointF(x * step, h_pts))
        for ann in items:
            paint_annotation(p, ann, expanded=ann["kind"] == "comment")
        p.end()
        return img, BOARD_W, h_pts

    def export_png(self) -> None:
        if not self.boards.items:
            return
        idx = self.current_board()
        name = self.boards.items[idx].get("title", "board").replace("/", "-")
        path, _ = QFileDialog.getSaveFileName(self, "Export board", f"{name}.png", "PNG image (*.png)")
        if path:
            img, _, _ = self._render_board(idx)
            if not img.save(path if path.lower().endswith(".png") else path + ".png", "PNG"):
                QMessageBox.warning(self, "Export failed", "Couldn't write the image file.")

    def export_pdf(self) -> None:
        if not self.boards.items:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export all boards", "whiteboards.pdf", "PDF files (*.pdf)")
        if not path:
            return
        from client.pdf.snip import _encode
        out = pymupdf.open()
        try:
            import base64
            for i in range(len(self.boards.items)):
                img, w, h = self._render_board(i, 1.2)
                page = out.new_page(width=w, height=h)
                page.insert_image(page.rect, stream=base64.b64decode(_encode(img, "JPEG", 90)))
            out.save(path if path.lower().endswith(".pdf") else path + ".pdf", garbage=3, deflate=True)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", f"Couldn't write the PDF.\n\n{exc}")
        finally:
            out.close()
