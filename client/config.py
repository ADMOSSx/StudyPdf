from __future__ import annotations

import os
from pathlib import Path

DEFAULT_SERVER = "ws://localhost:8765"
VERSION = "2.5.0"


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / "StudyPDF"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = app_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path
