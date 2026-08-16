"""
sync/mesh.py — the device mesh registry.

Tracks every connected node (WebSocket clients, webhooks, the HUD itself)
with a `ClientProfile`, polls health, and evicts stale/disconnected nodes
so the mesh never stalls on a dead peer.
"""
from __future__ import annotations

import re
import threading
import time

from .logging import log
from .protocol import ClientProfile, MeshEvent

#: A node that hasn't reported within this window is considered offline.
HEALTH_TIMEOUT = 12.0
#: Health sweeps run this often.
HEALTH_INTERVAL = 5.0
#: Commands delivered to a node's queue are dropped after this long.
QUEUE_DRAIN_TIMEOUT = 8.0


# --------------------------------------------------------------------------- #
# Client profiling (user-agent → device kind)
# --------------------------------------------------------------------------- #

_UA_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\biPhone\b", re.I), "iphone"),
    (re.compile(r"\biPad\b", re.I), "ipad"),
    (re.compile(r"\bAndroid\b", re.I), "android"),
    (re.compile(r"\bMac OS X\b", re.I), "desktop"),
    (re.compile(r"\bWindows\b", re.I), "desktop"),
    (re.compile(r"\bLinux\b", re.I), "desktop"),
    (re.compile(r"\bcurl\b|\bpython-requests\b|\bnode\b", re.I), "terminal"),
]


def profile_from_user_agent(
    user_agent: str,
    name: str = "",
    kind_hint: str = "",
    node_id: str = "",
) -> ClientProfile:
    """Build a ClientProfile by sniffing the user-agent string.

    An explicit ``kind_hint`` (from a query param or the join message)
    always wins; otherwise the UA is classified into
    iphone/ipad/android/desktop/terminal.
    """
    ua = user_agent or ""
    kind = kind_hint
    if not kind or kind not in ("iphone", "ipad", "android", "desktop", "terminal", "web", "iot"):
        kind = "unknown"
        for pattern, matched in _UA_RULES:
            if pattern.search(ua):
                kind = matched
                break
        if not ua and kind_hint:
            kind = "web"
    return ClientProfile(
        node_id=node_id,
        name=name or (kind.upper() if kind != "unknown" else "UNKNOWN NODE"),
        kind=kind,
        platform=ua[:80] or "",
        capabilities=[],
    )


# --------------------------------------------------------------------------- #
# Node handle
# --------------------------------------------------------------------------- #

class MeshNode:
    """One connected node. Thread-safe; owns its command delivery queue."""

    def __init__(
        self,
        profile: ClientProfile,
        send: object | None = None,          # callable(message: dict) -> None
        loop: object | None = None,          # asyncio loop of the WS handler
    ) -> None:
        self.profile = profile
        self.send = send                     # sync-compatible send callback
        self.loop = loop                     # for thread-safe queue puts
        self.heartbeat = asyncio_queue()     # commands waiting to be sent
        self._lock = threading.Lock()
        self.last_seen = time.time()
        self.joined_at = self.last_seen

    # -- health --------------------------------------------------------------
    def touch(self) -> None:
        with self._lock:
            self.last_seen = time.time()

    def is_alive(self, now: float | None = None) -> bool:
        with self._lock:
            return (now or time.time()) - self.last_seen < HEALTH_TIMEOUT

    # -- delivery -------------------------------------------------------------
    def enqueue(self, payload: dict) -> bool:
        """Push a command to this node. Returns False when the queue is full.

        Safe to call from any thread: the event-loop put is scheduled with
        ``call_soon_threadsafe`` when a loop is attached. A full queue is
        checked up-front so ``QueueFull`` never surfaces inside the loop.
        """
        try:
            if self.loop is not None:
                if self.heartbeat.full():
                    return False
                self.loop.call_soon_threadsafe(self.heartbeat.put_nowait, payload)
            else:
                self.heartbeat.put_nowait(payload)
            return True
        except Exception:  # noqa: BLE001 — full queue / closed loop
            return False


