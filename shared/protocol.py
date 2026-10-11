"""Wire-protocol constants and strict validators shared by server and client.

Coordinates are expressed in PDF points (1/72 inch) with the origin at the top-left of the
displayed page, so every client agrees on geometry regardless of zoom or screen size.
"""
from __future__ import annotations

import base64
import math
import re
import secrets
import unicodedata
from typing import Any

PROTOCOL_VERSION = 1
MAX_PDF_BYTES = 500 * 1024 * 1024
PDF_CHUNK_BYTES = 512 * 1024  # 512 KiB; base64 JSON frame stays below the 1 MiB limit
MAX_FRAME_BYTES = 1024 * 1024
MAX_PAGES = 5000
MAX_PTS = 20000
MAX_TEXT = 2000
MAX_CHAT = 1000
MAX_NAME = 24
MAX_ROOM_NAME = 48
MAX_ANNOTATIONS = 20000
BOARD_BASE = 10000            # annotation page numbers >= BOARD_BASE are whiteboard pages (BOARD_BASE + board index)
MAX_BOARDS = 20
MAX_IMAGE_B64 = 700_000       # embedded question snips (PNG/JPEG, base64) stay well under the 1 MiB frame limit

ANNOTATION_KINDS = frozenset(
    {"pen", "highlighter", "line", "arrow", "rect", "ellipse", "underline", "strike", "text", "note", "comment",
     "step", "image"}
)
TEXT_KINDS = frozenset({"text", "note", "comment", "step"})
QUESTION_STATUSES = ("todo", "progress", "solved", "difficult")
_EXACT_PTS = {"line": 4, "arrow": 4, "rect": 4, "ellipse": 4, "text": 4, "note": 4, "comment": 2,
              "step": 2, "image": 4}

DEFAULT_SETTINGS: dict[str, bool] = {
    "can_draw": True,           # members may create annotations
    "can_edit_others": True,    # members may move/edit/delete annotations made by others
    "can_chat": True,
    "can_control_timer": True,
    "allow_download": True,     # members may export the annotated PDF (UI-level)
    "sync_pages": False,        # members follow the host's page
}

_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


class ValidationError(ValueError):
    """Raised when a message does not satisfy the protocol."""


def make_room_code() -> str:
    return "".join(secrets.choice(_LETTERS) for _ in range(3)) + "-" + "".join(secrets.choice(_DIGITS) for _ in range(3))


def normalize_room_code(raw: Any) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", raw if isinstance(raw, str) else "").upper()
    return f"{s[:3]}-{s[3:]}" if len(s) == 6 else ""


def clean_text(value: Any, limit: int, multiline: bool = True) -> str:
    """Strip control/bidi-override characters and clamp length. Rendering is always plain text."""
    if not isinstance(value, str):
        return ""
    value = _CONTROL_RE.sub("", unicodedata.normalize("NFC", value))
    if not multiline:
        value = " ".join(value.split())
    return value.strip()[:limit]


def clean_name(value: Any) -> str:
    return clean_text(value, MAX_NAME, multiline=False) or "Guest"


