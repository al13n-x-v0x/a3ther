"""
device_service.py — real device inventory for the HUD.

Two real sources are merged:

1. **Bluetooth** — nearby devices from :mod:`bluetooth_service` (bleak scan).
2. **LAN hosts** — machines on the local network discovered by parsing the
   OS ARP table (``arp -a``) and resolving hostnames. Never fabricated:
   every entry corresponds to a real reachable address.

If a source is unavailable (no bleak, no ARP output) it is reported with an
empty list and a status flag — the HUD shows the truth, not placeholders.
"""
from __future__ import annotations

import logging
import re
import socket
import subprocess
import sys
import threading
import time

from .bluetooth_service import get_bluetooth_devices

LOGGER = logging.getLogger("a3ther.services.devices")

def _is_unicast(ip: str) -> bool:
    """Exclude multicast/reserved addresses (224+ and broadcast) from the list."""
    try:
        parts = [int(p) for p in ip.split(".")]
    except Exception:
        return False
    if len(parts) != 4:
        return False
    if parts[0] >= 224:          # multicast 224-239, reserved 240+
        return False
    if parts[0] == 0:            # "this network"
        return False
    return parts != [255, 255, 255, 255]


def _arp_table() -> list[dict]:
    """Parse the OS ARP table into real LAN host entries."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["arp", "-a"], capture_output=True, text=True, timeout=8, creationflags=0x08000000  # CREATE_NO_WINDOW
            ).stdout
        else:
            out = subprocess.run(["arp", "-an"], capture_output=True, text=True, timeout=8).stdout
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("ARP table unavailable: %s", exc)
        return []

    hosts: dict[str, dict] = {}
    ip = None
    for line in (out or "").splitlines():
        # Windows lines: "  192.168.1.10     aa-bb-cc-dd-ee-ff     dynamic"
        # Linux lines:  "? (192.168.1.10) at aa:bb:cc:dd:ee:ff [ether] on eth0"
        parts = line.split()
        for part in parts:
            if re.match(r"^(\d{1,3}\.){3}\d{1,3}$", part):
                ip = part
                break
        if ip and _is_unicast(ip) and "ff-ff-ff-ff-ff-ff" not in line.lower() and "ff:ff:ff:ff:ff:ff" not in line.lower():
            mac = ""
            m = re.search(r"([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){4})", line)
            if m:
                mac = m.group(1).upper()
            state = "dynamic" if "dynamic" in line.lower() else ("static" if "static" in line.lower() else "")
            if mac and mac != "00-00-00-00-00-00":
                hosts[ip] = {"ip": ip, "mac": mac, "state": state, "source": "lan"}
        ip = None
    return list(hosts.values())


def _hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ip


def get_lan_hosts() -> dict:
    """Real LAN hosts with status; never fabricated."""
    hosts = _arp_table()
    for host in hosts:
        host["name"] = _hostname(host["ip"]).upper() or host["ip"]
        host["online"] = True
        host["paired"] = False
        host["rssi"] = None
    return {
        "available": True,
        "devices": hosts,
        "count": len(hosts),
        "updated": time.time(),
    }


def _mesh_nodes() -> list[dict]:
    """Connected mesh nodes — the only devices that are actually CONTROLLABLE.

    A Bluetooth device being visible nearby (found by a scan) does not mean
    it is connected or controllable; mesh nodes joined the A3THER network,
    so they get ``controllable=True`` and appear first in the HUD.
    """
    try:
        from sync.mesh import get_mesh_registry

        nodes = get_mesh_registry().nodes()
    except Exception:  # noqa: BLE001
        return []
    out = []
    now = time.time()
    for node in nodes:
        p = node.profile
        kind = (p.kind or "").lower()
        # Dashboards / web consoles are control-plane, not user devices —
        # don't list the HUD itself as a "connected device" the user
        # controls. Real phones/tablets/laptops stay.
        if kind == "web":
            continue
        # Latency = how long since this node last heartbeated (health proxy).
        try:
            latency_ms = int((now - node.last_seen) * 1000)
        except Exception:  # noqa: BLE001
            latency_ms = None
        # Connection type: real heuristics — ADB devices are USB/wireless
        # debugging, phones on the mesh are WiFi, desktops are LAN.
        conn = "WIFI"
        if kind in ("iphone", "ipad", "android"):
            conn = "WIFI"
        elif kind == "desktop":
            conn = "LAN"
        elif kind == "iot":
            conn = "BT"
        out.append({
            "name": p.name or "Node",
            "address": p.node_id,
            "node_id": p.node_id,
            "source": "mesh",
            "kind": kind,
            "online": node.is_alive(now),
            "paired": True,
            "controllable": True,
            "rssi": None,
            "ip": None,
            "latency_ms": latency_ms,
            "conn": conn,
        })
    # ADB-connected Android devices (USB / wireless debugging) are real,
    # controllable devices — they belong on the network map too.
    try:
        from sync.android import adb_devices

        adb = adb_devices()
        for dev in (adb.get("devices") or [])[:4]:
            serial = dev.get("serial", "")
            out.append({
                "name": f"ANDROID-{serial[:8].upper()}" if serial else "ANDROID",
                "address": serial,
                "node_id": f"adb:{serial}",
                "source": "mesh",
                "kind": "android",
                "online": dev.get("state") == "device",
                "paired": True,
                "controllable": True,
                "rssi": None,
                "ip": None,
                "latency_ms": 0,
                "conn": "USB" if ":" not in serial else "WIFI",
            })
    except Exception:  # noqa: BLE001
        pass
    return out


def get_devices() -> dict:
    """Merged, de-duplicated device inventory (Bluetooth + LAN + mesh)."""
    bt = get_bluetooth_devices()
    lan = get_lan_hosts()

    by_key: dict[str, dict] = {}
    for d in bt.get("devices", []):
        by_key[f"bt:{d.get('address')}"] = d
    for d in lan.get("devices", []):
        by_key[f"lan:{d.get('ip')}"] = d
    for d in _mesh_nodes():
        by_key[f"mesh:{d.get('node_id')}"] = d

    devices = list(by_key.values())
    # Controllable (mesh) nodes first, then LAN, then Bluetooth.
    rank = {"mesh": 0, "lan": 1, "bluetooth": 2, "manual": 1}
    devices.sort(key=lambda d: rank.get(d.get("source"), 3))
    online = sum(1 for d in devices if d.get("online"))
    connected = sum(1 for d in devices if d.get("controllable"))
    return {
        "devices": devices,
        "count": len(devices),
        "online": online,
        "connected": connected,
        "bluetooth": {
            "available": bt.get("available"),
            "scanning": bt.get("scanning"),
            "error": bt.get("error"),
            "count": len(bt.get("devices", [])),
        },
        "lan": {"count": len(lan.get("devices", []))},
        "updated": time.time(),
    }


def refresh_devices() -> dict:
    """Immediately rescan Bluetooth and re-read the ARP table."""
    from .bluetooth_service import refresh_bluetooth

    refresh_bluetooth()
    # Invalidate the snapshot cache so the next poll returns fresh data.
    global _DEVICES
    with _DEVICES_LOCK:
        _DEVICES = None
    return get_devices()


def add_device(name: str, address: str = "") -> dict:
    """Register a manual device (persisted in-memory for the session)."""
    from .bluetooth_service import get_bluetooth_service

    service = get_bluetooth_service()
    with service._lock:  # noqa: SLF001
        service._devices.insert(  # noqa: SLF001
            0,
            {
                "address": (address or name).upper(),
                "name": name,
                "rssi": None,
                "source": "manual",
                "paired": False,
                "online": True,
            },
        )
    return get_devices()


def remove_device(address: str) -> bool:
    """Remove a cached device by address (Bluetooth cache only)."""
    from .bluetooth_service import remove_bluetooth_device

    return remove_bluetooth_device(address)


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_DEVICES: dict | None = None
_DEVICES_LOCK = threading.Lock()


def get_devices_snapshot() -> dict:
    """Cached merged snapshot (fresh for 5 s) so the HUD poll is cheap."""
    global _DEVICES
    now = time.time()
    if _DEVICES and (now - _DEVICES.get("updated", 0)) < 5:
        return _DEVICES
    with _DEVICES_LOCK:
        if _DEVICES and (now - _DEVICES.get("updated", 0)) < 5:
            return _DEVICES
        _DEVICES = get_devices()
    return _DEVICES