def asyncio_queue():
    """Lazily import asyncio's Queue — keeps module import side-effect free."""
    import asyncio

    return asyncio.Queue(maxsize=64)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

class MeshRegistry:
    """Thread-safe store of connected nodes + health sweeper."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._nodes: dict[str, MeshNode] = {}
        self._events: list[MeshEvent] = []
        self._event_hooks: list[object] = []      # callables(event: MeshEvent)
        self._sweeper: threading.Thread | None = None
        self._stop = threading.Event()

    # -- lifecycle ------------------------------------------------------------
    def register(self, node: MeshNode) -> MeshEvent:
        with self._lock:
            self._nodes[node.profile.node_id] = node
            self._ensure_sweeper()
        event = MeshEvent("join", node.profile.node_id, node.profile.name, node.profile.kind)
        self.record_event(event)
        log("NODE", f"+ {node.profile.name} ({node.profile.kind}) joined — {self.count()} node(s)")
        return event

    def unregister(self, node_id: str, reason: str = "disconnected") -> MeshEvent | None:
        with self._lock:
            node = self._nodes.pop(node_id, None)
        if node is None:
            return None
        event = MeshEvent("leave", node_id, node.profile.name, node.profile.kind, detail=reason)
        self.record_event(event)
        log("NODE", f"- {node.profile.name} left ({reason}) — {self.count()} node(s)")
        return event

    def get(self, node_id: str) -> MeshNode | None:
        with self._lock:
            return self._nodes.get(node_id)

    def nodes(self) -> list[MeshNode]:
        with self._lock:
            return list(self._nodes.values())

    def count(self) -> int:
        with self._lock:
            return len(self._nodes)

    # -- events ----------------------------------------------------------------
    def record_event(self, event: MeshEvent) -> None:
        """Public: append a mesh event to the log and notify hooks."""
        with self._lock:
            self._events.append(event)
            if len(self._events) > 200:
                self._events = self._events[-200:]
            hooks = list(self._event_hooks)
        for hook in hooks:
            try:
                hook(event)
            except Exception:  # noqa: BLE001
                pass

    def on_event(self, hook: object) -> None:
        """Register a callable(event: MeshEvent); used by the broadcast engine."""
        with self._lock:
            self._event_hooks.append(hook)

    def recent_events(self, limit: int = 25) -> list[dict]:
        with self._lock:
            return [e.to_dict() for e in self._events[-limit:]]

    # -- health ----------------------------------------------------------------
    def sweep(self) -> list[str]:
        """Drop nodes that missed their heartbeat window. Returns evicted ids."""
        now = time.time()
        evicted: list[str] = []
        for node in self.nodes():
            if not node.is_alive(now):
                self.unregister(node.profile.node_id, reason="heartbeat timeout")
                evicted.append(node.profile.node_id)
        return evicted

    def _ensure_sweeper(self) -> None:
        if self._sweeper is None or not self._sweeper.is_alive():
            self._sweeper = threading.Thread(target=self._sweep_loop, name="mesh-sweeper", daemon=True)
            self._sweeper.start()

    def _sweep_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(HEALTH_INTERVAL)
            try:
                self.sweep()
            except Exception:  # noqa: BLE001
                pass

    # -- status -----------------------------------------------------------------
    def status(self) -> dict:
        nodes = self.nodes()
        by_kind: dict[str, int] = {}
        for node in nodes:
            by_kind[node.profile.kind] = by_kind.get(node.profile.kind, 0) + 1
        return {
            "count": len(nodes),
            "by_kind": by_kind,
            "online": [n.profile.to_dict() for n in nodes],
            "events": self.recent_events(25),
        }


# --------------------------------------------------------------------------- #
# Singleton
# --------------------------------------------------------------------------- #

_REGISTRY: MeshRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_mesh_registry() -> MeshRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = MeshRegistry()
    return _REGISTRY
