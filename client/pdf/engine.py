"""PDF engine: wraps PyMuPDF. Pages are rendered on demand; annotations are never baked into the raster."""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from pathlib import Path

import pymupdf
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage


class PdfError(Exception):
    """User-presentable PDF problem."""


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class PdfDocument:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        try:
            with open(self.path, "rb") as fh:
                if b"%PDF-" not in fh.read(1024):
                    raise PdfError("This file isn't a PDF.")
            self._doc = pymupdf.open(self.path)
        except PdfError:
            raise
        except Exception as exc:
            raise PdfError("This PDF is corrupted or can't be opened.") from exc
        if not self._doc.is_pdf:
            raise PdfError("This file isn't a PDF.")
        if self._doc.needs_pass:
            self._doc.close()
            raise PdfError("This PDF is password-protected, which isn't supported yet.")
        if self._doc.page_count == 0:
            raise PdfError("This PDF has no pages.")
        self.page_count: int = self._doc.page_count
        self.name = Path(self.path).name
        self._sizes: dict[int, tuple[float, float]] = {}
        # Text extraction can be surprisingly large on scanned/OCR-heavy books. Keep only
        # a small LRU of page word boxes rather than retaining every visited page forever.
        self._words: OrderedDict[int, list[tuple[float, float, float, float]]] = OrderedDict()
        self._word_cache_pages = 8

    def page_size(self, index: int) -> tuple[float, float]:
        if index not in self._sizes:
            r = self._doc.load_page(index).rect
            self._sizes[index] = (float(r.width), float(r.height))
        return self._sizes[index]

    def render(self, index: int, scale: float) -> QImage:
        page = self._doc.load_page(index)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
        return img.copy()

    def render_region(self, index: int, rect: tuple[float, float, float, float], target_scale: float = 2.4,
                      max_pixels: int = 14_000_000) -> QImage:
        """Crop `rect` (PDF points) out of a page render. Used to snip a question onto the whiteboard."""
        w, h = self.page_size(index)
        scale = min(target_scale, math.sqrt(max_pixels / max(w * h, 1.0)))
        img = self.render(index, scale)
        x0, y0, x1, y1 = rect
        box = QRect(int(max(0, x0 * scale)), int(max(0, y0 * scale)), int((x1 - x0) * scale), int((y1 - y0) * scale))
        return img.copy(box.intersected(img.rect()))

    def thumbnail(self, index: int, width: int) -> QImage:
        return self.render(index, width / max(self.page_size(index)[0], 1))

    def outline(self) -> list[list]:
        """Return the PDF table of contents as [level, title, one_based_page] entries."""
        try:
            return self._doc.get_toc(simple=True) or []
        except Exception:
            return []

    def words(self, index: int) -> list[tuple[float, float, float, float]]:
        """Word boxes in page points (used to snap underline / strike-through to text)."""
        if index in self._words:
            self._words.move_to_end(index)
            return self._words[index]
        try:
            raw = self._doc.load_page(index).get_text("words")
        except Exception:
            raw = []
        result = [(w[0], w[1], w[2], w[3]) for w in raw]
        self._words[index] = result
        while len(self._words) > self._word_cache_pages:
            self._words.popitem(last=False)
        return result

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:
            pass
