"""Main window: builds the workspace layout and wires UI <-> annotation store <-> collaboration session."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QProgressDialog, QPushButton, QSplitter, QStackedWidget, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget, QLineEdit, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QTextEdit, QComboBox)

from client.annotations.editor import Editor
from client.annotations.model import AnnotationStore
from client.collaboration.questions import QuestionStore
from client.collaboration.session import SessionController
from client.config import DEFAULT_SERVER, cache_dir
from client.pdf.engine import PdfDocument, PdfError, file_sha256
from client.pdf.exporter import export_annotated, export_flattened
from client.pdf.search import PdfSearchThread
from client.pdf.snip import encode_snip
from client.collaboration.boards import BoardStore
from client.ui import theme
from client.ui.canvas import PageCanvas
from client.ui.dialogs import CreateRoomDialog, JoinRoomDialog, RoomCreatedDialog, RoomSettingsDialog
from client.ui.home import HomeScreen
from client.ui.icons import icon
from client.ui.stage import Stage
from client.ui.panels import ChatPanel, PagesPanel, QuestionsPanel, TimerWidget, UsersPanel, section_label
from client.ui.navigation import merge_old_solving_data, page_matches_filter
from client.ui.search_dialog import SearchDialog
from client.ui.toolbar import ToolBar, TopBar, ViewBar, tbtn
from client.ui.viewer import Viewer
from client.ui.palette import Command, CommandPalette
from client.ui.whiteboard import SendToBoardDialog, WhiteboardWorkspace
from database.client_db import ClientDatabase
from shared.protocol import MAX_PDF_BYTES
from shared.timer import TimerState

TOOL_KEYS = {"M": "step", "Q": "snip", "V": "select", "G": "hand", "P": "pen", "H": "highlighter", "T": "text", "R": "rect", "O": "ellipse", "A": "arrow",
             "L": "line", "U": "underline", "S": "strike", "N": "note", "C": "comment", "E": "eraser"}


class Toast(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {theme.BG3}; border: 1px solid {theme.BORDER}; border-left: 3px solid {theme.ACCENT}; "
                           "border-radius: 10px; padding: 9px 16px;")
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity", self)
        self._anim.setDuration(220)
        self.hide()
        self._timer = QTimer(self, singleShot=True, interval=3200)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str) -> None:
        self.setText(text)
        self.adjustSize()
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 100)
        if not self.isVisible():
            self._fx.setOpacity(0.0)
            self._anim.stop()
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        self.show()
        self.raise_()
        self._timer.start()


class MainWindow(QMainWindow):
    def __init__(self, db: ClientDatabase) -> None:
        super().__init__()
        self.db = db
        self.setWindowTitle(f"StudyPDF · {theme.THEME_NAME}")
        self.resize(1440, 880)
        self.setAcceptDrops(True)
        self.doc: PdfDocument | None = None
        self.doc_sha = ""
        self._reading = False
        self._created_dialog: RoomCreatedDialog | None = None
        self._connecting: QProgressDialog | None = None
        self._banner_was_down = False
        self._search_threads: list[PdfSearchThread] = []
        self._search_dialog: SearchDialog | None = None
        self._right_hidden = False
        self._reading_right_was_visible = True

        self.store = AnnotationStore()
        self.editor = Editor(self.store)
        self.questions = QuestionStore()
        self.boards = BoardStore()
        self.session = SessionController(self.store, self.questions, cache_dir(), self.boards)
        self.editor.identity = lambda: self.session.me_info() if self.session.active else ("local", "You")

        self._build_ui()
        self._wire()
        self._build_shortcuts()
        self._save_timer = QTimer(self, singleShot=True, interval=1500)
        self._save_timer.timeout.connect(self._save_now)
        self._notes_timer = QTimer(self, singleShot=True, interval=450)
        self._notes_timer.timeout.connect(self._save_study_notes)
        self.refresh_recent()
        self.refresh_bookmarks()
        self._apply_room_ui()
        saved_geometry = self.db.get_setting("window_geometry", "")
        if saved_geometry:
            self.restoreGeometry(QByteArray.fromBase64(saved_geometry.encode()))

    # ================================================================ UI construction
    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.home = HomeScreen()
        self.stack.addWidget(self.home)

        self.workspace = QWidget()
        self.workspace.setObjectName("root")
        wl = QVBoxLayout(self.workspace)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(0)
        self.topbar = TopBar()
        self._build_topbar_actions()
        wl.addWidget(self.topbar)
        self.banner = QLabel("")
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setFixedHeight(26)
        self.banner.hide()
        wl.addWidget(self.banner)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(12)
        self.splitter.setContentsMargins(12, 0, 12, 12)
        left = QFrame()
        left.setObjectName("panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 10, 4, 6)
        ll.setSpacing(7)
        self.nav_tabs = QTabWidget()
        pages_tab = QWidget()
        ptl = QVBoxLayout(pages_tab)
        ptl.setContentsMargins(2, 2, 2, 2)
        self.page_filter = QLineEdit()
        self.page_filter.setPlaceholderText("Page #, range or list…")
        self.page_filter.setClearButtonEnabled(True)
        self.page_filter.setToolTip("Examples: 500, page 500, 100-120, or 1, 3, 7-9. Ctrl+G jumps directly.")
        self.pages = PagesPanel()
        ptl.addWidget(self.page_filter)
        ptl.addWidget(self.pages, 1)
        self.outline_tree = QTreeWidget()
        self.outline_tree.setHeaderHidden(True)
        self.outline_tree.setIndentation(14)
        self.outline_tree.setUniformRowHeights(True)
        self.outline_tree.setToolTip("Jump through the PDF table of contents")
        self.bookmark_list = QListWidget()
        self.bookmark_list.setToolTip("Saved reading positions for this PDF")
        self.nav_tabs.addTab(pages_tab, "Pages")
        self.nav_tabs.addTab(self.outline_tree, "Outline")
        self.nav_tabs.addTab(self.bookmark_list, "Saved")
        ll.addWidget(self.nav_tabs, 1)
        left.setFixedWidth(190)
        self.left_panel = left

        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.viewbar = ViewBar()
        self.toolbar = ToolBar()
        self.canvas = PageCanvas(self.store, self.editor)
        self.viewer = Viewer(self.canvas)
        self.stage = Stage(self.viewer, self.toolbar.rail, self.toolbar.props, self.viewbar)
        self.wait_box = QWidget()
        wb = QVBoxLayout(self.wait_box)
        wb.addStretch(1)
        self.wait_label = QLabel("Waiting for the host's PDF…")
        self.wait_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wait_label.setObjectName("h2")
        self.wait_bar = QProgressBar()
        self.wait_bar.setFixedWidth(280)
        self.wait_bar.setRange(0, 100)
        wb.addWidget(self.wait_label)
        wb.addWidget(self.wait_bar, 0, Qt.AlignmentFlag.AlignHCenter)
        wb.addStretch(1)
        self.centre_stack = QStackedWidget()
        self.centre_stack.addWidget(self.stage)
        self.centre_stack.addWidget(self.wait_box)
        cl.addWidget(self.centre_stack, 1)

        right = QFrame()
        right.setObjectName("panel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 10, 0, 0)
        rl.setSpacing(8)
        self.timer = TimerWidget()
        tw = QWidget()
        tl = QVBoxLayout(tw)
        tl.setContentsMargins(12, 0, 12, 0)
        tl.addWidget(self.timer)
        rl.addWidget(tw)
        self.users = UsersPanel()
        self.tabs = QTabWidget()
        self.chat = ChatPanel()
        self.questions_panel = QuestionsPanel(self.questions)
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.chat, "Chat")
        self.tab_people = self.tabs.addTab(self.users, "People")
        # The whiteboard is a full-size, collaborative workspace (shares the annotation engine with the PDF).
        self.whiteboard = WhiteboardWorkspace(self.store, self.editor, self.boards)
        self.whiteboard.request_board = self._request_board
        self.whiteboard.snip_from_pdf.connect(self.start_snip)
        self.whiteboard.reading_requested.connect(self.toggle_reading)
        self.centre_stack.addWidget(self.whiteboard)
        self.tabs.addTab(self.questions_panel, "Questions")
        self.study_notes = QTextEdit()
        self.study_notes.setPlaceholderText("Your notes for this PDF…\n\nSummaries, formulas, reminders. Saved automatically on this device.")
        self.study_notes.textChanged.connect(self._queue_study_notes_save)
        self.tabs.addTab(self.study_notes, "Notes")
        rl.addWidget(self.tabs, 1)
        right.setMinimumWidth(310)
        right.setMaximumWidth(390)
        self.right_panel = right

        self.splitter.addWidget(left)
        self.splitter.addWidget(centre)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        wl.addWidget(self.splitter, 1)
        self.stack.addWidget(self.workspace)
        self.toast = Toast(self.workspace)

    def _build_topbar_actions(self) -> None:
        tb = self.topbar
        self.pages_btn = tbtn("sidebar", "Show / hide the page sidebar", checkable=True)
        self.pages_btn.setChecked(True)
        self.pages_btn.toggled.connect(lambda on: self.left_panel.setVisible(on and not self._reading))
        tb.add_left(self.pages_btn)

        search_b = tbtn("search", "Search in the PDF (Ctrl+F)")
        search_b.clicked.connect(self.open_search)

        save_b = tbtn("save", "Save & export")
        save_b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(save_b)
        m.addAction(icon("save"), "Save annotated copy…\tCtrl+S", self.save_annotated)
        m.addAction(icon("export"), "Export flattened (print-ready) PDF…", self.export_flat)
        m.addSeparator()
        m.addAction("Save session file…", self.save_session_file)
        m.addAction("Open session file…", self.open_session_file)
        save_b.setMenu(m)

        self.study_btn = QPushButton(icon("users", theme.INK, 16), "  Study together")
        self.study_btn.setProperty("kind", "primary")
        self.study_btn.setToolTip("Create or join a live study room")
        self.study_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rm = QMenu(self.study_btn)
        rm.addAction(icon("plus"), "Create a study room…", self.create_room)
        rm.addAction(icon("join"), "Join a study room…", self.join_room)
        rm.addSeparator()
        self.act_copy = rm.addAction(icon("copy"), "Copy room code", self.copy_code)
        self.act_settings = rm.addAction(icon("gear"), "Room settings…", self.room_settings)
        self.act_leave = rm.addAction(icon("leave"), "Leave room", self.leave_room)
        self.study_btn.setMenu(rm)

        more_b = tbtn("more", "More")
        more_b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        mm = QMenu(more_b)
        mm.addAction(icon("folder"), "Open PDF…\tCtrl+O", self.open_pdf_dialog)
        mm.addAction(icon("command"), "Command palette…\tCtrl+K", self.open_command_palette)
        mm.addSeparator()
        mm.addAction(icon("list"), "Go to page…\tCtrl+G", self.quick_goto_page)
        mm.addAction(icon("note"), "Bookmark this page\tCtrl+B", self.add_bookmark)
        mm.addAction(icon("fullscreen"), "Reading mode\tF11", self.toggle_reading)
        more_b.setMenu(mm)

        self.right_btn = tbtn("panel-right-close", "Hide the right panel (chat, people, questions, notes)", checkable=True, size=18)
        self.right_btn.toggled.connect(self.toggle_right_panel)
        for w in (search_b, save_b, self.study_btn, more_b, self.right_btn):
            tb.add_action(w)

    def open_command_palette(self) -> None:
        CommandPalette(self, self._commands()).exec()

    def _commands(self) -> list[Command]:
        in_pdf = lambda: self.doc is not None and self.stack.currentIndex() == 1          # noqa: E731
        in_room = lambda: self.session.active and self.session.joined_once                # noqa: E731
        no_room = lambda: not self.session.active                                         # noqa: E731
        on_board = lambda: self.centre_stack.currentWidget() is self.whiteboard           # noqa: E731
        tool = lambda name: (lambda: self._active_toolbar().select_tool_by_name(name))    # noqa: E731
        cmds = [
            Command("Open PDF…", "File", self.open_pdf_dialog, "Ctrl+O"),
            Command("Search in the PDF…", "File", self.open_search, "Ctrl+F", in_pdf),
            Command("Save annotated copy…", "File", self.save_annotated, "Ctrl+S", in_pdf),
            Command("Export flattened (print-ready) PDF…", "File", self.export_flat, "", in_pdf),
            Command("Save session file…", "File", self.save_session_file, "", in_pdf),
            Command("Open session file…", "File", self.open_session_file),
            Command("Back to home", "File", self.go_home, "", in_pdf),
            Command("Go to page…", "Navigate", self.quick_goto_page, "Ctrl+G", in_pdf),
            Command("Bookmark this page", "Navigate", self.add_bookmark, "Ctrl+B", in_pdf),
            Command("Clear bookmarks for this PDF", "Navigate", self.clear_bookmarks, "", in_pdf),
            Command("Create a study room…", "Room", self.create_room, "", no_room),
            Command("Join a study room…", "Room", self.join_room, "", no_room),
            Command("Copy room code", "Room", self.copy_code, "", in_room),
            Command("Room settings…", "Room", self.room_settings, "", in_room),
            Command("Leave room", "Room", self.leave_room, "", in_room),
            Command("Open the whiteboard", "Whiteboard", self.show_whiteboard, "Ctrl+Shift+W", in_pdf),
            Command("Back to the PDF", "Whiteboard", self.show_pdf_workspace, "Ctrl+Shift+W", on_board),
            Command("New board…", "Whiteboard", lambda: (self.show_whiteboard(), self.whiteboard.new_board()), "", in_pdf),
            Command("Add question text to the board…", "Whiteboard",
                    lambda: (self.show_whiteboard(), self.whiteboard.add_question_text()), "", in_pdf),
            Command("Snip a question from the PDF", "Whiteboard", self.start_snip, "Q", in_pdf),
            Command("Add solution template", "Whiteboard", lambda: (self.show_whiteboard(), self.whiteboard.add_solution_template()), "", in_pdf),
            Command("Export board as PNG…", "Whiteboard", self.whiteboard.export_png, "", on_board),
            Command("Export all boards as PDF…", "Whiteboard", self.whiteboard.export_pdf, "", on_board),
            Command("Toggle the page sidebar", "View", lambda: self.pages_btn.toggle(), "", in_pdf),
            Command("Toggle the right panel", "View", lambda: self.right_btn.toggle(), "", in_pdf),
            Command("Reading mode", "View", self.toggle_reading, "F11", in_pdf),
            Command("Zoom in", "View", lambda: self.viewer.zoom_step(1), "Ctrl++", in_pdf),
            Command("Zoom out", "View", lambda: self.viewer.zoom_step(-1), "Ctrl+-", in_pdf),
            Command("Fit page", "View", self.viewer.fit_page, "", in_pdf),
            Command("Fit width", "View", self.viewer.fit_width, "", in_pdf),
        ]
        for name, label, key in (("select", "Select", "V"), ("hand", "Hand (pan)", "G"), ("pen", "Pen", "P"),
                                 ("highlighter", "Highlighter", "H"), ("text", "Text box", "T"), ("rect", "Rectangle", "R"),
                                 ("ellipse", "Circle", "O"), ("arrow", "Arrow", "A"), ("line", "Line", "L"),
                                 ("underline", "Underline", "U"), ("strike", "Strike-through", "S"),
                                 ("note", "Sticky note", "N"), ("comment", "Comment", "C"), ("step", "Step marker", "M"),
                                 ("eraser", "Eraser", "E")):
            cmds.append(Command(f"{label} tool", "Tools", tool(name), key, in_pdf))
        return cmds

    def toggle_whiteboard_workspace(self) -> None:
        """Switch the centre area between the PDF and the whiteboard."""
        if self.centre_stack.currentWidget() is self.whiteboard:
            self.show_pdf_workspace()
        else:
            self.show_whiteboard()

    def _on_mode_changed(self, key: str) -> None:
        if key == "board":
            self.show_whiteboard()
        else:
            self.show_pdf_workspace()

    def show_pdf_workspace(self) -> None:
        if self.doc:
            self.centre_stack.setCurrentWidget(self.stage)
        self.topbar.set_mode("pdf")

    def show_whiteboard(self, index: int | None = None) -> None:
        if not self.doc:
            self.topbar.set_mode("pdf")
            self.toast.show_text("Open a PDF first")
            return
        self.centre_stack.setCurrentWidget(self.whiteboard)
        self.topbar.set_mode("board")
        if index is not None:
            self.whiteboard.show_board(index)

    def _active_toolbar(self) -> ToolBar:
        return self.whiteboard.toolbar if self.centre_stack.currentWidget() is self.whiteboard else self.toolbar

    def _request_board(self, title: str, then) -> None:
        if self.boards.full():
            self.toast.show_text("A room can have up to 20 whiteboards")
            return
        if self.session.active and self.session.joined_once and not self.session.can_draw():
            self.toast.show_text("The host made this room view-only for you")
            return
        self.boards.add(title, online=self.session.active and self.session.joined_once, then=then)

    def start_snip(self) -> None:
        """Go back to the PDF with the snip tool active: drag a box around a question."""
        self.show_pdf_workspace()
        self.toolbar.select_tool_by_name("snip")
        self.toast.show_text("Drag a box around the question to send it to the whiteboard")

    def _on_snip(self, page: int, x0: float, y0: float, x1: float, y1: float) -> None:
        if not self.doc:
            return
        if self.session.active and self.session.joined_once and not self.session.can_draw():
            self.toast.show_text("The host made this room view-only for you")
            return
        dlg = SendToBoardDialog(self, f"Question · page {page + 1}")
        if not dlg.exec():
            return
        title, track = dlg.values()
        try:
            data, _, _ = encode_snip(self.doc.render_region(page, (x0, y0, x1, y1)))
        except Exception as exc:
            QMessageBox.warning(self, "Couldn't snip the question", str(exc) or "That area couldn't be captured.")
            return

        def place(idx: int) -> None:
            self.whiteboard.add_question_image(idx, data, x1 - x0, y1 - y0)
            self.show_whiteboard(idx)
            self.toast.show_text("Question sent to the whiteboard - start solving!")
        self._request_board(title, place)
        if track:
            self.questions.add(title, page)
        self.toolbar.select_tool_by_name("select")

    def toggle_right_panel(self, hidden: bool) -> None:
        """Collapse/restore the collaboration and notes panel without changing the current tab."""
        self._right_hidden = hidden
        self.right_panel.setVisible(not hidden and not self._reading)
        self.right_btn.setIcon(icon("panel-right" if hidden else "panel-right-close", size=18))
        self.right_btn.setToolTip("Show right panel" if hidden else "Hide right panel (chat, people, questions and notes)")
        if not self._reading:
            self.toast.show_text("Right panel hidden" if hidden else "Right panel shown")

    def _bookmark_key(self) -> str:
        return "bookmarks:" + self.doc_sha if self.doc and self.doc_sha else "bookmarks:none"

    def _bookmark_pages(self) -> list[int]:
        try:
            raw = json.loads(self.db.get_setting(self._bookmark_key(), "[]"))
            if isinstance(raw, list):
                maximum = self.doc.page_count - 1 if self.doc else -1
                return sorted({int(p) for p in raw if str(p).lstrip("-").isdigit() and 0 <= int(p) <= maximum})
        except (ValueError, TypeError):
            pass
        return []

    def add_bookmark(self) -> None:
        if not self.doc:
            QMessageBox.information(self, "Bookmarks", "Open a PDF first.")
            return
        pages = self._bookmark_pages()
        if self.canvas.page in pages:
            pages.remove(self.canvas.page)
            message = f"Removed bookmark for page {self.canvas.page + 1}"
        else:
            pages.append(self.canvas.page)
            pages.sort()
            message = f"Bookmarked page {self.canvas.page + 1}"
        self.db.set_setting(self._bookmark_key(), json.dumps(pages))
        self.refresh_bookmarks()
        self.toast.show_text(message)

    def clear_bookmarks(self) -> None:
        if not self.doc:
            return
        self.db.set_setting(self._bookmark_key(), "[]")
        self.refresh_bookmarks()
        self.toast.show_text("Bookmarks cleared for this PDF")

    def refresh_bookmarks(self) -> None:
        self.bookmark_list.clear()
        if not self.doc:
            return
        pages = self._bookmark_pages()
        for page in pages:
            item = QListWidgetItem(f"Page {page + 1}")
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.bookmark_list.addItem(item)
        if not pages:
            self.bookmark_list.addItem("No saved pages yet. Press Ctrl+B on a page to save it.")

    def open_search(self) -> None:
        if not self.doc:
            QMessageBox.information(self, "Search PDF", "Open a PDF first.")
            return
        if self._search_dialog is None:
            self._search_dialog = SearchDialog(self)
            self._search_dialog.search_requested.connect(self._start_pdf_search)
            self._search_dialog.page_selected.connect(lambda page: self.goto_page(page))
            self._search_dialog.finished.connect(lambda *_: setattr(self, "_search_dialog", None))
        self._search_dialog.show()
        self._search_dialog.raise_()
        self._search_dialog.activateWindow()
        self._search_dialog.query.setFocus()

    def _start_pdf_search(self, query: str) -> None:
        if not self.doc or not self._search_dialog:
            return
        worker = PdfSearchThread(self.doc.path, query, self)
        self._search_threads.append(worker)
        dialog = self._search_dialog
        worker.progress.connect(dialog.set_progress)
        worker.results_ready.connect(dialog.set_results)
        worker.failed.connect(dialog.set_error)
        worker.finished.connect(lambda w=worker: self._search_threads.remove(w) if w in self._search_threads else None)
        worker.start()

    def _build_shortcuts(self) -> None:
        def sc(keys: str, fn) -> None:
            s = QShortcut(QKeySequence(keys), self)
            s.activated.connect(lambda: self.stack.currentIndex() == 1 and fn())
        sc("Ctrl+S", self.save_annotated)
        sc("Ctrl+G", self.quick_goto_page)
        sc("Ctrl+F", self.open_search)
        sc("Ctrl+B", self.add_bookmark)
        sc("Ctrl+Z", self.editor.undo)
        sc("Ctrl+Y", self.editor.redo)
        sc("Ctrl+Shift+Z", self.editor.redo)
        sc("PgDown", lambda: self._flip(1))
        sc("PgUp", lambda: self._flip(-1))
        sc("Ctrl+=", lambda: self.viewer.zoom_step(1))
        sc("Ctrl++", lambda: self.viewer.zoom_step(1))
        sc("Ctrl+-", lambda: self.viewer.zoom_step(-1))
        sc("F11", self.toggle_reading)
        sc("Ctrl+Shift+W", self.toggle_whiteboard_workspace)
        for key, tool in TOOL_KEYS.items():
            sc(key, lambda t=tool: self._active_toolbar().select_tool_by_name(t))
        pal = QShortcut(QKeySequence("Ctrl+K"), self)             # works on every screen
        pal.activated.connect(self.open_command_palette)
        s = QShortcut(QKeySequence("Ctrl+O"), self)
        s.activated.connect(self.open_pdf_dialog)
        self._esc = QShortcut(QKeySequence("Esc"), self)      # only active in reading mode, so Esc still
        self._esc.setEnabled(False)                           # cancels text editing / deselects otherwise
        self._esc.activated.connect(self.toggle_reading)

    # ================================================================ wiring
    def _wire(self) -> None:
        h = self.home
        h.open_pdf.connect(self.open_pdf_dialog)
        h.create_room.connect(self.create_room)
        h.join_room.connect(self.join_room)
        h.open_recent.connect(self.open_pdf)
        h.forget_recent.connect(lambda p: (self.db.remove_recent(p), self.refresh_recent()))
        self.topbar.home.connect(self.go_home)
        self.topbar.mode_changed.connect(self._on_mode_changed)
        self.topbar.copy_code.connect(self.copy_code)

        self.toolbar.tool_changed.connect(self.canvas.set_tool)
        self.toolbar.symbol_chosen.connect(self.canvas.insert_text)
        self.canvas.snip_requested.connect(self._on_snip)
        # the PDF canvas and the whiteboard canvas behave identically: same permissions, identity, cursors, live strokes
        for tbar, c in ((self.toolbar, self.canvas), (self.whiteboard.toolbar, self.whiteboard.canvas)):
            tbar.undo.connect(self.editor.undo)
            tbar.redo.connect(self.editor.redo)
            self.editor.history_changed.connect(lambda t=tbar: t.set_history(self.editor.can_undo(), self.editor.can_redo()))
            c.style_for = tbar.style_for
            c.identity = self.editor.identity
            c.can_draw = self.session.can_draw
            c.can_modify = self.session.can_modify
            c.cursors = self.session.cursors
            c.remote_live = lambda page: [v for v in self.session.live.values() if v["page"] == page]
            c.cursor_moved.connect(self.session.send_cursor)
            c.live_update.connect(self.session.send_live)
            c.denied.connect(self.toast.show_text)

        vb = self.viewbar
        vb.prev_page.connect(lambda: self.goto_page(self.canvas.page - 1))
        vb.next_page.connect(lambda: self.goto_page(self.canvas.page + 1))
        vb.goto.connect(self.goto_page)
        vb.zoom_in.connect(lambda: self.viewer.zoom_step(1))
        vb.zoom_out.connect(lambda: self.viewer.zoom_step(-1))
        vb.fit_page.connect(self.viewer.fit_page)
        vb.fit_width.connect(self.viewer.fit_width)
        vb.reading_mode.connect(self.toggle_reading)
        vb.follow_toggled.connect(self._follow_toggled)
        self.viewer.zoom_changed.connect(vb.set_zoom)
        self.viewer.flip.connect(self._wheel_flip)
        self.pages.page_selected.connect(lambda p: self.goto_page(p))
        self.page_filter.textChanged.connect(self._filter_pages)
        self.outline_tree.itemActivated.connect(self._outline_activated)
        self.bookmark_list.itemActivated.connect(self._bookmark_activated)

        s = self.session
        self.editor.op.connect(s.send_op)
        self.questions.op.connect(s.send_question_op)
        self.boards.op.connect(s.send_question_op)
        s.status.connect(self._on_status)
        s.entered.connect(self._on_entered)
        s.resynced.connect(self._apply_room_ui)
        s.members_changed.connect(self._apply_room_ui)
        s.settings_changed.connect(self._apply_room_ui)
        s.chat_added.connect(self.chat.add)
        s.chat_loaded.connect(self.chat.load)
        s.timer_changed.connect(self._on_timer)
        s.follow_page.connect(lambda p: self.goto_page(p, from_user=False))
        s.pdf_progress.connect(self._on_pdf_progress)
        s.pdf_ready.connect(self._on_pdf_ready)
        s.pdf_failed.connect(lambda m: QMessageBox.warning(self, "PDF transfer problem", m))
        s.notice.connect(self._notice)
        s.fatal.connect(self._on_fatal)
        s.cursors_changed.connect(self.canvas.update)
        s.cursors_changed.connect(self.whiteboard.canvas.update)
        s.live_changed.connect(lambda p: (p == self.canvas.ap and self.canvas.update(),
                                          p == self.whiteboard.canvas.ap and self.whiteboard.canvas.update()))

        self.users.kick.connect(lambda uid: s.host_action("kick", id=uid))
        self.chat.send.connect(s.send_chat)
        self.timer.action.connect(self._timer_action)
        self.questions_panel.current_page = lambda: self.canvas.page
        self.questions_panel.can_edit = self.session.can_draw
        self.questions_panel.goto_page.connect(self.goto_page)

        for sig in (self.store.changed, self.store.removed, self.store.reset, self.questions.changed, self.boards.changed):
            sig.connect(lambda *_: self._save_timer.start())

    # ================================================================ documents
    def refresh_recent(self) -> None:
        self.home.set_recent(self.db.recent(), cache_dir() / "thumbs")

    def open_pdf_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", self.db.get_setting("last_dir", ""), "PDF files (*.pdf)")
        if path:
            self.open_pdf(path)

    def open_pdf(self, path: str) -> None:
        if self.session.active:
            if QMessageBox.question(self, "Leave room?", "Opening another PDF will leave the current room. Continue?") \
                    != QMessageBox.StandardButton.Yes:
                return
            self.leave_room(confirm=False)
        self._load(path)

    def _load(self, path: str, room: bool = False) -> bool:
        try:
            doc = PdfDocument(path)
        except PdfError as e:
            QMessageBox.warning(self, "Can't open this PDF", str(e))
            if not room:
                self.db.remove_recent(path)
                self.refresh_recent()
            return False
        except OSError:
            QMessageBox.warning(self, "Can't open this PDF", "The file couldn't be read. Was it moved or deleted?")
            return False
        self._save_now()
        old, self.doc = self.doc, doc
        self.canvas.set_document(doc)
        if old:
            old.close()
        self.pages.set_document(doc)
        self.page_filter.clear()
        self.doc_sha = file_sha256(path)
        self._populate_outline()
        self._load_study_notes()
        self.topbar.set_doc(doc.name)
        self.setWindowTitle(f"StudyPDF · {theme.THEME_NAME} - {doc.name}")
        start_page = 0
        if not room:
            self.db.set_setting("last_dir", str(Path(path).parent))
            self.db.add_recent(path, doc.name, self.doc_sha, doc.page_count)
            try:                                   # first-page thumbnail for the home screen gallery
                tdir = cache_dir() / "thumbs"
                tdir.mkdir(exist_ok=True)
                doc.thumbnail(0, 220).save(str(tdir / f"{self.doc_sha}.png"))
            except Exception:
                pass
            self.refresh_recent()
            self.store.replace_all([])
            self.questions.replace_all([])
            self.boards.replace_all([])
            self.editor.clear_history()
            saved = self.db.load_session("doc:" + self.doc_sha)
            if saved:
                self.boards.replace_all(saved.get("boards", []))
                self.store.replace_all(saved.get("annotations", []))
                self.questions.replace_all(saved.get("questions", []))
                start_page = min(int(saved.get("page", 0)), doc.page_count - 1)
                self.toast.show_text("Restored your previous annotations")
        else:
            start_page = min(self.session.members.get(self.session.host_id, {}).get("page", 0), doc.page_count - 1)
        self.centre_stack.setCurrentIndex(0)
        self.topbar.set_mode("pdf")
        self.stack.setCurrentIndex(1)
        self.goto_page(start_page, from_user=False)
        QTimer.singleShot(50, self.viewer.fit_width)
        return True

    def go_home(self) -> None:
        if self.session.active and QMessageBox.question(self, "Leave room?", "Go back home and leave the study room?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._save_now()
        if self.session.active:
            self.session.leave()
        self._close_document()
        self._apply_room_ui()
        self.refresh_recent()
        self.stack.setCurrentIndex(0)

    def _close_document(self) -> None:
        old, self.doc = self.doc, None
        self.canvas.set_document(None)
        self.pages.set_document(None)
        self.outline_tree.clear()
        self.bookmark_list.clear()
        self.study_notes.blockSignals(True)
        self.study_notes.clear()
        self.study_notes.blockSignals(False)
        if old:
            old.close()
        self.store.replace_all([])
        self.questions.replace_all([])
        self.boards.replace_all([])
        self.editor.clear_history()
        self.chat.clear()
        self.setWindowTitle(f"StudyPDF · {theme.THEME_NAME}")

    def _filter_pages(self, query: str) -> None:
        """Filter page rows without rendering thumbnails; supports page lists and ranges."""
        for row in range(self.pages.count()):
            item = self.pages.item(row)
            item.setHidden(not page_matches_filter(row + 1, query))

    def _populate_outline(self) -> None:
        self.outline_tree.clear()
        if not self.doc:
            return
        stack: list[QTreeWidgetItem] = []
        entries = self.doc.outline()
        for entry in entries:
            if len(entry) < 3:
                continue
            level, title, page_num = int(entry[0]), str(entry[1]), int(entry[2])
            if page_num < 1 or page_num > self.doc.page_count:
                continue
            node = QTreeWidgetItem([title or f"Page {page_num}"])
            node.setData(0, Qt.ItemDataRole.UserRole, page_num - 1)
            node.setToolTip(0, f"Page {page_num}")
            while stack and len(stack) >= level:
                stack.pop()
            if stack:
                stack[-1].addChild(node)
            else:
                self.outline_tree.addTopLevelItem(node)
            stack.append(node)
        if not entries:
            item = QTreeWidgetItem(["No table of contents in this PDF"])
            item.setDisabled(True)
            self.outline_tree.addTopLevelItem(item)
        self.outline_tree.expandToDepth(0)

    def _outline_activated(self, item, *_args) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self.goto_page(int(page))
            self.nav_tabs.setCurrentWidget(self.outline_tree)

    def _bookmark_activated(self, item, *_args) -> None:
        page = item.data(Qt.ItemDataRole.UserRole)
        if page is not None:
            self.goto_page(int(page))
            self.nav_tabs.setCurrentWidget(self.bookmark_list)

    def quick_goto_page(self) -> None:
        if not self.doc:
            return
        from PySide6.QtWidgets import QInputDialog
        page, ok = QInputDialog.getInt(self, "Go to page", f"Page number (1–{self.doc.page_count}):",
                                       self.canvas.page + 1, 1, self.doc.page_count)
        if ok:
            self.goto_page(page - 1)

    def _notes_key(self) -> str:
        return "study_notes:" + self.doc_sha if self.doc_sha else "study_notes:none"

    def _load_study_notes(self) -> None:
        self.study_notes.blockSignals(True)
        self.study_notes.setPlainText(self.db.get_setting(self._notes_key(), ""))
        self.study_notes.blockSignals(False)
        self._migrate_old_solving_workspace()

    def _queue_study_notes_save(self) -> None:
        if self.doc:
            self._notes_timer.start()

    def _save_study_notes(self) -> None:
        if self.doc:
            self.db.set_setting(self._notes_key(), self.study_notes.toPlainText())

    def _migrate_old_solving_workspace(self) -> None:
        """Carry text saved in the old Solve tab over to Notes, once."""
        key = "solving_workspace:" + self.doc_sha
        try:
            data = json.loads(self.db.get_setting(key, "{}") or "{}")
        except ValueError:
            return
        merged = merge_old_solving_data(self.study_notes.toPlainText(), data)
        if merged is not None:
            self.study_notes.setPlainText(merged)          # textChanged queues the save
            self.db.set_setting(key, "{}")

    def goto_page(self, page: int, from_user: bool = True, scroll: str | None = None) -> None:
        if not self.doc or not 0 <= page < self.doc.page_count:
            return
        changed = page != self.canvas.page
        self.canvas.set_page(page)
        if self.pages.item(page) and self.pages.item(page).isHidden():
            self.page_filter.clear()
        self.pages.set_current(page)
        self.viewbar.set_page(page, self.doc.page_count)
        if scroll:
            self.viewer.scroll_to(scroll)
        self.refresh_bookmarks()
        if changed:
            self.session.send_page(page)
            if from_user and self.session.active and not self.session.is_host and self.viewbar.follow.isChecked():
                self.viewbar.follow.setChecked(False)
            self._save_timer.start()

    def _flip(self, delta: int) -> None:
        """PageUp / PageDown: next page of the PDF, or next board when the whiteboard is showing."""
        if self.centre_stack.currentWidget() is self.whiteboard:
            self.whiteboard.step_board(delta)
        else:
            self.goto_page(self.canvas.page + delta)

    def _wheel_flip(self, direction: int) -> None:
        if self.doc and 0 <= self.canvas.page + direction < self.doc.page_count:
            self.goto_page(self.canvas.page + direction, scroll="top" if direction > 0 else "bottom")

    def _follow_toggled(self, on: bool) -> None:
        self.session.follow_host = on
        self.viewbar.follow.setText("Following host" if on else "Follow host")
        if on:
            host = self.session.members.get(self.session.host_id)
            if host:
                self.goto_page(host["page"], from_user=False)

    def toggle_reading(self) -> None:
        self._reading = not self._reading
        self._esc.setEnabled(self._reading)
        for w in (self.topbar, self.toolbar):
            w.setVisible(not self._reading)
        self.right_panel.setVisible(not self._reading and not self._right_hidden)
        self.left_panel.setVisible(self.pages_btn.isChecked() and not self._reading)
        if self._reading:
            self.showFullScreen()
            self.toast.show_text("Reading mode - press F11 or Esc to exit")
        else:
            self.showNormal()

    # ================================================================ rooms
    def _settings_name(self) -> str:
        return self.db.get_setting("user_name", "")

    def create_room(self) -> None:
        if self.session.active:
            QMessageBox.information(self, "Already in a room", "Leave the current room first.")
            return
        dlg = CreateRoomDialog(self, self._settings_name(), self.db.get_setting("server", DEFAULT_SERVER),
                               self.doc.path if self.doc else "")
        if not dlg.exec():
            return
        v = dlg.values()
        if not self.doc or os.path.abspath(v["pdf"]) != os.path.abspath(self.doc.path):
            if not self._load(v["pdf"]):
                return
        assert self.doc
        size = os.path.getsize(self.doc.path)
        if size > MAX_PDF_BYTES:
            QMessageBox.warning(self, "PDF too large", f"Rooms support PDFs up to {MAX_PDF_BYTES // 2**20} MB.")
            return
        self.db.set_setting("user_name", v["name"])
        self.db.set_setting("server", v["server"])
        info = {"name": self.doc.name, "size": size, "pages": self.doc.page_count, "sha256": self.doc_sha}
        self._show_connecting("Creating room…")
        self.session.start_create(v["server"], v["room"], v["name"], v["password"], self.doc.path, info)

    def join_room(self) -> None:
        if self.session.active:
            QMessageBox.information(self, "Already in a room", "Leave the current room first.")
            return
        dlg = JoinRoomDialog(self, self._settings_name(), self.db.get_setting("server", DEFAULT_SERVER))
        if not dlg.exec():
            return
        v = dlg.values()
        self.db.set_setting("user_name", v["name"])
        self.db.set_setting("server", v["server"])
        if self.doc:
            self._save_now()
            self._close_document()
        self._show_connecting("Joining room…")
        self.session.start_join(v["server"], v["code"], v["name"], v["password"])

    def _show_connecting(self, text: str) -> None:
        dlg = QProgressDialog(text, "Cancel", 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.canceled.connect(self._cancel_connect)
        dlg.show()
        self._connecting = dlg

    def _cancel_connect(self) -> None:
        if self._connecting and not self.session.joined_once:
            self.session.leave()
        self._connecting = None

    def _hide_connecting(self) -> None:
        if self._connecting:
            self._connecting.canceled.disconnect()
            self._connecting.close()
            self._connecting = None

    def leave_room(self, confirm: bool = True) -> None:
        if not self.session.active:
            return
        if confirm and QMessageBox.question(self, "Leave room?", "Leave this study room? You keep a local copy of the annotations.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.session.leave()
        self._apply_room_ui()

    def copy_code(self) -> None:
        if self.session.active and self.session.code:
            QApplication.clipboard().setText(self.session.code)
            self.toast.show_text(f"Room code {self.session.code} copied")

    def room_settings(self) -> None:
        if self.session.active and self.session.joined_once:
            RoomSettingsDialog(self, self.session).exec()
        else:
            QMessageBox.information(self, "Room settings", "Create or join a study room to use room settings.")

    # ---- session callbacks ------------------------------------------------------------------
    def _on_entered(self, msg: dict[str, Any]) -> None:
        self._hide_connecting()
        self.timer.local = False
        self.chat.system(f"You joined {self.session.room_name}")
        if msg.get("created"):
            self._created_dialog = RoomCreatedDialog(self, self.session.code)
            self._created_dialog.show()
        else:
            self.wait_label.setText("Waiting for the host's PDF…" if not msg["pdf"].get("ready") else "Downloading the PDF…")
            self.wait_bar.setValue(0)
            self.centre_stack.setCurrentIndex(1)
            self.stack.setCurrentIndex(1)
        self._apply_room_ui()

    def _on_status(self, s: str) -> None:
        if s == "reconnecting":
            self._banner_was_down = True
            self._banner("Connection lost… Reconnecting…", theme.WARN)
        elif s == "syncing":
            self._banner("Connected ✓  Syncing changes…", theme.GOOD)
        elif s == "online":
            if self._banner_was_down:
                self._banner_was_down = False
                self._banner("Connected ✓", theme.GOOD)
                QTimer.singleShot(2200, lambda: self.session.client.online and self.banner.hide())
            else:
                self.banner.hide()
        elif s == "connecting" and self.session.joined_once:
            self._banner("Connecting…", theme.WARN)

    def _banner(self, text: str, color: str) -> None:
        self.banner.setText(text)
        self.banner.setStyleSheet(f"background: {color}; color: #ffffff; font-weight: 600;")
        self.banner.show()

    def _on_pdf_progress(self, done: int, total: int, kind: str) -> None:
        if kind == "upload" and self._created_dialog:
            self._created_dialog.set_progress(done, total)
        elif kind == "download":
            self.wait_label.setText("Downloading the PDF…")
            self.wait_bar.setValue(int(done / max(total, 1) * 100))

    def _on_pdf_ready(self, path: str, name: str) -> None:
        if self._load(path, room=True):
            self.doc.name = name  # type: ignore[union-attr]
            self.topbar.set_doc(name)
            self.toast.show_text("Document received - happy studying!")
        self._apply_room_ui()

    def _on_timer(self, state: dict[str, Any], finished: bool) -> None:
        self.timer.set_state(state)
        if finished:
            QApplication.beep()
            self.toast.show_text("Timer finished" + (" - break time!" if state.get("phase") == "break" else ""))

    def _timer_action(self, name: str, kw: dict[str, Any]) -> None:
        if self.session.active:
            self.session.timer_action(name, **kw)
            return
        t, now = self.timer.state, time.time()
        if name == "start":
            t.start(now)
        elif name == "pause":
            t.pause(now)
        elif name == "reset":
            t.reset()
        elif name == "config":
            t.configure(kw["mode"], kw["focus_min"], kw["break_min"])
        self.timer.set_state(t.to_dict(now))

    def _notice(self, text: str) -> None:
        self.toast.show_text(text)
        self.chat.system(text)

    def _on_fatal(self, code: str, text: str) -> None:
        self._hide_connecting()
        if self._created_dialog:
            self._created_dialog.close()
            self._created_dialog = None
        self.session.leave()
        self.timer.local = True
        QMessageBox.warning(self, "Study room", text)
        if code == "kicked" or not self.doc:
            self._close_document()
            self.stack.setCurrentIndex(0)
        self._apply_room_ui()

    def _apply_room_ui(self) -> None:
        s = self.session
        active = s.active and s.joined_once
        self.tabs.setTabVisible(self.tab_people, active)
        self.topbar.set_room(s.code if active else None, s.members)
        self.topbar.room_chip.setToolTip("Live Study Together is connected — click to copy room code" if active else "Live Study Together room code")
        self.chat.me = s.me if active else ""
        self.act_copy.setEnabled(active)
        self.act_settings.setEnabled(active)
        self.act_leave.setEnabled(active)
        self.study_btn.setText("  Invite friends" if active else "  Study together")
        if active:
            self.users.set_members(s.members, s.me, s.is_host)
            self.tabs.setTabText(self.tab_people, f"People · {sum(1 for x in s.members.values() if x['connected'])}")
            self.chat.set_enabled(s.can_chat(), "" if s.can_chat() else "The host turned chat off.")
            self.timer.set_enabled_controls(s.can_timer())
            self.timer.local = False
            show_follow = s.settings["sync_pages"] and not s.is_host
            self.viewbar.follow.setVisible(show_follow)
            if self._created_dialog:
                self._created_dialog.set_friends(max(0, sum(1 for m in s.members.values() if m["connected"]) - 1))
        else:
            self.banner.hide()
            self.chat.set_enabled(False, "Join or create a room to chat.")
            self.timer.set_enabled_controls(True)
            self.timer.local = True
            self.viewbar.follow.setVisible(False)
        self.toolbar.set_drawing_enabled(s.can_draw())
        self.whiteboard.toolbar.set_drawing_enabled(s.can_draw())
        self.canvas.update()

    # ================================================================ saving / exporting
    def _session_key(self) -> str:
        return f"room:{self.session.code}" if self.session.active and self.session.code else "doc:" + self.doc_sha

    def _save_now(self) -> None:
        if not self.doc:
            return
        data = {"annotations": self.store.all(), "questions": self.questions.ordered(), "page": self.canvas.page,
                "boards": list(self.boards.items)}
        self.db.save_session(self._session_key(), data)
        self._save_study_notes()
        if self.session.active:                       # also keep a local copy keyed by document
            self.db.save_session("doc:" + self.doc_sha, data)

    def _export(self, flat: bool) -> None:
        if not self.doc:
            return
        if not self.session.can_download():
            QMessageBox.information(self, "Not allowed", "The host disabled saving and exporting for members.")
            return
        stem = Path(self.doc.path).stem
        default = str(Path(self.doc.path).with_name(stem + ("_final.pdf" if flat else "_annotated.pdf")))
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF" if flat else "Save annotated copy", default, "PDF files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        prog = QProgressDialog("Exporting…", "Cancel", 0, 100, self)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(400)

        def step(done: int, total: int) -> bool:
            prog.setValue(int(done / max(total, 1) * 100))
            QApplication.processEvents()
            return not prog.wasCanceled()
        try:
            (export_flattened if flat else export_annotated)(self.doc, self.store.all(), path, progress=step)
        except PdfError as e:
            prog.close()
            QMessageBox.warning(self, "Export", str(e))
            return
        except Exception as e:
            prog.close()
            QMessageBox.warning(self, "Export failed", f"Couldn't write the PDF.\n\n{e}")
            return
        prog.close()
        self.toast.show_text(f"Saved {Path(path).name}")

    def save_annotated(self) -> None:
        self._export(False)

    def export_flat(self) -> None:
        self._export(True)

    def save_session_file(self) -> None:
        if not self.doc:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save session", str(Path(self.doc.path).with_suffix(".studypdf")),
                                              "StudyPDF session (*.studypdf)")
        if not path:
            return
        data = {"version": 1, "pdf_name": self.doc.name, "pdf_path": self.doc.path, "pdf_sha": self.doc_sha,
                "annotations": self.store.all(), "questions": self.questions.ordered(), "page": self.canvas.page,
                "boards": list(self.boards.items)}
        try:
            Path(path).write_text(json.dumps(data), encoding="utf-8")
            self.toast.show_text("Session saved")
        except OSError as e:
            QMessageBox.warning(self, "Save failed", str(e))

    def open_session_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open session", "", "StudyPDF session (*.studypdf)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            anns, qs = list(data["annotations"]), list(data.get("questions", []))
        except (OSError, ValueError, KeyError, TypeError):
            QMessageBox.warning(self, "Open session", "That session file is damaged or not a StudyPDF session.")
            return
        pdf = data.get("pdf_path", "")
        if not os.path.exists(pdf):
            pdf, _ = QFileDialog.getOpenFileName(self, f"Locate {data.get('pdf_name', 'the PDF')}", "", "PDF files (*.pdf)")
            if not pdf:
                return
        if self.session.active:
            self.leave_room(confirm=False)
        if not self._load(pdf):
            return
        if file_sha256(pdf) != data.get("pdf_sha"):
            QMessageBox.information(self, "Different PDF", "This PDF differs from the one the session was saved with; "
                                                           "annotations may not line up.")
        self.boards.replace_all(list(data.get("boards", [])))
        self.store.replace_all(anns)
        self.questions.replace_all(qs)
        self.goto_page(min(int(data.get("page", 0)), self.doc.page_count - 1), from_user=False)  # type: ignore[union-attr]

    # ================================================================ window events
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".pdf") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(".pdf"):
                self.open_pdf(u.toLocalFile())
                break

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.show_text(self.toast.text())

    def closeEvent(self, e) -> None:
        for worker in list(self._search_threads):
            worker.cancel()
        for worker in list(self._search_threads):
            worker.wait()
        self._save_now()
        self.db.set_setting("window_geometry", bytes(self.saveGeometry().toBase64()).decode())
        if self.session.active:
            self.session.leave()
        e.accept()
