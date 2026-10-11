"""Export annotated PDFs. The original file is never modified: output always goes to a new path."""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import pymupdf
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from client.annotations.model import bounds, ellipse_points
from client.annotations.render import arrow_head, paint_annotation
from client.pdf.engine import PdfDocument, PdfError

Progress = Callable[[int, int], bool]   # (done, total) -> keep going?


def _rgb(hex_: str) -> tuple[float, float, float]:
    c = QColor(hex_)
    return c.redF(), c.greenF(), c.blueF()


def _png(img: QImage, fmt: str = "PNG", quality: int = -1) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt, quality)
    return bytes(ba.data())


def _pairs(pts: list[float]) -> list[pymupdf.Point]:
    return [pymupdf.Point(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)]


def _draw_vector(page: pymupdf.Page, ann: dict[str, Any]) -> None:
    kind, pts, st = ann["kind"], ann["pts"], ann.get("style", {})
    color, width = _rgb(st.get("color", "#ff6b6b")), float(st.get("width", 2))
    shape = page.new_shape()
    if kind in ("pen", "highlighter"):
        alpha = 0.4 if kind == "highlighter" else 1.0
        if len(pts) <= 2:
            shape.draw_circle(pymupdf.Point(pts[0], pts[1]), width / 2)
            shape.finish(color=None, fill=color, fill_opacity=alpha)
        else:
            shape.draw_polyline(_pairs(pts))
            shape.finish(color=color, width=width, stroke_opacity=alpha, lineCap=1, lineJoin=1, closePath=False)
    elif kind in ("line", "arrow"):
        shape.draw_line(pymupdf.Point(pts[0], pts[1]), pymupdf.Point(pts[2], pts[3]))
        shape.finish(color=color, width=width, lineCap=1)
        if kind == "arrow" and math.hypot(pts[2] - pts[0], pts[3] - pts[1]) > 1:
            head = arrow_head(*pts[:4], width)
            shape.draw_polyline([pymupdf.Point(head.at(i).x(), head.at(i).y()) for i in range(3)])
            shape.finish(color=color, fill=color, width=0.4, closePath=True)
    elif kind == "rect":
        x0, y0, x1, y1 = bounds(ann)
        shape.draw_rect(pymupdf.Rect(x0, y0, x1, y1))
        shape.finish(color=color, width=width, lineJoin=1)
    elif kind == "ellipse":
        shape.draw_polyline(_pairs(ellipse_points(*bounds(ann), n=72)))
        shape.finish(color=color, width=width, closePath=True, lineJoin=1)
    elif kind in ("underline", "strike"):
        for i in range(0, len(pts), 4):
            shape.draw_line(pymupdf.Point(pts[i], pts[i + 1]), pymupdf.Point(pts[i + 2], pts[i + 3]))
        shape.finish(color=color, width=max(width, 1.2), lineCap=1)
    shape.commit()


def _draw_text_image(page: pymupdf.Page, ann: dict[str, Any]) -> None:
    """Text/notes are rasterised by Qt so any script (Arabic, CJK, math symbols...) renders correctly."""
    x0, y0, x1, y1 = bounds(ann)
    s = 4.0
    img = QImage(max(1, int((x1 - x0) * s)), max(1, int((y1 - y0) * s)), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    p.scale(s, s)
    p.translate(-x0, -y0)
    paint_annotation(p, ann)
    p.end()
    page.insert_image(pymupdf.Rect(x0, y0, x1, y1), stream=_png(img))


def export_annotated(doc: PdfDocument, anns: list[dict[str, Any]], out_path: str, progress: Progress | None = None) -> None:
    """Vector copy: original pages stay intact (text stays selectable), annotations are drawn on top."""
    if Path(out_path).resolve() == Path(doc.path).resolve():
        raise PdfError("Choose a different file name - the original PDF is never overwritten.")
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for a in sorted(anns, key=lambda a: a.get("z", 0)):
        by_page[a["page"]].append(a)
    src = pymupdf.open(doc.path)
    try:
        for n, (pno, items) in enumerate(sorted(by_page.items())):
            if pno >= src.page_count:
                continue
            page = src[pno]
            if page.rotation:
                page.remove_rotation()
            for ann in items:
                if ann["kind"] in ("text", "note"):
                    _draw_text_image(page, ann)
                elif ann["kind"] == "comment":
                    annot = page.add_text_annot(pymupdf.Point(ann["pts"][0], ann["pts"][1]), ann.get("text", ""), icon="Comment")
                    annot.set_info(title=ann.get("owner_name", ""), content=ann.get("text", ""))
                    annot.update()
                else:
                    _draw_vector(page, ann)
            if progress and not progress(n + 1, len(by_page)):
                raise PdfError("Export cancelled.")
        src.save(out_path, garbage=3, deflate=True)
    finally:
        src.close()


def export_flattened(doc: PdfDocument, anns: list[dict[str, Any]], out_path: str, dpi: int = 150,
                     progress: Progress | None = None) -> None:
    """Print-ready copy: every page (with annotations) becomes one image, nothing remains editable."""
    if Path(out_path).resolve() == Path(doc.path).resolve():
        raise PdfError("Choose a different file name - the original PDF is never overwritten.")
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for a in sorted(anns, key=lambda a: a.get("z", 0)):
        by_page[a["page"]].append(a)
    scale = dpi / 72.0
    out = pymupdf.open()
    try:
        for i in range(doc.page_count):
            img = doc.render(i, scale)
            p = QPainter(img)
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
            p.scale(scale, scale)
            for ann in by_page.get(i, []):
                paint_annotation(p, ann, expanded=ann["kind"] == "comment")
            p.end()
            w, h = doc.page_size(i)
            page = out.new_page(width=w, height=h)
            page.insert_image(page.rect, stream=_png(img, "JPEG", 88))
            if progress and not progress(i + 1, doc.page_count):
                raise PdfError("Export cancelled.")
        out.save(out_path, garbage=3, deflate=True)
    finally:
        out.close()
