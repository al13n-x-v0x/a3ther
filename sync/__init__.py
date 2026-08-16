"""
sync — the A.3.T.H.E.R. multi-device orchestration layer ("Ultron Control").

Modules
-------
- :mod:`sync.protocol`    — typed wire contracts (DeviceCommand, ClientProfile,
  MobileDeviceState, TerminationOrder) + TypeScript mirrors.
- :mod:`sync.mesh`        — thread-safe node registry with health polling and
  stale-node eviction.
- :mod:`sync.broadcaster` — fan-out broadcast engine ("Ultron Control") with
  local command hooks + self-healing delivery.
- :mod:`sync.mobile`      — iOS/iPhone/Android compatibility layer: Focus
  modes, APNs payloads, Shortcuts webhooks, device profiling.
- :mod:`sync.failsafe`    — the "JARVIS Failsafe" hard-termination pipeline:
  kill process trees, clear queues, notify the mesh.
- :mod:`sync.logging`     — color-coded subsystem logging.

Wire it up::

    from sync.broadcaster import get_broadcast_engine, register_command

    register_command("play_tone", lambda params: print("tone", params))
    get_broadcast_engine().broadcast("unlock_interface", {"confirm": True})
"""
from .broadcaster import get_broadcast_engine
from .failsafe import get_failsafe
from .mesh import get_mesh_registry
from .mobile import get_mobile_controller
from .protocol import TS_INTERFACES

__all__ = [
    "get_broadcast_engine",
    "get_failsafe",
    "get_mesh_registry",
    "get_mobile_controller",
    "TS_INTERFACES",
]
