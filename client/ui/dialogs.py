"""Create / join / created / settings dialogs."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout)

from client.collaboration.session import SessionController
from client.config import DEFAULT_SERVER
from shared.protocol import normalize_room_code

PRESETS = {
    "Everyone can edit": {"can_draw": True, "can_edit_others": True, "can_chat": True},
    "Everyone can annotate (edit only their own)": {"can_draw": True, "can_edit_others": False, "can_chat": True},
    "Host only can edit": {"can_draw": False, "can_edit_others": False, "can_chat": True},
    "View only (chat off)": {"can_draw": False, "can_edit_others": False, "can_chat": False},
}


def normalize_url(text: str) -> str:
    text = text.strip() or DEFAULT_SERVER
    if "://" not in text:
        text = "ws://" + text
    url = QUrl(text)
    if url.port() == -1 and url.scheme() == "ws":
        url.setPort(8765)
    return url.toString()


def _title(dialog: QDialog, title: str, subtitle: str = "") -> QVBoxLayout:
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(440)
    lay = QVBoxLayout(dialog)
    lay.setContentsMargins(26, 24, 26, 22)
    lay.setSpacing(12)
    h = QLabel(title)
    h.setObjectName("h1")
    h.setStyleSheet("font-size: 22px;")
    lay.addWidget(h)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("muted")
        s.setWordWrap(True)
        lay.addWidget(s)
    return lay


class CreateRoomDialog(QDialog):
    def __init__(self, parent, default_name: str, default_server: str, pdf_path: str = "") -> None:
        super().__init__(parent)
        lay = _title(self, "Create Study Room", "Everyone who joins sees this PDF and solves it together with you.")
        form = QFormLayout()
        form.setSpacing(10)
        self.room = QLineEdit("Study session")
        self.name = QLineEdit(default_name)
        self.server = QLineEdit(default_server or DEFAULT_SERVER)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Optional")
        self.pdf = QLineEdit(pdf_path)
        self.pdf.setPlaceholderText("Choose the PDF to share…")
        self.pdf.setReadOnly(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        pdf_row = QHBoxLayout()
        pdf_row.addWidget(self.pdf, 1)
        pdf_row.addWidget(browse)
        form.addRow("Room name", self.room)
        form.addRow("Your name", self.name)
        form.addRow("PDF", pdf_row)
        form.addRow("Server", self.server)
        form.addRow("Password", self.password)
        lay.addLayout(form)
        self.err = QLabel("")
        self.err.setStyleSheet("color: #dc2626;")
        lay.addWidget(self.err)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Create Room")
        ok.setProperty("kind", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose PDF", "", "PDF files (*.pdf)")
        if path:
            self.pdf.setText(path)

    def _accept(self) -> None:
        if not self.name.text().strip():
            self.err.setText("Please enter your name.")
        elif not self.pdf.text():
            self.err.setText("Choose a PDF to share.")
        else:
            self.accept()

    def values(self) -> dict[str, str]:
        return {"room": self.room.text().strip() or "Study session", "name": self.name.text().strip(),
                "pdf": self.pdf.text(), "server": normalize_url(self.server.text()), "password": self.password.text()}


class JoinRoomDialog(QDialog):
    def __init__(self, parent, default_name: str, default_server: str) -> None:
        super().__init__(parent)
        lay = _title(self, "Join Study Room", "Enter the code your friend shared with you.")
        form = QFormLayout()
        form.setSpacing(10)
        self.code = QLineEdit()
        self.code.setPlaceholderText("ABC-492")
        self.code.setStyleSheet("font-size: 20px; letter-spacing: 3px; padding: 9px 12px;")
        self.code.setMaxLength(8)
        self.name = QLineEdit(default_name)
        self.server = QLineEdit(default_server or DEFAULT_SERVER)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Only if the room has one")
        form.addRow("Room code", self.code)
        form.addRow("Your name", self.name)
        form.addRow("Server", self.server)
        form.addRow("Password", self.password)
        lay.addLayout(form)
        self.err = QLabel("")
        self.err.setStyleSheet("color: #dc2626;")
        lay.addWidget(self.err)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Join Room")
        ok.setProperty("kind", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _accept(self) -> None:
        if not normalize_room_code(self.code.text()):
            self.err.setText("Room codes look like ABC-492.")
        elif not self.name.text().strip():
            self.err.setText("Please enter your name.")
        else:
            self.accept()

    def values(self) -> dict[str, str]:
        return {"code": normalize_room_code(self.code.text()), "name": self.name.text().strip(),
                "server": normalize_url(self.server.text()), "password": self.password.text()}


class RoomCreatedDialog(QDialog):
    def __init__(self, parent, code: str) -> None:
        super().__init__(parent)
        self.code = code
        lay = _title(self, "Room Created!")
        lay.addWidget(QLabel("Room Code"))
        big = QLabel(code)
        big.setObjectName("code")
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(big)
        self.copy = QPushButton("Copy Code")
        self.copy.setProperty("kind", "primary")
        self.copy.clicked.connect(self._copy)
        lay.addWidget(self.copy)
        self.status = QLabel("Uploading the PDF…")
        self.status.setObjectName("muted")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        lay.addWidget(self.status)
        lay.addWidget(self.bar)
        self._dots = 0
        t = QTimer(self, interval=500)
        t.timeout.connect(self._animate)
        t.start()
        self.uploaded = False
        self.friends = 0
        close = QPushButton("Start studying")
        close.clicked.connect(self.accept)
        lay.addWidget(close)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.code)
        self.copy.setText("Copied ✓")

    def set_progress(self, done: int, total: int) -> None:
        self.bar.setValue(int(done / max(total, 1) * 100))
        if done >= total:
            self.uploaded = True
            self.bar.hide()

    def set_friends(self, n: int) -> None:
        self.friends = n

    def _animate(self) -> None:
        self._dots = (self._dots + 1) % 4
        if not self.uploaded:
            return
        self.status.setText(f"{self.friends} friend(s) joined" if self.friends else "Waiting for friends" + "." * self._dots)


class RoomSettingsDialog(QDialog):
    """Host controls. Non-hosts see the same values read-only; the server enforces everything regardless."""

    def __init__(self, parent, session: SessionController) -> None:
        super().__init__(parent)
        self.session = session
        host = session.is_host
        lay = _title(self, "Room Settings",
                     "Control what everyone in the room can do." if host else "Only the host can change these.")
        s = session.settings
        self.preset = QComboBox()
        self.preset.addItems(list(PRESETS))
        self.preset.addItem("Custom")
        self.preset.currentTextChanged.connect(self._apply_preset)
        lay.addWidget(self.preset)
        self.boxes: dict[str, QCheckBox] = {}
        for key, text in (("can_draw", "Members can draw, write, highlight and add notes"),
                          ("can_edit_others", "Members can move or delete other people's annotations"),
                          ("can_chat", "Members can chat"),
                          ("can_control_timer", "Members can control the study timer"),
                          ("allow_download", "Members can save / export the annotated PDF"),
                          ("sync_pages", "Page navigation follows the host")):
            cb = QCheckBox(text)
            cb.setChecked(bool(s.get(key)))
            self.boxes[key] = cb
            lay.addWidget(cb)
        self.lock = QCheckBox("Lock room (nobody new can join)")
        self.lock.setChecked(session.locked)
        lay.addWidget(self.lock)
        self._sync_preset()
        for cb in list(self.boxes.values()):
            cb.toggled.connect(lambda _=False: self._sync_preset())
        danger = QFrame()
        dl = QHBoxLayout(danger)
        dl.setContentsMargins(0, 6, 0, 0)
        clear = QPushButton("Clear annotations")
        reset = QPushButton("Reset document")
        end = QPushButton("End session")
        end.setProperty("kind", "danger")
        clear.clicked.connect(lambda: self._confirm("Clear every annotation for everyone?", lambda: session.host_action("clear_annotations")))
        reset.clicked.connect(lambda: self._confirm("Clear annotations, questions and the timer, and return to page 1?", lambda: session.host_action("reset_document")))
        end.clicked.connect(lambda: self._confirm("End the session for everyone? The room will be deleted.", lambda: session.host_action("end_session")))
        for b in (clear, reset, end):
            dl.addWidget(b)
        lay.addWidget(danger)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close" if not host else "Cancel")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        if host:
            ok = QPushButton("Apply")
            ok.setProperty("kind", "primary")
            ok.clicked.connect(self._apply)
            row.addWidget(ok)
        lay.addLayout(row)
        if not host:
            for w in list(self.boxes.values()) + [self.lock, self.preset, clear, reset, end]:
                w.setEnabled(False)
        danger.setVisible(host)

    def _confirm(self, text: str, fn) -> None:
        if QMessageBox.question(self, "Are you sure?", text) == QMessageBox.StandardButton.Yes:
            fn()
            self.accept()

    def _sync_preset(self) -> None:
        cur = {k: self.boxes[k].isChecked() for k in ("can_draw", "can_edit_others", "can_chat")}
        name = next((n for n, p in PRESETS.items() if all(cur[k] == v for k, v in p.items())), "Custom")
        self.preset.blockSignals(True)
        self.preset.setCurrentText(name)
        self.preset.blockSignals(False)

    def _apply_preset(self, name: str) -> None:
        for k, v in PRESETS.get(name, {}).items():
            self.boxes[k].blockSignals(True)
            self.boxes[k].setChecked(v)
            self.boxes[k].blockSignals(False)

    def _apply(self) -> None:
        self.session.host_action("set_settings", settings={k: cb.isChecked() for k, cb in self.boxes.items()},
                                 locked=self.lock.isChecked())
        self.accept()
