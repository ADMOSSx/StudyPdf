"""StudyPDF collaboration server.

    python run.py server --host 0.0.0.0 --port 8765
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import ssl
from pathlib import Path

import websockets

from database.server_db import ServerDatabase
from server.config import ServerConfig
from server.rooms.manager import RoomManager
from server.websocket.handler import ConnectionHandler
from shared.protocol import MAX_FRAME_BYTES


async def serve(config: ServerConfig) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    db = ServerDatabase(config.data_dir / "studypdf_server.db")
    manager = RoomManager(config, db)
    handler = ConnectionHandler(config, manager)
    ssl_ctx = None
    if config.ssl_cert and config.ssl_key:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(config.ssl_cert, config.ssl_key)
    scheme = "wss" if ssl_ctx else "ws"
    async with websockets.serve(handler.handle, config.host, config.port, max_size=MAX_FRAME_BYTES,
                                ping_interval=20, ping_timeout=25, compression=None, ssl=ssl_ctx):
        logging.getLogger("studypdf.server").info(
            "StudyPDF server listening on %s://%s:%d  (%d rooms restored)", scheme, config.host, config.port, len(manager.rooms))
        asyncio.ensure_future(handler.maintenance_loop())
        await asyncio.Future()


def main() -> None:
    ap = argparse.ArgumentParser(description="StudyPDF collaboration server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default="server_data")
    ap.add_argument("--cert", help="TLS certificate (enables wss://)")
    ap.add_argument("--key", help="TLS private key")
    ap.add_argument("--room-ttl-hours", type=float, default=72.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = ServerConfig(host=args.host, port=args.port, data_dir=Path(args.data_dir), ssl_cert=args.cert,
                          ssl_key=args.key, room_ttl_hours=args.room_ttl_hours)
    try:
        asyncio.run(serve(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
