from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    data_dir: Path = Path("server_data")
    max_rooms: int = 200
    max_members: int = 20
    room_ttl_hours: float = 72.0          # idle rooms (nobody connected) expire after this long
    offline_member_ttl: float = 900.0     # seconds before a disconnected member is dropped from the list
    rooms_per_ip_per_hour: int = 12
    ssl_cert: str | None = None
    ssl_key: str | None = None

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"
