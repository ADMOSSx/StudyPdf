"""Encode a cropped region of a PDF page as a compact PNG/JPEG for sharing on the whiteboard."""
from __future__ import annotations

import base64

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

LIMIT_B64 = 600_000          # the server accepts up to 700 kB of base64 per image


def _encode(img: QImage, fmt: str, quality: int) -> str:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt, quality)
    return base64.b64encode(bytes(ba.data())).decode()


def encode_snip(image: QImage) -> tuple[str, int, int]:
    """PNG when it fits (crisp text), otherwise JPEG, otherwise shrink and retry. Returns (base64, w, h)."""
    img = image.convertToFormat(QImage.Format.Format_RGB888)
    for _ in range(8):
        for fmt, quality in (("PNG", -1), ("JPEG", 90), ("JPEG", 78)):
            data = _encode(img, fmt, quality)
            if len(data) <= LIMIT_B64:
                return data, img.width(), img.height()
        img = img.scaled(int(img.width() * 0.8), int(img.height() * 0.8),
                         Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    raise ValueError("The selected area is too detailed to share. Select a smaller region.")
