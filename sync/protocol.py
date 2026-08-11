"""
sync/protocol.py — typed payload contracts for the device mesh.

These dataclasses are the *authoritative* wire schema. Every message that
crosses the mesh (WebSocket JSON, internal queues, mobile webhooks) is
serialized from them and validated on arrival.

For the companion app / iOS Shortcuts side, the same contracts are exposed
as TypeScript interfaces (see :data:`TS_INTERFACES`) so the mobile client
can be written with full static typing.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Client profiles
# --------------------------------------------------------------------------- #

#: Canonical device kinds the mesh understands.
DEVICE_KINDS = ("iphone", "ipad", "android", "desktop", "terminal", "web", "iot", "unknown")

#: Order used when aggregating mesh statistics.
KIND_ORDER = ("iphone", "ipad", "android", "desktop", "terminal", "web", "iot")


@dataclass
class ClientProfile:
    """Everything the mesh knows about one connected node."""

    node_id: str
    name: str
    kind: str = "unknown"          # one of DEVICE_KINDS
    platform: str = ""             # "iOS 17.4", "Windows 11", "Android 14" …
    app_version: str = ""          # companion-app version, if any
    capabilities: list[str] = field(default_factory=list)   # e.g. ["push", "focus", "exec"]
    transport: str = "websocket"   # websocket | webhook | sse

    def __post_init__(self) -> None:
        if self.kind not in DEVICE_KINDS:
            self.kind = "unknown"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "ClientProfile":
        data = data or {}
        return cls(
            node_id=str(data.get("node_id") or uuid.uuid4().hex[:12]),
            name=str(data.get("name") or "Unknown Node"),
            kind=str(data.get("kind") or "unknown"),
            platform=str(data.get("platform") or ""),
            app_version=str(data.get("app_version") or ""),
            capabilities=list(data.get("capabilities") or []),
            transport=str(data.get("transport") or "websocket"),
        )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

#: Built-in deep-automation commands every node should understand.
BUILTIN_COMMANDS = (
    "unlock_interface",
    "initialize_diagnostic",
    "system_sleep",
    "lock_interface",
    "flash_screen",
    "play_tone",
    "open_url",
    "push_notification",
    "terminate",
)


@dataclass
class DeviceCommand:
    """A single instruction broadcast to one or all mesh nodes."""

    command: str
    params: dict[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    target: str | None = None          # node_id; None = broadcast to all
    source: str = "a3ther"             # which component issued it
    issued_at: float = field(default_factory=time.time)
    ack_required: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "DeviceCommand":
        data = data or {}
        return cls(
            command=str(data.get("command") or "noop"),
            params=dict(data.get("params") or {}),
            command_id=str(data.get("command_id") or uuid.uuid4().hex[:16]),
            target=data.get("target"),
            source=str(data.get("source") or "a3ther"),
            issued_at=float(data.get("issued_at") or time.time()),
            ack_required=bool(data.get("ack_required")),
        )


@dataclass
class CommandResult:
    """Outcome of a command on one node (local hook or remote ack)."""

    node_id: str
    command_id: str
    ok: bool
    detail: str = ""
    duration_ms: float = 0.0
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Node state + mesh events
# --------------------------------------------------------------------------- #


@dataclass
class MobileDeviceState:
    """Snapshot of a mobile node's health, for profiling + the dashboard."""

    node_id: str
    kind: str = "unknown"
    online: bool = False
    battery_percent: float | None = None
    screen_on: bool | None = None
    focus_mode: str | None = None          # iOS Focus: "work", "sleep", "driving" …
    network: str | None = None             # "wifi" | "cellular"
    last_seen: float = 0.0
    rssi_dbm: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MeshEvent:
    """A lifecycle message (join/leave/command/ack) for logs + the HUD."""

    type: str                            # join | leave | command | ack | broadcast | terminate
    node_id: str = ""
    node_name: str = ""
    kind: str = "unknown"
    command: str = ""
    detail: str = ""
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Termination order
# --------------------------------------------------------------------------- #


@dataclass
class TerminationOrder:
    """The JARVIS failsafe — kill everything, everywhere, immediately."""

    reason: str
    scope: str = "all"                     # all | local | mesh
    issued_at: float = field(default_factory=time.time)
    order_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# TypeScript mirrors (documentation for the companion app / iOS client)
# --------------------------------------------------------------------------- #

TS_INTERFACES = """\
// sync/protocol.ts — companion-app side of the A.3.T.H.E.R. mesh.
// Mirror of sync/protocol.py. Keep both in lockstep.

export type DeviceKind = 'iphone' | 'ipad' | 'android' | 'desktop' | 'terminal' | 'web' | 'iot' | 'unknown';

export interface ClientProfile {
  node_id: string;
  name: string;
  kind: DeviceKind;
  platform: string;
  app_version: string;
  capabilities: string[];
  transport: 'websocket' | 'webhook' | 'sse';
}

export interface DeviceCommand {
  command: string;
  params: Record<string, unknown>;
  command_id: string;
  target: string | null;   // null = broadcast
  source: string;
  issued_at: number;
  ack_required: boolean;
}

export interface MobileDeviceState {
  node_id: string;
  kind: DeviceKind;
  online: boolean;
  battery_percent: number | null;
  screen_on: boolean | null;
  focus_mode: string | null;
  network: 'wifi' | 'cellular' | null;
  last_seen: number;
  rssi_dbm: number | null;
}

export interface MeshEvent {
  type: 'join' | 'leave' | 'command' | 'ack' | 'broadcast' | 'terminate';
  node_id: string;
  node_name: string;
  kind: DeviceKind;
  command: string;
  detail: string;
  at: number;
}

export interface TerminationOrder {
  reason: string;
  scope: 'all' | 'local' | 'mesh';
  issued_at: number;
  order_id: string;
}
"""
