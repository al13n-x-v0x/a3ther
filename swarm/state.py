"""
state.py — the shared global state canvas.

``AgentState`` is a thread-safe dict-like object handed between agents so
they can pass variables, results and context to each other. Every write
is recorded in ``log`` for the dashboard terminal.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class AgentState:
    """Shared, thread-safe task state with an audit log."""

    def __init__(self, task: str = "", task_id: str = ""):
        self.task = task
        self.task_id = task_id
        self._data: dict[str, Any] = {}
        self._lock = threading.RLock()
        self.log: list[dict] = []
        self._log_lock = threading.Lock()
        self.completed = False

    # ------------------------------------------------------------------ #
    # Dict-like access
    # ------------------------------------------------------------------ #
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any, by: str = "system") -> None:
        with self._lock:
            self._data[key] = value
        self.event("state", f"{by} set {key}")

    def update(self, mapping: dict, by: str = "system") -> None:
        with self._lock:
            self._data.update(mapping)
        self.event("state", f"{by} updated {len(mapping)} key(s)")

    def data(self) -> dict:
        with self._lock:
            return dict(self._data)

    # ------------------------------------------------------------------ #
    # Event log
    # ------------------------------------------------------------------ #
    def event(self, kind: str, message: str, agent: str = "system", **extra) -> None:
        """Record a transition for the frontend terminal log."""
        entry = {
            "ts": time.time(),
            "kind": kind,
            "agent": agent,
            "message": message,
            **extra,
        }
        with self._log_lock:
            self.log.append(entry)
            if len(self.log) > 500:
                self.log = self.log[-500:]

    def events_since(self, timestamp: float) -> list[dict]:
        with self._log_lock:
            return [e for e in self.log if e["ts"] > timestamp]