def valid_page(value: Any) -> bool:
    """A PDF page (0..MAX_PAGES-1) or a whiteboard page (BOARD_BASE..BOARD_BASE+MAX_BOARDS-1)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return 0 <= value < MAX_PAGES or BOARD_BASE <= value < BOARD_BASE + MAX_BOARDS


def valid_image_data(value: Any) -> str:
    if not isinstance(value, str) or not 100 < len(value) <= MAX_IMAGE_B64:
        raise ValidationError("bad image size")
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValidationError("bad image data") from None
    if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")):   # PNG or JPEG only
        raise ValidationError("unsupported image type")
    return value


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _num(v: Any, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValidationError("number out of range")
    return float(v)


def validate_pts(kind: str, pts: Any, exact: bool = True) -> list[float]:
    if not isinstance(pts, list) or not pts or len(pts) % 2 or len(pts) > MAX_PTS:
        raise ValidationError("bad pts")
    out = [round(_num(v, -2000, 20000), 2) for v in pts]
    if exact:
        if kind in _EXACT_PTS and len(out) != _EXACT_PTS[kind]:
            raise ValidationError("bad pts length")
        if kind in ("underline", "strike") and (len(out) % 4 or len(out) > 800):
            raise ValidationError("bad segment list")
    return out


def validate_style(style: Any, partial: bool = False) -> dict[str, Any]:
    if not isinstance(style, dict):
        raise ValidationError("bad style")
    out: dict[str, Any] = {}
    if "color" in style:
        if not isinstance(style["color"], str) or not _COLOR_RE.match(style["color"]):
            raise ValidationError("bad color")
        out["color"] = style["color"].lower()
    if "width" in style:
        out["width"] = round(_num(style["width"], 0.1, 200), 2)
    if "size" in style:
        out["size"] = round(_num(style["size"], 4, 200), 1)
    if "opacity" in style:
        out["opacity"] = round(_num(style["opacity"], 0.08, 1.0), 3)
    if not partial:
        out.setdefault("color", "#ff6b6b")
        out.setdefault("width", 2.0)
        out.setdefault("size", 16.0)
    return out


def validate_annotation(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("annotation must be an object")
    kind = raw.get("kind")
    if kind not in ANNOTATION_KINDS:
        raise ValidationError("unknown kind")
    if not valid_id(raw.get("id")):
        raise ValidationError("bad id")
    page = raw.get("page")
    if not valid_page(page):
        raise ValidationError("bad page")
    ann: dict[str, Any] = {
        "id": raw["id"], "kind": kind, "page": page,
        "pts": validate_pts(kind, raw.get("pts")),
        "style": validate_style(raw.get("style", {})),
    }
    if kind in TEXT_KINDS:
        ann["text"] = clean_text(raw.get("text", ""), MAX_TEXT)
    if kind == "image":
        ann["data"] = valid_image_data(raw.get("data"))
    if kind == "step" and not ann["text"]:
        raise ValidationError("step needs a label")
    return ann


def validate_patch(kind: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("patch must be an object")
    out: dict[str, Any] = {}
    if "pts" in raw:
        out["pts"] = validate_pts(kind, raw["pts"])
    if "text" in raw and kind in TEXT_KINDS:
        out["text"] = clean_text(raw["text"], MAX_TEXT)
    if "style" in raw:
        out["style"] = validate_style(raw["style"], partial=True)
    return out


def validate_live(raw: dict[str, Any]) -> dict[str, Any]:
    kind = raw.get("kind")
    if kind not in ("pen", "highlighter") or not valid_id(raw.get("id")):
        raise ValidationError("bad live stroke")
    page = raw.get("page")
    if not valid_page(page):
        raise ValidationError("bad page")
    pts = validate_pts(kind, raw.get("append"), exact=False)
    if len(pts) > 600:
        raise ValidationError("too many points")
    return {"id": raw["id"], "kind": kind, "page": page, "append": pts,
            "style": validate_style(raw.get("style", {}))}


def validate_pdf_info(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("pdf info missing")
    size, pages, sha = raw.get("size"), raw.get("pages"), raw.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_PDF_BYTES:
        raise ValidationError("pdf_too_large")
    if isinstance(pages, bool) or not isinstance(pages, int) or not 0 < pages <= MAX_PAGES:
        raise ValidationError("bad page count")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise ValidationError("bad hash")
    return {"name": clean_text(raw.get("name"), 120, False) or "document.pdf", "size": size,
            "pages": pages, "sha256": sha, "ready": False}


def validate_settings(raw: Any) -> dict[str, bool]:
    if not isinstance(raw, dict):
        raise ValidationError("bad settings")
    return {k: bool(raw[k]) for k in DEFAULT_SETTINGS if k in raw}


def validate_question(raw: Any, page_count: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or not valid_id(raw.get("id")):
        raise ValidationError("bad question")
    status = raw.get("status", "todo")
    page = raw.get("page", 0)
    if status not in QUESTION_STATUSES or isinstance(page, bool) or not isinstance(page, int) \
            or not 0 <= page < max(page_count, 1):
        raise ValidationError("bad question fields")
    label = clean_text(raw.get("label"), 60, False)
    if not label:
        raise ValidationError("empty label")
    return {"id": raw["id"], "label": label, "page": page, "status": status}


def validate_board_title(raw: Any) -> str:
    title = clean_text(raw, 40, multiline=False)
    if not title:
        raise ValidationError("empty board title")
    return title
