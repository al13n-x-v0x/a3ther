"""
A3THER Remote Server Developer Mode.

Two layers live in this package:

1. **Remote control (Phase 1)** — :mod:`remote_dev.agent_server` turns this
   machine into a LAN-discoverable A3THER device that a phone can pair with
   and control (``open``, ``lock``, ``status``, optional ``run``).
   :mod:`remote_dev.devices` provides identity + pairing + discovery.

2. **SSH dev mode** — :class:`remote_dev.dev_mode.DevModeManager` opens a
   secure paramiko channel to a server profile and lets A3THER run
   commands, read logs and deploy patches like a real developer.

Security
--------
- Remote control requires pairing; every state-changing call needs the
  paired token.
- Host keys are verified against ``~/.ssh/known_hosts`` when available.
"""
from .devices import (
    DISCOVERY_PORT,
    DeviceIdentity,
    DiscoveryBeacon,
    PairingStore,
    discover,
    get_identity,
    get_pairing_store,
)

try:
    from .agent_server import AgentHTTPServer, execute_action, start_server, stop_server
except Exception:  # noqa: BLE001 — server needs stdlib only; keep import robust
    AgentHTTPServer = execute_action = start_server = stop_server = None  # type: ignore[assignment]

try:
    from .dev_mode import DevModeManager, get_dev_mode_manager
except Exception:  # noqa: BLE001 — legacy SSH mode may be broken on some checkouts
    DevModeManager = get_dev_mode_manager = None  # type: ignore[assignment,misc]

__all__ = [
    "DeviceIdentity",
    "DiscoveryBeacon",
    "PairingStore",
    "discover",
    "get_identity",
    "get_pairing_store",
    "AgentHTTPServer",
    "execute_action",
    "start_server",
    "stop_server",
    "DevModeManager",
    "get_dev_mode_manager",
]
