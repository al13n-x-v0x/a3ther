"""
client.py — pure-Python phone-side client for the A3THER remote server.

No third-party dependencies (stdlib only), so it runs identically inside
the Android Toga app, on the desktop, or in tests. Talks to
``remote_dev/agent_server.py`` on the laptop:

    discover()            -> list of A3THER devices on the LAN (UDP beacon)
    pair(addr)            -> start pairing; returns {code, qr}
    confirm(addr, code)   -> exchange code for a long-lived token
    command(addr, token)  -> run an action (status / open X / lock / run Y)
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

DISCOVERY_PORT = 42871
HTTP_PORT = 42872
DISCOVERY_TIMEOUT = 2.0


# --------------------------------------------------------------------------- #
# LAN discovery
# --------------------------------------------------------------------------- #


def discover(timeout: float = DISCOVERY_TIMEOUT) -> list[dict]:
    """Find A3THER laptops on the LAN via a UDP broadcast probe."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(0.5)
    try:
        sock.sendto(b"A3THER?", ("255.255.255.255", DISCOVERY_PORT))
    except OSError:
        pass

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
        except Exception:  # noqa: BLE001
            continue
        if info.get("type") != "a3ther":
            continue
        # If the beacon doesn't carry an addr, fall back to the packet source.
        info.setdefault("addr", addr[0])
        found[info.get("device_id", addr[0])] = info
    sock.close()
    return list(found.values())


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #


def _base(addr: str, port: int = HTTP_PORT) -> str:
    """Normalise a host/ip:port into http://host:port."""
    addr = (addr or "").strip().rstrip("/")
    if "://" in addr:
        return addr
    if addr.count(":") == 1:
        host, _, port_str = addr.partition(":")
        return f"http://{host}:{port_str}"
    return f"http://{addr}:{port}"


def _call(base: str, method: str, path: str, body: dict | None = None, token: str | None = None, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(base + path, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode("utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def pair(base: str) -> dict:
    """Request a fresh pairing code from the laptop."""
    return _call(_base(base), "POST", "/pair")


def confirm(base: str, code: str, name: str = "Phone", device_id: str = "") -> str:
    """Exchange the code for a token; raises on invalid code."""
    result = _call(
        _base(base),
        "POST",
        "/pair/confirm",
        {"code": code.strip(), "name": name, "device_id": device_id},
    )
    return result["token"]


def command(base: str, token: str, action: str, timeout: float = 30.0) -> dict:
    """Run an action on the laptop; returns the full response."""
    return _call(_base(base), "POST", "/command", {"action": action}, token=token, timeout=timeout)
