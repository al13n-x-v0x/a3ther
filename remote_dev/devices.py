"""
devices.py — device identity, pairing store, and LAN discovery.

This is the Phase-1 remote-control layer for A3THER:

- A persistent *device identity* (id + name + public key material) stored
  in the OS app-data folder so the laptop is the same "A3THER Laptop" every
  boot.
- A *pairing store*: pending pairing codes (short-lived) and paired
  devices (token -> device info) persisted to disk so a phone stays
  paired across restarts.
- *LAN discovery*: a UDP beacon announces the laptop's presence on the
  local network so the phone app can find it without typing an IP.

Everything here is stdlib-only and self-contained — it does not depend on
the rest of the repo (several imports elsewhere in this checkout are
broken), so the server runs on its own.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths & identity
# --------------------------------------------------------------------------- #

PROTOCOL_VERSION = 1
DISCOVERY_PORT = 42871          # UDP: LAN beacon / discovery probe
PAIR_TTL_SECONDS = 600          # a pairing code expires after 10 minutes
PAIR_CODE_LENGTH = 6


def data_dir() -> Path:
    """OS-appropriate folder for remote-control state (never the repo)."""
    base = os.environ.get("A3THER_DATA_DIR")
    if base:
        path = Path(base)
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA", str(Path.home()))) / "A3THER"
    else:
        path = Path.home() / ".config" / "a3ther"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class DeviceIdentity:
    """Stable identity for this machine on the LAN."""

    device_id: str
    name: str
    created_at: str
    version: int = PROTOCOL_VERSION

    def public(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "version": self.version,
            "created_at": self.created_at,
            "platform": os.name,
        }


def load_or_create_identity() -> DeviceIdentity:
    """Load the persisted identity, creating a fresh one on first run."""
    path = data_dir() / "identity.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return DeviceIdentity(
                device_id=str(raw["device_id"]),
                name=str(raw.get("name", "A3THER Laptop")),
                created_at=str(raw.get("created_at", "")),
                version=int(raw.get("version", PROTOCOL_VERSION)),
            )
        except Exception:
            pass
    identity = DeviceIdentity(
        device_id=str(uuid.uuid4()),
        name=os.environ.get("A3THER_DEVICE_NAME") or f"A3THER-{socket.gethostname() or 'Laptop'}",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    path.write_text(
        json.dumps(identity.__dict__, indent=2),
        encoding="utf-8",
    )
    return identity


# --------------------------------------------------------------------------- #
# Pairing store
# --------------------------------------------------------------------------- #


@dataclass
class PairedDevice:
    """A phone/laptop that completed pairing."""

    token: str
    name: str
    device_id: str
    paired_at: str
    last_seen: str = ""


class PairingStore:
    """Pending codes + paired devices, persisted as JSON."""

    def __init__(self, path: Path | None = None):
        self._path = path or (data_dir() / "pairing.json")
        self._lock = threading.RLock()
        self._pending: dict[str, float] = {}          # code -> expires_at
        self._paired: dict[str, dict] = {}            # token -> device info
        self._load()

    # -- persistence -------------------------------------------------------- #
    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._pending = {str(k): float(v) for k, v in raw.get("pending", {}).items()}
                self._paired = {str(k): dict(v) for k, v in raw.get("paired", {}).items()}
        except Exception:
            self._pending, self._paired = {}, {}

    def _save(self) -> None:
        with self._lock:
            try:
                self._path.write_text(
                    json.dumps({"pending": self._pending, "paired": self._paired}, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

    # -- pairing ------------------------------------------------------------ #
    def new_code(self) -> str:
        """Generate a fresh, unique 6-digit pairing code."""
        with self._lock:
            self._prune()
            while True:
                code = "".join(secrets.choice("0123456789") for _ in range(PAIR_CODE_LENGTH))
                if code not in self._pending:
                    self._pending[code] = time.time() + PAIR_TTL_SECONDS
                    self._save()
                    return code

    def confirm(self, code: str, name: str, phone_id: str) -> str | None:
        """Validate a code and mint a long-lived token; None if invalid/expired."""
        with self._lock:
            self._prune()
            expires = self._pending.pop(code, None)
            if expires is None:
                return None
            token = secrets.token_urlsafe(32)
            self._paired[token] = PairedDevice(
                token=token,
                name=name or "Phone",
                device_id=phone_id or "unknown",
                paired_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ).__dict__
            self._save()
            return token

    def revoke(self, token: str) -> bool:
        with self._lock:
            removed = self._paired.pop(token, None) is not None
            if removed:
                self._save()
            return removed

    def touch(self, token: str) -> None:
        with self._lock:
            if token in self._paired:
                self._paired[token]["last_seen"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                )
                self._save()

    # -- queries ------------------------------------------------------------ #
    def is_valid(self, token: str) -> bool:
        return token in self._paired

    def devices(self) -> list[dict]:
        with self._lock:
            return [dict(v) for v in self._paired.values()]

    def _prune(self) -> None:
        now = time.time()
        expired = [c for c, exp in self._pending.items() if exp < now]
        for code in expired:
            self._pending.pop(code, None)
        if expired:
            self._save()


# --------------------------------------------------------------------------- #
# LAN discovery
# --------------------------------------------------------------------------- #

_BEACON = {
    "type": "a3ther",
    "proto": PROTOCOL_VERSION,
}


class DiscoveryBeacon:
    """Broadcast presence + answer discovery probes over UDP.

    Beacons are sent every 3 seconds on the LAN broadcast address so the
    phone app can list this laptop automatically. Probes (any packet whose
    payload starts with ``A3THER?``) are answered directly with identity.
    """

    def __init__(self, identity: DeviceIdentity, port: int = DISCOVERY_PORT):
        self.identity = identity
        self.port = port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="a3ther-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        sock.bind(("", self.port))
        sock.settimeout(0.5)

        payload = json.dumps(
            {"type": "a3ther", "proto": PROTOCOL_VERSION, **self.identity.public()}
        ).encode("utf-8")

        while not self._stop.is_set():
            # Periodic broadcast so phones discover us passively.
            try:
                sock.sendto(payload, ("255.255.255.255", self.port))
            except OSError:
                pass
            # Answer direct probes.
            try:
                data, addr = sock.recvfrom(2048)
                if data.startswith(b"A3THER?"):
                    sock.sendto(payload, addr)
            except socket.timeout:
                pass
            except OSError:
                pass
            self._stop.wait(3.0)
        sock.close()


def discover(timeout: float = 2.0, port: int = DISCOVERY_PORT) -> list[dict]:
    """Client helper: find every A3THER device on the LAN.

    Sends a broadcast probe, then collects beacon/answer packets for
    ``timeout`` seconds. Returns a list of identity dicts (deduplicated by
    device_id).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass
    sock.bind(("", port))
    sock.settimeout(timeout)
    sock.sendto(b"A3THER?", ("255.255.255.255", port))

    found: dict[str, dict] = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            info = json.loads(data.decode("utf-8"))
        except Exception:
            continue
        if info.get("type") == "a3ther":
            info.setdefault("addr", addr[0])
            found[info.get("device_id", addr[0])] = info
    sock.close()
    return list(found.values())


# --------------------------------------------------------------------------- #
# Convenience singleton
# --------------------------------------------------------------------------- #

_IDENTITY: DeviceIdentity | None = None
_IDENTITY_LOCK = threading.Lock()


def get_identity() -> DeviceIdentity:
    global _IDENTITY
    if _IDENTITY is None:
        with _IDENTITY_LOCK:
            if _IDENTITY is None:
                _IDENTITY = load_or_create_identity()
    return _IDENTITY


def get_pairing_store() -> PairingStore:
    return PairingStore()
