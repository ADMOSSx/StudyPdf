"""Background PDF raster rendering with an independent PyMuPDF document handle."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage


class RenderSignals(QObject):
    finished = Signal(object, int, object, str)  # key, generation, QImage, error


class PageRenderTask(QRunnable):
    """Render one page away from the UI thread; never share a fitz Document across threads."""

    def __init__(self, path: str, page_index: int, scale: float, key: tuple[Any, ...], generation: int) -> None:
        super().__init__()
        self.path = str(Path(path))
        self.page_index = int(page_index)
        self.scale = float(scale)
        self.key = key
        self.generation = int(generation)
        self.signals = RenderSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        image = QImage()
        error = ""
        doc = None
        try:
            doc = pymupdf.open(self.path)
            page = doc.load_page(self.page_index)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(self.scale, self.scale), alpha=False)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        except Exception as exc:  # pass a safe message to the UI; don't crash a worker thread
            error = str(exc)[:240]
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
        self.signals.finished.emit(self.key, self.generation, image, error)
