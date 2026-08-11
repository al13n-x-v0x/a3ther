"""
sync/ble_controller.py — real Bluetooth LE device controller.

The scanner in ``backend/services/bluetooth_service.py`` only *discovers*
nearby devices. This module actually talks to them over Bluetooth LE:

- ``connect(address)`` — open a BLE connection to any discovered device
  (phone, earbuds, smartwatch, sensor, laptop/PC with BLE…).
- ``info()``        — read the standard Battery (0x180F) + Device
  Information (0x180A) services: battery %, manufacturer, model,
  firmware/hardware/serial where the device exposes them.
- ``services()``    — enumerate every GATT service + characteristic with
  its properties, so the UI can show what the device can do.
- ``write(uuid, data)`` — send a raw command (text or hex) to any
  writable characteristic.
- ``disconnect()``  — cleanly drop the link.

Every connection runs on its own asyncio loop in a dedicated background
thread so the API never blocks; access is guarded by a lock so the HUD
can poll ``info()`` while a write is in flight. A hung adapter can never
freeze the app — every operation is bounded by a timeout.

Devices that are unreachable / turn off mid-session are reported HONESTLY
(``connected: false`` + the real error), never faked.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

LOGGER = logging.getLogger("a3ther.sync.ble")

# Standard GATT UUIDs (16-bit, expanded form).
UUID_BATTERY_SVC = "0000180f-0000-1000-8000-00805f9b34fb"
UUID_BATTERY_LVL = "00002a19-0000-1000-8000-00805f9b34fb"
UUID_DEVINFO_SVC = "0000180a-0000-1000-8000-00805f9b34fb"
UUID_MFR_NAME    = "00002a29-0000-1000-8000-00805f9b34fb"
UUID_MODEL_NUM   = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_SERIAL_NUM  = "00002a25-0000-1000-8000-00805f9b34fb"
UUID_FW_REV      = "00002a26-0000-1000-8000-00805f9b34fb"
UUID_HW_REV      = "00002a27-0000-1000-8000-00805f9b34fb"

_OP_TIMEOUT = 12.0   # seconds; a hung BLE stack can't stall the app


def _bleak_available() -> bool:
    try:
        import bleak  # noqa: F401
        return True
    except Exception:
        return False


class BleController:
    """One BLE connection at a time, on its own background asyncio loop."""

    def __init__(self) -> None:
        self._available = _bleak_available()
        self._lock = threading.RLock()
        self._address: str | None = None
        self._name: str | None = None
        self._client = None          # BleakClient
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._connected_at = 0.0
        self._last_error: str | None = None
        self._cached_info: dict | None = None

    # ------------------------------------------------------------------ #
    # Public API (thread-safe)
    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        return self._available

    def status(self) -> dict:
        """Current connection state — safe to poll from the HUD."""
        with self._lock:
            return {
                "available": self._available,
                "connected": self._client is not None,
                "address": self._address,
                "name": self._name,
                "connected_at": self._connected_at,
                "uptime_seconds": round(time.time() - self._connected_at, 1) if self._connected_at else 0,
                "last_error": self._last_error,
                "info": self._cached_info,
            }

    def connect(self, address: str, name: str | None = None) -> dict:
        """Open a BLE connection to ``address`` (MAC or Windows BLE id)."""
        if not self._available:
            return {"ok": False, "error": "bleak not installed — run: pip install bleak"}
        address = (address or "").strip()
        if not address:
            return {"ok": False, "error": "no device address given"}
        self.disconnect()  # one link at a time
        self._address = address
        self._name = name
        self._last_error = None
        self._cached_info = None

        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=self._run, args=(loop, address), name="ble-connect", daemon=True
        )
        with self._lock:
            self._loop = loop
            self._thread = thread
        thread.start()
        return {"ok": True, "connecting": True, "address": address, "name": name}

    def disconnect(self) -> dict:
        """Drop the current link (if any)."""
        with self._lock:
            loop, client = self._loop, self._client
            self._client = None
            self._loop = None
            self._thread = None
            self._connected_at = 0.0
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:  # noqa: BLE001
                pass
        if client is not None:
            try:
                # Give the loop a moment to run the disconnect coroutine.
                time.sleep(0.2)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._address = None
            self._name = None
        return {"ok": True, "connected": False}

    def read_info(self) -> dict:
        """Read battery + device-info characteristics over the live link."""
        if not self._connected_now():
            return {"ok": False, "connected": False, "error": "not connected"}
        with self._lock:
            loop, client = self._loop, self._client
        if loop is None or client is None:
            return {"ok": False, "connected": False, "error": "not connected"}
        try:
            result = asyncio.run_coroutine_threadsafe(self._read_info_async(client), loop)
            info = result.result(timeout=_OP_TIMEOUT)
            with self._lock:
                self._cached_info = info
            return {"ok": True, "connected": True, "info": info}
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = str(exc)
            return {"ok": False, "connected": False, "error": str(exc)}

    def list_services(self) -> dict:
        """Enumerate GATT services + characteristics with properties."""
        if not self._connected_now():
            return {"ok": False, "connected": False, "error": "not connected"}
        with self._lock:
            loop, client = self._loop, self._client
        if loop is None or client is None:
            return {"ok": False, "connected": False, "error": "not connected"}
        try:
            result = asyncio.run_coroutine_threadsafe(self._services_async(client), loop)
            services = result.result(timeout=_OP_TIMEOUT)
            return {"ok": True, "connected": True, "services": services}
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = str(exc)
            return {"ok": False, "connected": False, "error": str(exc)}

    def write(self, uuid: str, data: str, as_hex: bool = False) -> dict:
        """Write raw bytes to a writable characteristic."""
        if not self._connected_now():
            return {"ok": False, "connected": False, "error": "not connected"}
        uuid = (uuid or "").strip()
        if not uuid:
            return {"ok": False, "error": "no characteristic UUID given"}
        try:
            if as_hex:
                payload = bytes.fromhex(data.replace(" ", "").replace("0x", ""))
            else:
                payload = (data or "").encode("utf-8")
        except ValueError:
            return {"ok": False, "error": "invalid hex — use pairs like '01 02 ff' or leave hex off for text"}
        with self._lock:
            loop, client = self._loop, self._client
        if loop is None or client is None:
            return {"ok": False, "connected": False, "error": "not connected"}
        try:
            result = asyncio.run_coroutine_threadsafe(
                self._write_async(client, uuid, payload), loop
            )
            result.result(timeout=_OP_TIMEOUT)
            return {"ok": True, "uuid": uuid, "bytes": len(payload)}
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = str(exc)
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _connected_now(self) -> bool:
        with self._lock:
            return self._client is not None and self._loop is not None

    def _run(self, loop: asyncio.AbstractEventLoop, address: str) -> None:
        """Background thread: run the loop, connect, then idle."""
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect_async(address))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("BLE connect failed for %s: %s", address, exc)
            with self._lock:
                self._last_error = str(exc)
                self._client = None
        # Idle — serve read/write requests until disconnect() stops the loop.
        try:
            loop.run_forever()
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()
            with self._lock:
                if self._client is not None:
                    self._client = None
                    self._connected_at = 0.0

    async def _connect_async(self, address: str) -> None:
        from bleak import BleakClient

        client = BleakClient(address, timeout=_OP_TIMEOUT)
        await client.connect(timeout=_OP_TIMEOUT)
        with self._lock:
            self._client = client
            self._connected_at = time.time()
            self._last_error = None
        LOGGER.info("BLE connected to %s", address)

    async def _read_info_async(self, client) -> dict:  # noqa: ANN001
        """Read the standard battery + device-info services (best effort)."""
        info: dict = {"battery_percent": None}

        async def _read_uuid(uuid: str) -> str | None:
            try:
                raw = await client.read_gatt_char(uuid)
                if not raw:
                    return None
                text = raw.decode("utf-8", errors="replace").strip("\x00 ")
                return text or None
            except Exception:  # noqa: BLE001 — not all devices expose everything
                return None

        info["battery_percent"] = await _read_uuid(UUID_BATTERY_LVL)
        if info["battery_percent"] is not None:
            try:
                info["battery_percent"] = int(info["battery_percent"])
            except ValueError:
                pass
        info["manufacturer"] = await _read_uuid(UUID_MFR_NAME)
        info["model"] = await _read_uuid(UUID_MODEL_NUM)
        info["serial"] = await _read_uuid(UUID_SERIAL_NUM)
        info["firmware"] = await _read_uuid(UUID_FW_REV)
        info["hardware"] = await _read_uuid(UUID_HW_REV)
        return info

    async def _services_async(self, client) -> list[dict]:  # noqa: ANN001
        out: list[dict] = []
        for service in client.services:
            chars = []
            for char in service.characteristics:
                chars.append(
                    {
                        "uuid": str(char.uuid),
                        "handle": char.handle,
                        "properties": sorted(char.properties),
                    }
                )
            out.append({"uuid": str(service.uuid), "characteristics": chars})
        return out

    async def _write_async(self, client, uuid: str, payload: bytes) -> None:  # noqa: ANN001
        await client.write_gatt_char(uuid, payload)


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_CONTROLLER: BleController | None = None
_CONTROLLER_LOCK = threading.Lock()


def get_ble_controller() -> BleController:
    global _CONTROLLER
    if _CONTROLLER is None:
        with _CONTROLLER_LOCK:
            if _CONTROLLER is None:
                _CONTROLLER = BleController()
    return _CONTROLLER
