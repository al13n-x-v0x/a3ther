"""
events.py — the process-wide swarm event stream.

Every agent transition, transfer, and hand-off is appended here so the
frontend terminal (``/agents``) can render the swarm's activity live via
a simple poll.
"""
from __future__ import annotations

import itertools
import threading
import time

_EVENT_LIMIT = 1000


class EventLog:
    """Append-only, thread-safe swarm event log."""

    def __init__(self):
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._ids = itertools.count(1)

    def emit(self, kind: str, message: str, agent: str = "system", **extra) -> dict:
        entry = {
            "id": next(self._ids),
            "ts": time.time(),
            "kind": kind,          # plan | start | event | transfer | result | error | done
            "agent": agent,
            "message": message,
            **extra,
        }
        with self._lock:
            self._events.append(entry)
            if len(self._events) > _EVENT_LIMIT:
                self._events = self._events[-_EVENT_LIMIT:]
        return entry

    def recent(self, since_id: int = 0, limit: int = 200) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["id"] > since_id][-limit:]

    def last(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return self._events[-limit:]


_EVENT_LOG: EventLog | None = None
_EVENT_LOCK = threading.Lock()


def get_event_log() -> EventLog:
    """Return the process-wide event log singleton."""
    global _EVENT_LOG
    if _EVENT_LOG is None:
        with _EVENT_LOCK:
            if _EVENT_LOG is None:
                _EVENT_LOG = EventLog()
    return _EVENT_LOG
