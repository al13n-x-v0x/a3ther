"""
queue.py — asynchronous agent-to-agent communication.

:class:`AgentMailbox` gives every agent its own FIFO inbox plus a shared
worker pool, so long-running tasks (research, codegen) can execute
concurrently in the background without locking up the UI.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable


class AgentMailbox:
    """Per-agent message queues with a background worker pool."""

    def __init__(self, max_workers: int = 4):
        self._inboxes: dict[str, "queue.Queue[dict]"] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="swarm"
        )

    # ------------------------------------------------------------------ #
    def _inbox(self, agent: str) -> "queue.Queue[dict]":
        with self._lock:
            if agent not in self._inboxes:
                self._inboxes[agent] = queue.Queue(maxsize=64)
            return self._inboxes[agent]

    def send(self, to: str, message: dict) -> None:
        """Post a message to an agent's inbox (async, non-blocking)."""
        try:
            self._inbox(to).put_nowait(dict(message))
        except queue.Full:
            raise RuntimeError(f"Inbox full for agent {to!r}")

    def receive(self, agent: str, timeout: float = 0.1) -> dict | None:
        try:
            return self._inbox(agent).get(timeout=timeout)
        except queue.Empty:
            return None

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        """Run a long task on the shared pool; returns a concurrent Future."""
        return self._pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
