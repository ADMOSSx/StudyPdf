"""Room registry: creation, lookup, expiry, persistence and timer scheduling."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

from database.server_db import ServerDatabase
from server.config import ServerConfig
from server.models.room import Room
from shared.protocol import make_room_code


class ServerBusy(Exception):
    pass


class RoomManager:
    def __init__(self, config: ServerConfig, db: ServerDatabase) -> None:
        self.config, self.db = config, db
        self.rooms: dict[str, Room] = {}
        self.expired: dict[str, float] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._creations: dict[str, deque] = defaultdict(deque)
        self._failed_joins: dict[str, deque] = defaultdict(deque)
        config.pdf_dir.mkdir(parents=True, exist_ok=True)
        for row in db.load_rooms():
            self.rooms[row["code"]] = Room.restore(row, db)

    def pdf_path(self, code: str):
        return self.config.pdf_dir / f"{code}.pdf"

    def part_path(self, code: str):
        return self.config.pdf_dir / f"{code}.part"

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code)

    def create(self, name: str, password: str | None, ip: str) -> Room:
        now = time.time()
        recent = self._creations[ip]
        while recent and now - recent[0] > 3600:
            recent.popleft()
        if len(self.rooms) >= self.config.max_rooms or len(recent) >= self.config.rooms_per_ip_per_hour:
            raise ServerBusy()
        for _ in range(50):                           # duplicate codes are simply re-rolled
            code = make_room_code()
            if code not in self.rooms:
                break
        else:
            raise ServerBusy()
        room = Room(code, name, password, self.db)
        self.rooms[code] = room
        recent.append(now)
        room.save_meta()
        return room

    def note_failed_join(self, ip: str) -> bool:
        """Records a failed join; returns True once the address is temporarily blocked."""
        now = time.time()
        q = self._failed_joins[ip]
        q.append(now)
        while q and now - q[0] > 300:
            q.popleft()
        return len(q) > 15

    def is_blocked(self, ip: str) -> bool:
        now = time.time()
        q = self._failed_joins[ip]
        while q and now - q[0] > 300:
            q.popleft()
        return len(q) > 15

    def delete(self, room: Room, expired: bool = False) -> None:
        self.rooms.pop(room.code, None)
        h = self._timers.pop(room.code, None)
        if h:
            h.cancel()
        self.db.delete_room(room.code)
        for p in (self.pdf_path(room.code), self.part_path(room.code)):
            p.unlink(missing_ok=True)
        if expired:
            self.expired[room.code] = time.time()

    def schedule_timer(self, room: Room, on_finish: Callable[[Room], Awaitable[None]]) -> None:
        h = self._timers.pop(room.code, None)
        if h:
            h.cancel()
        if room.timer.running:
            delay = max(0.05, room.timer.now_remaining(time.time()))
            loop = asyncio.get_running_loop()
            self._timers[room.code] = loop.call_later(delay, lambda: asyncio.ensure_future(on_finish(room)))

    def expiring_rooms(self) -> list[Room]:
        limit = time.time() - self.config.room_ttl_hours * 3600
        return [r for r in self.rooms.values() if not r.peers and r.last_active < limit]
