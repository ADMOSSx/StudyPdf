"""Search dialog for searching text across a PDF without blocking the UI."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QProgressBar, QVBoxLayout)


class SearchDialog(QDialog):
    search_requested = Signal(str)
    page_selected = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Search PDF")
        self.resize(620, 480)
        root = QVBoxLayout(self)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search text in this PDF…")
        self.query.returnPressed.connect(self._search)
        self.search_btn = QPushButton("Search")
        self.search_btn.setProperty("kind", "primary")
        self.search_btn.clicked.connect(self._search)
        row.addWidget(self.query, 1)
        row.addWidget(self.search_btn)
        root.addLayout(row)
        self.status = QLabel("Search text-based PDFs. Scanned pages require OCR text to be searchable.")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        root.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        root.addWidget(self.progress)
        self.results = QListWidget()
        self.results.itemDoubleClicked.connect(self._open_item)
        root.addWidget(self.results, 1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

    def _search(self) -> None:
        q = self.query.text().strip()
        if not q:
            self.status.setText("Type something to search for.")
            self.query.setFocus()
            return
        self.results.clear()
        self.progress.setValue(0)
        self.progress.show()
        self.search_btn.setEnabled(False)
        self.status.setText("Searching pages in the background…")
        self.search_requested.emit(q)

    def set_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)
        self.status.setText(f"Searching… page {done:,} of {total:,}")

    def set_results(self, query: str, results: list[dict]) -> None:
        self.search_btn.setEnabled(True)
        self.progress.hide()
        for result in results:
            page = int(result["page"])
            item = QListWidgetItem(f"Page {page + 1}:  {result['snippet']}")
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.results.addItem(item)
        if results:
            self.status.setText(f"Found {len(results):,} match(es) for “{query}”. Double-click a result to open that page.")
        else:
            self.status.setText(f"No matches found for “{query}”. Scanned PDFs need OCR text to search.")

    def set_error(self, message: str) -> None:
        self.search_btn.setEnabled(True)
        self.progress.hide()
        self.status.setText(message)

    def _open_item(self, item: QListWidgetItem) -> None:
        self.page_selected.emit(int(item.data(Qt.ItemDataRole.UserRole)))
        self.accept()
