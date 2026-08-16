"""
sync/failsafe.py — the "JARVIS Failsafe" hard-termination pipeline.

A single choke point that:
  1. kills every subprocess A3THER has spawned (tracked PIDs → OS kill-tree),
  2. clears outstanding task queues (mesh delivery queues, pending buffers),
  3. broadcasts a TerminationOrder to every connected device,
so a "terminate"/"abort" instruction from a terminal or a mobile client
stops local threads *and* remote clients instantly.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

from .logging import log
from .protocol import TerminationOrder

#: PIDs currently spawned by the host (registered by autopilot/executor.py).
_ACTIVE_PIDS: set[int] = set()
_PIDS_LOCK = threading.Lock()

#: Termination hooks — callables(reason: str) cleared on every terminate.
_TERMINATION_HOOKS: list[object] = []
_HOOKS_LOCK = threading.Lock()


def track_pid(pid: int) -> None:
    """Register a spawned child process for failsafe cleanup."""
    if pid and pid > 0:
        with _PIDS_LOCK:
            _ACTIVE_PIDS.add(pid)


def untrack_pid(pid: int) -> None:
    with _PIDS_LOCK:
        _ACTIVE_PIDS.discard(pid)


def active_pids() -> list[int]:
    with _PIDS_LOCK:
        return sorted(_ACTIVE_PIDS)


def on_terminate(hook: object) -> None:
    """Register a cleanup callable, invoked with (reason: str) on terminate."""
    with _HOOKS_LOCK:
        _TERMINATION_HOOKS.append(hook)


def _kill_tree(pid: int) -> bool:
    """Kill a process and its whole tree, cross-platform, best-effort."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=5,
            )
        else:
            try:
                os.killpg(pid, 9)          # whole process group
            except ProcessLookupError:
                os.kill(pid, 9)
        return True
    except Exception:  # noqa: BLE001
        try:
            os.kill(pid, 9)
        except Exception:  # noqa: BLE001
            return False
        return True


class FailsafeTerminator:
    """The one place termination is processed and fanned out."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_terminate = 0.0
        self._last_order_id = ""

    # ------------------------------------------------------------------ #
    def terminate(
        self,
        reason: str = "manual abort",
        scope: str = "all",
        broadcast_mesh: bool = True,
    ) -> dict:
        """Execute a full stop: kill children, clear queues, notify mesh."""
        now = time.time()
        order = TerminationOrder(reason=reason, scope=scope)
        with self._lock:
            # Debounce: a terminate already in flight (e.g. re-entered via the
            # mesh 'terminate' local hook) is coalesced — this early return is
            # what breaks the broadcast→hook→terminate recursion.
            if now - self._last_terminate < 1.5:
                log("FAILSAFE", "terminate coalesced — already in progress")
                return {
                    "ok": True,
                    "coalesced": True,
                    "order_id": self._last_order_id or order.order_id,
                    "reason": reason,
                    "scope": scope,
                    "at": now,
                }
            self._last_terminate = now
            self._last_order_id = order.order_id

        log("FAILSAFE", f"TERMINATE issued — reason: {reason} | scope: {scope} | order: {order.order_id}")

        killed: list[int] = []
        failed: list[int] = []
        with _PIDS_LOCK:
            pids = list(_ACTIVE_PIDS)
            _ACTIVE_PIDS.clear()

        for pid in pids:
            ok = _kill_tree(pid)
            (killed if ok else failed).append(pid)
            untrack_pid(pid)

        # Run registered cleanup hooks (task queues, buffers, etc.).
        with _HOOKS_LOCK:
            hooks = list(_TERMINATION_HOOKS)
        for hook in hooks:
            try:
                hook(reason)
            except Exception:  # noqa: BLE001
                pass

        # Fan the termination order out to the mesh.
        mesh_delivered = 0
        if broadcast_mesh:
            try:
                from .broadcaster import get_broadcast_engine

                summary = get_broadcast_engine().broadcast(
                    "terminate",
                    params={"reason": reason, "order_id": order.order_id},
                    source="failsafe",
                )
                mesh_delivered = summary.get("delivered", 0)
            except Exception as exc:  # noqa: BLE001
                log("FAILSAFE", f"mesh broadcast failed: {exc}")

        log(
            "FAILSAFE",
            f"killed {len(killed)} process tree(s) {killed[:8]} | "
            f"mesh notified: {mesh_delivered} | hooks: {len(hooks)}",
        )
        return {
            "ok": True,
            "order_id": order.order_id,
            "reason": reason,
            "scope": scope,
            "killed_pids": killed,
            "failed_pids": failed,
            "mesh_delivered": mesh_delivered,
            "hooks_run": len(hooks),
            "at": order.issued_at,
        }


# --------------------------------------------------------------------------- #
# Singleton
# --------------------------------------------------------------------------- #

_TERMINATOR: FailsafeTerminator | None = None
_TERMINATOR_LOCK = threading.Lock()


def get_failsafe() -> FailsafeTerminator:
    global _TERMINATOR
    if _TERMINATOR is None:
        with _TERMINATOR_LOCK:
            if _TERMINATOR is None:
                _TERMINATOR = FailsafeTerminator()
    return _TERMINATOR
