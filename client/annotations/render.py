"""Paints annotations with QPainter. The painter is expected to be scaled so 1 unit = 1 PDF point."""
from __future__ import annotations

import base64
import math
from collections import OrderedDict
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF

from client.annotations.model import COMMENT_H, COMMENT_W, STEP_SIZE, bounds

NOTE_PAD = 8.0
TEXT_PAD = 3.0
CARD_W = 200.0


def qcolor(hex_: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_)
    c.setAlphaF(alpha)
    return c


def make_font(size: float) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI", "Noto Sans", "Arial", "Tahoma", "Sans Serif"])
    f.setPixelSize(max(4, int(round(size))))
    return f


def text_height(text: str, width: float, size: float, pad: float) -> float:
    rect = QFontMetricsF(make_font(size)).boundingRect(QRectF(0, 0, max(width - 2 * pad, 10), 100000),
                                                        int(Qt.TextFlag.TextWordWrap), text or " ")
    return rect.height() + 2 * pad


def smooth_path(pts: list[float]) -> QPainterPath:
    path = QPainterPath()
    n = len(pts) // 2
    path.moveTo(pts[0], pts[1])
    if n == 2:
        path.lineTo(pts[2], pts[3])
    for i in range(1, n - 1):
        mx, my = (pts[2 * i] + pts[2 * i + 2]) / 2, (pts[2 * i + 1] + pts[2 * i + 3]) / 2
        path.quadTo(pts[2 * i], pts[2 * i + 1], mx, my)
    if n > 2:
        path.lineTo(pts[-2], pts[-1])
    return path


def arrow_head(x1: float, y1: float, x2: float, y2: float, width: float) -> QPolygonF:
    ang = math.atan2(y2 - y1, x2 - x1)
    L = max(10.0, width * 4.5)
    left = QPointF(x2 - L * math.cos(ang - 0.45), y2 - L * math.sin(ang - 0.45))
    right = QPointF(x2 - L * math.cos(ang + 0.45), y2 - L * math.sin(ang + 0.45))
    return QPolygonF([QPointF(x2, y2), left, right])


_IMAGES: "OrderedDict[tuple[str, int], QImage]" = OrderedDict()


def decoded_image(ann: dict[str, Any]) -> QImage:
    """Decode an embedded question snip once and keep a few recent ones (they can be large)."""
    key = (ann.get("id", ""), len(ann.get("data", "")))
    img = _IMAGES.get(key)
    if img is None:
        img = QImage()
        try:
            img.loadFromData(base64.b64decode(ann.get("data", "")))
        except Exception:
            img = QImage()
        _IMAGES[key] = img
        while len(_IMAGES) > 24:
            _IMAGES.popitem(last=False)
    else:
        _IMAGES.move_to_end(key)
    return img


def comment_card_rect(ann: dict[str, Any]) -> QRectF:
    x, y = ann["pts"][0], ann["pts"][1]
    fm = QFontMetricsF(make_font(12))
    body = fm.boundingRect(QRectF(0, 0, CARD_W - 16, 100000), int(Qt.TextFlag.TextWordWrap), ann.get("text", "") or " ")
    return QRectF(x, y + COMMENT_H + 2, CARD_W, body.height() + 38)


