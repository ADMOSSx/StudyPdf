"""SQLite persistence for the collaboration server (rooms survive restarts)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_TABLES = ("members", "annotations", "questions")


class ServerDatabase:
    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS rooms (code TEXT PRIMARY KEY, data TEXT NOT NULL)")
        for t in _TABLES:
            self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS {t} (room TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, "
                "PRIMARY KEY (room, id))")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS chat (seq INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL, data TEXT NOT NULL)")

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, args)

    def save_room(self, code: str, data: dict[str, Any]) -> None:
        self._exec("INSERT OR REPLACE INTO rooms(code, data) VALUES (?, ?)", (code, json.dumps(data)))

    def put(self, table: str, room: str, item_id: str, data: dict[str, Any]) -> None:
        assert table in _TABLES
        self._exec(f"INSERT OR REPLACE INTO {table}(room, id, data) VALUES (?, ?, ?)", (room, item_id, json.dumps(data)))

    def delete(self, table: str, room: str, item_id: str | None = None) -> None:
        assert table in _TABLES
        if item_id is None:
            self._exec(f"DELETE FROM {table} WHERE room = ?", (room,))
        else:
            self._exec(f"DELETE FROM {table} WHERE room = ? AND id = ?", (room, item_id))

    def add_chat(self, room: str, data: dict[str, Any]) -> None:
        self._exec("INSERT INTO chat(room, data) VALUES (?, ?)", (room, json.dumps(data)))

    def delete_room(self, code: str) -> None:
        self._exec("DELETE FROM rooms WHERE code = ?", (code,))
        for t in _TABLES:
            self.delete(t, code)
        self._exec("DELETE FROM chat WHERE room = ?", (code,))

    def load_rooms(self) -> list[dict[str, Any]]:
        out = []
        for code, data in self._exec("SELECT code, data FROM rooms").fetchall():
            room = json.loads(data)
            room["code"] = code
            for t in _TABLES:
                room[t] = [json.loads(r[0]) for r in self._exec(f"SELECT data FROM {t} WHERE room = ?", (code,))]
            room["chat"] = [json.loads(r[0]) for r in self._exec(
                "SELECT data FROM chat WHERE room = ? ORDER BY seq DESC LIMIT 300", (code,))][::-1]
            out.append(room)
        return out

    def close(self) -> None:
        self.conn.close()
