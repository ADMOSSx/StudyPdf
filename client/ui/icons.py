"""Crisp vector icons drawn from inline SVG (no image assets to ship)."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

P = {
    "select": '<path d="M5 3l14 8-6.5 1.8L10 19z"/>',
    "pen": '<path d="M4 20l1-4L16.5 4.5a2 2 0 013 3L8 19z"/><path d="M14 7l3 3"/>',
    "edit": '<path d="M4 20l1-4L16.5 4.5a2 2 0 013 3L8 19z"/><path d="M14 7l3 3"/>',
    "highlighter": '<path d="M9 13l6-6 4 4-6 6z"/><path d="M9 13l-3 3 1 3h4l2-2"/><path d="M4 21h6"/>',
    "text": '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>',
    "rect": '<rect x="4" y="5" width="16" height="14" rx="2"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8.5" ry="6.5"/>',
    "arrow": '<path d="M5 19L19 5M9 5h10v10"/>',
    "line": '<path d="M5 19L19 5"/>',
    "underline": '<path d="M7 4v7a5 5 0 0010 0V4M5 21h14"/>',
    "strike": '<path d="M4 12h16M16.5 7C15.5 5.5 14 5 12 5c-2.5 0-4 1.2-4 3 0 1.3.8 2.2 2 2.8M8.5 17c1 1.3 2.100 2 3.800 2 2.500 0 4.200-1.200 4.200-3 0-.6-.2-1.100-.5-1.500"/>',
    "eraser": '<path d="M8 20h11M5.500 14L14 5.500a2 2 0 012.800 0l2.700 2.700a2 2 0 010 2.800L11 19.500H8l-3-3a2 2 0 010-2.500z"/>',
    "note": '<path d="M5 4h14v10l-5 6H5z"/><path d="M14 20v-6h5"/>',
    "comment": '<path d="M4 5h16v11H10l-5 4v-4H4z"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 000 12h3"/>',
    "zoom_in": '<circle cx="11" cy="11" r="6.500"/><path d="M20 20l-4.500-4.500M11 8.500v5M8.500 11h5"/>',
    "zoom_out": '<circle cx="11" cy="11" r="6.500"/><path d="M20 20l-4.500-4.500M8.500 11h5"/>',
    "fit_page": '<path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"/>',
    "fit_width": '<path d="M4 5v14M20 5v14M8 12h8M10.500 9.500L8 12l2.500 2.500M13.500 9.500L16 12l-2.500 2.500"/>',
    "fullscreen": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
    "hand": '<path d="M8 12V5a2 2 0 014 0v6-3a2 2 0 014 0v3-2a2 2 0 014 0v7c0 4-2 6-6 6h-1c-2 0-3.5-1-4.5-2.5L5 16a2 2 0 013-2l2 2"/>',
    "panel-right": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    "panel-right-close": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16M18 9l-3 3 3 3"/>',
    "sparkles": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',

    "left": '<path d="M15 6l-6 6 6 6"/>',
    "right": '<path d="M9 6l6 6-6 6"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.600 5.600l2.100 2.100M16.300 16.300l2.100 2.100M5.600 18.400l2.100-2.100M16.300 7.700l2.100-2.100"/>',
    "users": '<circle cx="9" cy="8" r="3.500"/><path d="M2.500 20c0-3.500 3-6 6.500-6s6.500 2.500 6.500 6M16 4.500a3.500 3.500 0 010 7M18 14.500c2 .8 3.500 2.800 3.500 5.500"/>',
    "folder": '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    "save": '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    "export": '<path d="M12 4v11M7.500 10.500L12 15l4.500-4.500M5 19h14"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "join": '<path d="M10 5H6a2 2 0 00-2 2v10a2 2 0 002 2h4M14 8l4 4-4 4M18 12H9"/>',
    "leave": '<path d="M14 5h4a2 2 0 012 2v10a2 2 0 01-2 2h-4M10 8l-4 4 4 4M6 12h9"/>',
    "home": '<path d="M4 11l8-7 8 7M6 10v9h12v-9"/>',
    "play": '<path d="M7 5l12 7-12 7z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "reset": '<path d="M4 12a8 8 0 108-8H8M8 4L5 7l3 3"/>',
    "send": '<path d="M4 12l16-8-6 16-3-7z"/>',
    "trash": '<path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/>',
    "check": '<path d="M5 12.500l4.500 4.500L19 7.500"/>',
    "crown": '<path d="M4 8l4 4 4-6 4 6 4-4-1.500 10h-13z"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
    "pages": '<rect x="5" y="3" width="11" height="15" rx="1.500"/><path d="M9 21h9a1 1 0 001-1V7"/>',
    "chat": '<path d="M4 5h16v11H9l-5 4z"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.500 6h.01M4.500 12h.01M4.500 18h.01"/>',
    "step": '<circle cx="12" cy="12" r="8.500"/><path d="M10.500 10.200l2-1.400v7"/>',
    "snip": '<path d="M6 3v13a2 2 0 002 2h13M3 6h13a2 2 0 012 2v13"/>',
    "board": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M7 9l3 3 2-2 3 3"/>',
    "sigma": '<path d="M17 5H7l5.500 7L7 19h10"/>',
    "question": '<circle cx="12" cy="12" r="9"/><path d="M9.500 9.500a2.500 2.500 0 015 .500c0 1.700-2.500 2-2.500 3.500M12 17h.01"/>',
    "grid": '<path d="M4 4h16v16H4zM4 12h16M12 4v16"/>',
    "search": '<circle cx="11" cy="11" r="6.500"/><path d="M20 20l-4.500-4.500"/>',
    "more": '<path d="M5 12h.01M12 12h.01M19 12h.01"/>',
    "command": '<path d="M9 6a3 3 0 10-3 3h12a3 3 0 10-3-3v12a3 3 0 103-3H6a3 3 0 103 3z"/>',
    "sidebar": '<rect x="3" y="4" width="18" height="16" rx="2.500"/><path d="M9.500 4v16"/>',
    "mode": '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/>',
    "moon": '<path d="M20.5 14.2A8.5 8.5 0 019.8 3.5 8.5 8.5 0 1020.5 14.2z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.65 6.35l1.42-1.42"/>',
}
_cache: dict[tuple[str, str, int], QIcon] = {}


def icon(name: str, color: str = "#48506a", size: int = 20) -> QIcon:
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{P[name]}</svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    ic = QIcon(pm)
    _cache[key] = ic
    return ic


def logo_pixmap(size: int = 64) -> QPixmap:
    """The StudyPDF mark: a gradient tile with a page, text lines and a yellow highlighter swipe."""
    s = size * 2
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0, QColor("#176b66"))
    g.setColorAt(1, QColor("#527b9a"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.27, s * 0.27)
    page = QRectF(s * 0.27, s * 0.18, s * 0.46, s * 0.64)
    p.setBrush(QColor(255, 255, 255, 245))
    p.drawRoundedRect(page, s * 0.05, s * 0.05)
    ys = [page.top() + s * (0.14 + 0.12 * k) for k in range(4)]
    p.setBrush(QColor(255, 212, 59, 170))
    p.drawRoundedRect(QRectF(page.left() + s * 0.05, ys[1] - s * 0.04, s * 0.30, s * 0.08), s * 0.03, s * 0.03)
    p.setPen(QPen(QColor("#6d5efc"), s * 0.035, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for k, y in enumerate(ys):
        p.drawLine(QPointF(page.left() + s * 0.07, y), QPointF(page.right() - s * (0.07 if k % 2 == 0 else 0.15), y))
    p.end()
    pm.setDevicePixelRatio(2)
    return pm
