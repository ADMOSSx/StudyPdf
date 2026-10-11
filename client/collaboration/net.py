"""WebSocket client with automatic reconnect and a heartbeat watchdog (Qt event-loop friendly)."""
from __future__ import annotations

import json
import time
from functools import partial
from typing import Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

BACKOFF = (1, 2, 4, 8, 10)


class CollabClient(QObject):
    opened = Signal()
    closed = Signal()
    received = Signal(dict)
    status = Signal(str)        # connecting | online | reconnecting | offline
    failed = Signal(str)        # could not reach the server on the first attempt

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ws: QWebSocket | None = None
        self._url = ""
        self._want = False
        self._ever = False
        self._attempt = 0
        self._dead = False
        self._online = False
        self._last_seen = time.monotonic()
        self._retry = QTimer(self, singleShot=True)
        self._retry.timeout.connect(self._open)
        self._watch = QTimer(self, interval=6000)
        self._watch.timeout.connect(self._heartbeat)

    @property
    def online(self) -> bool:
        return self._online

    def connect_to(self, url: str) -> None:
        self._url, self._want, self._ever, self._attempt = url, True, False, 0
        self._open()

    def close(self) -> None:
        self._want = False
        self._retry.stop()
        self._watch.stop()
        self._drop()
        self._online = False
        self.status.emit("offline")

    def send(self, msg: dict[str, Any]) -> bool:
        if not (self._online and self._ws):
            return False
        self._ws.sendTextMessage(json.dumps(msg, separators=(",", ":")))
        return True

    # ---- internals ----------------------------------------------------------
    def _drop(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            for sig in (ws.connected, ws.disconnected, ws.textMessageReceived, ws.errorOccurred, ws.pong):
                try:
                    sig.disconnect()
                except Exception:
                    pass
            ws.abort()
            ws.deleteLater()

    def _open(self) -> None:
        self._drop()
        self._dead = False
        ws = QWebSocket()
        if hasattr(ws, "setMaxAllowedIncomingMessageSize"):
            ws.setMaxAllowedIncomingMessageSize(64 * 1024 * 1024)
        ws.connected.connect(partial(self._on_connected, ws))
        ws.disconnected.connect(partial(self._lost, ws, ""))
        ws.textMessageReceived.connect(partial(self._on_text, ws))
        ws.errorOccurred.connect(partial(self._on_error, ws))
        ws.pong.connect(partial(self._on_pong, ws))
        self._ws = ws
        self.status.emit("reconnecting" if self._ever else "connecting")
        ws.open(QUrl(self._url))

    def _on_connected(self, ws: QWebSocket) -> None:
        if ws is not self._ws:
            return
        self._ever, self._online, self._attempt = True, True, 0
        self._last_seen = time.monotonic()
        self._watch.start()
        self.status.emit("online")
        self.opened.emit()

    def _on_text(self, ws: QWebSocket, text: str) -> None:
        if ws is not self._ws:
            return
        self._last_seen = time.monotonic()
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if isinstance(msg, dict):
            self.received.emit(msg)

    def _on_pong(self, ws: QWebSocket, *_: Any) -> None:
        if ws is self._ws:
            self._last_seen = time.monotonic()

    def _on_error(self, ws: QWebSocket, *_: Any) -> None:
        self._lost(ws, ws.errorString())

    def _lost(self, ws: QWebSocket, reason: str) -> None:
        if ws is not self._ws or self._dead:
            return
        self._dead = True
        was_online, self._online = self._online, False
        self._watch.stop()
        if was_online:
            self.closed.emit()
        if not self._want:
            return
        if not self._ever:
            self._want = False
            self._drop()
            self.status.emit("offline")
            self.failed.emit(reason or "Connection refused")
            return
        delay = BACKOFF[min(self._attempt, len(BACKOFF) - 1)]
        self._attempt += 1
        self.status.emit("reconnecting")
        self._retry.start(delay * 1000)

    def _heartbeat(self) -> None:
        if not (self._online and self._ws):
            return
        if time.monotonic() - self._last_seen > 20:
            ws = self._ws
            ws.abort()
            self._lost(ws, "timeout")
        else:
            self._ws.ping()
