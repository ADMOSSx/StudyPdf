"""Local SQLite storage for the desktop client: settings, recent documents, autosaved sessions."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class ClientDatabase:
    def __init__(self, path: Path | str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS recent (path TEXT PRIMARY KEY, name TEXT, sha TEXT, pages INTEGER, opened REAL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS sessions (key TEXT PRIMARY KEY, data TEXT, updated REAL)")

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))

    def add_recent(self, path: str, name: str, sha: str, pages: int) -> None:
        self.conn.execute("INSERT OR REPLACE INTO recent VALUES (?, ?, ?, ?, ?)", (path, name, sha, pages, time.time()))

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT path, name, sha, pages, opened FROM recent ORDER BY opened DESC LIMIT ?", (limit,))
        return [dict(zip(("path", "name", "sha", "pages", "opened"), r)) for r in rows]

    def remove_recent(self, path: str) -> None:
        self.conn.execute("DELETE FROM recent WHERE path = ?", (path,))

    def save_session(self, key: str, data: dict[str, Any]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)", (key, json.dumps(data), time.time()))

    def load_session(self, key: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT data FROM sessions WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