def paint_annotation(p: QPainter, ann: dict[str, Any], expanded: bool = False) -> None:
    kind, pts, st = ann["kind"], ann["pts"], ann.get("style", {})
    color, width = st.get("color", "#ff6b6b"), float(st.get("width", 2.0))
    p.save()
    p.setOpacity(max(0.08, min(1.0, float(st.get("opacity", 1.0)))))
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind in ("pen", "highlighter"):
        alpha = 0.4 if kind == "highlighter" else 1.0
        pen = QPen(qcolor(color, alpha), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if len(pts) <= 2:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(color, alpha))
            p.drawEllipse(QPointF(pts[0], pts[1]), width / 2, width / 2)
        else:
            p.drawPath(smooth_path(pts))
    elif kind in ("line", "arrow", "rect", "ellipse"):
        pen = QPen(qcolor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if kind in ("line", "arrow"):
            p.drawLine(QPointF(pts[0], pts[1]), QPointF(pts[2], pts[3]))
            if kind == "arrow" and math.hypot(pts[2] - pts[0], pts[3] - pts[1]) > 1:
                p.setBrush(qcolor(color))
                p.drawPolygon(arrow_head(*pts[:4], width))
        else:
            r = QRectF(QPointF(pts[0], pts[1]), QPointF(pts[2], pts[3])).normalized()
            p.drawRect(r) if kind == "rect" else p.drawEllipse(r)
    elif kind in ("underline", "strike"):
        p.setPen(QPen(qcolor(color), max(width, 1.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for i in range(0, len(pts), 4):
            p.drawLine(QPointF(pts[i], pts[i + 1]), QPointF(pts[i + 2], pts[i + 3]))
    elif kind == "text":
        x0, y0, x1, y1 = bounds(ann)
        p.setFont(make_font(float(st.get("size", 16))))
        p.setPen(qcolor(color))
        p.drawText(QRectF(x0 + TEXT_PAD, y0 + TEXT_PAD, x1 - x0 - 2 * TEXT_PAD, y1 - y0 - 2 * TEXT_PAD),
                   int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), ann.get("text", ""))
    elif kind == "note":
        x0, y0, x1, y1 = bounds(ann)
        r = QRectF(x0, y0, x1 - x0, y1 - y0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawRoundedRect(r.translated(2, 3), 4, 4)
        base = QColor(color)
        p.setBrush(base.lighter(125) if base.lightness() < 200 else base)
        p.drawRoundedRect(r, 4, 4)
        p.setFont(make_font(float(st.get("size", 14))))
        p.setPen(QColor("#1f2937"))
        p.drawText(r.adjusted(NOTE_PAD, NOTE_PAD, -NOTE_PAD, -NOTE_PAD),
                   int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), ann.get("text", ""))
    elif kind == "image":
        x0, y0, x1, y1 = bounds(ann)
        r = QRectF(x0, y0, x1 - x0, y1 - y0)
        img = decoded_image(ann)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 28))
        p.drawRoundedRect(r.translated(2, 3), 6, 6)                     # soft shadow
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(r, 6, 6)
        if not img.isNull():
            p.drawImage(r.adjusted(2, 2, -2, -2), img)
        else:
            p.setPen(QColor("#94a3b8"))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "Question image unavailable")
        p.setPen(QPen(QColor("#c9d6d3"), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 6, 6)
    elif kind == "step":
        x, y = pts[0], pts[1]
        c = QRectF(x, y, STEP_SIZE, STEP_SIZE)
        p.setPen(QPen(QColor("#ffffff"), 2.2))
        p.setBrush(qcolor(color))
        p.drawEllipse(c)
        f = make_font(15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(c, int(Qt.AlignmentFlag.AlignCenter), ann.get("text", ""))
    elif kind == "comment":
        x, y = pts[0], pts[1]
        bubble = QRectF(x, y, COMMENT_W, COMMENT_H - 5)
        path = QPainterPath()
        path.addRoundedRect(bubble, 5, 5)
        tail = QPainterPath()
        tail.moveTo(x + 5, y + COMMENT_H - 6)
        tail.lineTo(x + 5, y + COMMENT_H)
        tail.lineTo(x + 11, y + COMMENT_H - 6)
        path.addPath(tail)
        p.setPen(QPen(QColor(0, 0, 0, 80), 0.8))
        p.setBrush(qcolor(color))
        p.drawPath(path.simplified())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white"))
        for i in range(3):
            p.drawEllipse(QPointF(x + 6 + i * 5, y + (COMMENT_H - 5) / 2), 1.5, 1.5)
        if expanded:
            card = comment_card_rect(ann)
            p.setPen(QPen(QColor("#d5d9e5"), 0.8))
            p.setBrush(QColor("#ffffff"))
            p.drawRoundedRect(card, 6, 6)
            p.setFont(make_font(11))
            p.setPen(QColor("#667089"))
            p.drawText(card.adjusted(8, 6, -8, 0), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                       ann.get("owner_name", ""))
            p.setFont(make_font(12))
            p.setPen(QColor("#171b26"))
            p.drawText(card.adjusted(8, 26, -8, -6),
                       int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), ann.get("text", ""))
    p.restore()
