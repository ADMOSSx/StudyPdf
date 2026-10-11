"""Background text search for large PDFs. Uses its own PyMuPDF handle per worker."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QThread, Signal


class PdfSearchThread(QThread):
    progress = Signal(int, int)
    results_ready = Signal(str, list)
    failed = Signal(str)

    def __init__(self, path: str | Path, query: str, parent=None) -> None:
        super().__init__(parent)
        self.path = str(path)
        self.query = query.strip()
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        if not self.query:
            self.results_ready.emit(self.query, [])
            return
        matches: list[dict] = []
        try:
            # Do not share the GUI's pymupdf.Document across threads.
            with pymupdf.open(self.path) as doc:
                total = doc.page_count
                needle = self.query.casefold()
                for index in range(total):
                    if self._cancel_requested:
                        break
                    page = doc.load_page(index)
                    text = page.get_text("text") or ""
                    folded = text.casefold()
                    start = 0
                    count = 0
                    while True:
                        pos = folded.find(needle, start)
                        if pos < 0:
                            break
                        left, right = max(0, pos - 55), min(len(text), pos + len(self.query) + 85)
                        snippet = " ".join(text[left:right].split())
                        matches.append({"page": index, "snippet": snippet})
                        count += 1
                        start = pos + max(1, len(needle))
                        if len(matches) >= 1000:
                            break
                    self.progress.emit(index + 1, total)
                    if len(matches) >= 1000:
                        break
            self.results_ready.emit(self.query, matches)
        except Exception as exc:
            self.failed.emit(f"Couldn't search this PDF: {exc}")
