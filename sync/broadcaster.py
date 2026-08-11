"""
sync/broadcaster.py — the "Ultron Control" broadcast engine.

Dispatches :class:`~sync.protocol.DeviceCommand` payloads to every node in
the mesh simultaneously (fan-out), and to locally-registered command hooks
so the host machine executes deep-automation commands in parallel with the
clients. Dead nodes are dropped on delivery failure (self-healing) and a
dispatch summary with per-node results is returned.
"""
from __future__ import annotations

import threading
import time
import traceback

from .logging import log
from .mesh import get_mesh_registry
from .protocol import BUILTIN_COMMANDS, CommandResult, DeviceCommand, MeshEvent

#: Local command hooks: command-name -> callable(params: dict) -> dict
_LOCAL_HOOKS: dict[str, object] = {}
_HOOKS_LOCK = threading.Lock()


def register_command(name: str, handler: object) -> None:
    """Register a local executor for a command name.

    The handler is called with ``(params: dict)`` and may return a dict
    (used as the CommandResult.detail). Thread-safe.
    """
    with _HOOKS_LOCK:
        _LOCAL_HOOKS[name] = handler
    log("BROADCAST", f"registered local hook '{name}'")


def unregister_command(name: str) -> None:
    with _HOOKS_LOCK:
        _LOCAL_HOOKS.pop(name, None)


def get_local_hooks() -> dict[str, object]:
    with _HOOKS_LOCK:
        return dict(_LOCAL_HOOKS)


def is_builtin(command: str) -> bool:
    return command in BUILTIN_COMMANDS


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class BroadcastEngine:
    """Fan-out dispatcher with local hooks + remote node delivery."""

    def __init__(self) -> None:
        self.registry = get_mesh_registry()
        self.registry.on_event(self._on_mesh_event)

    # ------------------------------------------------------------------ #
    def _on_mesh_event(self, event: MeshEvent) -> None:
        log("BROADCAST", f"mesh {event.type}: {event.node_name} ({event.kind})")

    # ------------------------------------------------------------------ #
    def broadcast(
        self,
        command: str,
        params: dict | None = None,
        target: str | None = None,
        source: str = "a3ther",
        ack_required: bool = False,
        block_local: bool = True,
    ) -> dict:
        """Send a command to all (or one) node and run local hooks.

        Returns a dispatch summary::

            {
              "command": ..., "command_id": ...,
              "targets": N, "delivered": N, "failed": N,
              "local_results": [...], "results": [...],
            }
        """
        cmd = DeviceCommand(
            command=command,
            params=dict(params or {}),
            target=target,
            source=source,
            ack_required=ack_required,
        )
        started = time.time()
        log("BROADCAST", f">> {command} {'→ ' + target if target else '→ ALL'} {params or ''}")

        # -- remote fan-out -----------------------------------------------------
        nodes = self.registry.nodes()
        if target:
            nodes = [n for n in nodes if n.profile.node_id == target]

        delivered = 0
        failed: list[str] = []
        results: list[dict] = []
        for node in nodes:
            node.touch()
            ok = node.enqueue(cmd.to_dict())
            if ok:
                delivered += 1
            else:
                failed.append(node.profile.node_id)
                # Self-heal: a node that can't take commands anymore is dropped.
                self.registry.unregister(node.profile.node_id, reason="delivery failure")

        # -- local hooks ----------------------------------------------------------
        local_results: list[dict] = []
        if block_local:
            for name, handler in get_local_hooks().items():
                if name != command:
                    continue
                res = self._run_local(handler, cmd, started)
                local_results.append(res)

        self.registry.record_event(MeshEvent(
            "broadcast", command=command, detail=f"{delivered} delivered / {failed or 0} failed"
        ))

        summary = {
            "command": cmd.command,
            "command_id": cmd.command_id,
            "params": cmd.params,
            "target": target,
            "targets": len(nodes),
            "delivered": delivered,
            "failed": failed,
            "local_results": local_results,
            "results": results,
            "duration_ms": round((time.time() - started) * 1000, 1),
        }
        log("BROADCAST", f"<< {command} delivered={delivered} failed={failed or 0} in {summary['duration_ms']}ms")
        return summary

    # ------------------------------------------------------------------ #
    @staticmethod
    def _run_local(handler: object, cmd: DeviceCommand, started: float) -> dict:
        """Execute one local hook defensively — never raises into the caller."""
        try:
            detail = handler(cmd.params)
            if detail is None:
                detail = ""
            ok = True
            message = str(detail)[:400]
        except Exception as exc:  # noqa: BLE001
            ok = False
            message = f"{type(exc).__name__}: {exc}"
            log("BROADCAST", f"local hook '{cmd.command}' failed: {message}")
            traceback.print_exc(limit=2)
        result = CommandResult(
            node_id="local",
            command_id=cmd.command_id,
            ok=ok,
            detail=message,
            duration_ms=round((time.time() - started) * 1000, 1),
        )
        return result.to_dict()

    # ------------------------------------------------------------------ #
    def mesh_status(self) -> dict:
        """Status payload for the API + HUD."""
        status = self.registry.status()
        status["builtin_commands"] = list(BUILTIN_COMMANDS)
        status["local_hooks"] = list(get_local_hooks().keys())
        return status


# --------------------------------------------------------------------------- #
# Singleton
# --------------------------------------------------------------------------- #

_ENGINE: BroadcastEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_broadcast_engine() -> BroadcastEngine:
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = BroadcastEngine()
    return _ENGINE
