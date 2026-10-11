"""Desktop client entry point."""
from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from client.config import app_dir
from client.ui.main_window import MainWindow
from client.ui.icons import logo_pixmap
from client.ui.theme import apply_theme
from database.client_db import ClientDatabase


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("StudyPDF")
    db = ClientDatabase(app_dir() / "studypdf.db")
    apply_theme(app, db.get_setting("theme", "light"))
    app.setWindowIcon(QIcon(logo_pixmap(256)))
    window = MainWindow(db)
    window.show()
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".pdf"):
        window.open_pdf(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
