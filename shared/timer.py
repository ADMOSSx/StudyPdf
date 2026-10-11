"""Study timer state machine, used by the server (authoritative) and the client (offline mode)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TimerState:
    mode: str = "focus"          # "focus" (single countdown) | "pomodoro"
    phase: str = "focus"         # "focus" | "break"
    focus_min: int = 25
    break_min: int = 5
    running: bool = False
    remaining: float = 25 * 60.0
    started_at: float = 0.0

    def phase_seconds(self) -> float:
        return float((self.focus_min if self.phase == "focus" else self.break_min) * 60)

    def now_remaining(self, now: float) -> float:
        if self.running:
            return max(0.0, self.remaining - (now - self.started_at))
        return max(0.0, self.remaining)

    def start(self, now: float) -> None:
        if self.running:
            return
        if self.remaining <= 0:
            self.remaining = self.phase_seconds()
        self.running = True
        self.started_at = now

    def pause(self, now: float) -> None:
        if self.running:
            self.remaining = self.now_remaining(now)
            self.running = False

    def reset(self) -> None:
        self.phase = "focus"
        self.remaining = self.phase_seconds()
        self.running = False

    def configure(self, mode: str, focus_min: int, break_min: int) -> None:
        self.mode = mode if mode in ("focus", "pomodoro") else "focus"
        self.focus_min = max(1, min(180, int(focus_min)))
        self.break_min = max(1, min(60, int(break_min)))
        self.reset()

    def finish(self, now: float) -> None:
        """Called when a running countdown reaches zero."""
        if self.mode == "pomodoro":
            self.phase = "break" if self.phase == "focus" else "focus"
            self.remaining = self.phase_seconds()
            self.running = True
            self.started_at = now
        else:
            self.running = False
            self.remaining = 0.0

    def to_dict(self, now: float) -> dict[str, Any]:
        return {
            "mode": self.mode, "phase": self.phase, "focus_min": self.focus_min,
            "break_min": self.break_min, "running": self.running,
            "remaining": self.now_remaining(now),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], now: float) -> "TimerState":
        t = cls()
        t.mode = data.get("mode", "focus")
        t.phase = data.get("phase", "focus")
        t.focus_min = int(data.get("focus_min", 25))
        t.break_min = int(data.get("break_min", 5))
        t.running = bool(data.get("running", False))
        t.remaining = float(data.get("remaining", t.phase_seconds()))
        t.started_at = now
        return t
