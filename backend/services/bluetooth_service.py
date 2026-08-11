"""
bluetooth_service.py — real Bluetooth device discovery.

Uses ``bleak`` (the cross-platform Bluetooth LE scanner) to discover nearby
Bluetooth devices. Scans run on a background thread so the API never blocks;
results are cached with a TTL and returned instantly on request.

If ``bleak`` is not installed (``pip install bleak``), the service degrades
HONESTLY — it reports ``available: false`` with a message instead of
fabricating devices. The HUD shows the real state either way.
"""
from __future__ import annotations

import logging
import threading
import time

LOGGER = logging.getLogger("a3ther.services.bluetooth")

_SCAN_INTERVAL = 30.0   # seconds between automatic rescans
_SCAN_TIMEOUT = 8.0     # bleak scan duration per pass


def _bleak_available() -> bool:
    try:
        import bleak  # noqa: F401
        return True
    except Exception:
        return False


class BluetoothService:
    """Background-scanned Bluetooth device list with cache + manual refresh."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._devices: list[dict] = []
        self._last_scan = 0.0
        self._scanning = False
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._available = _bleak_available()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_devices(self) -> dict:
        """Return cached devices + scan status (never blocks)."""
        with self._lock:
            return {
                "available": self._available,
                "scanning": self._scanning,
                "error": self._error,
                "last_scan": self._last_scan,
                "devices": list(self._devices),
            }

    def refresh(self) -> dict:
        """Trigger an immediate background rescan and return current state."""
        if not self._available:
            return self.get_devices()
        self._start_scan()
        return self.get_devices()

    def start_autoscan(self) -> None:
        """Kick off the periodic background scanner."""
        if self._available and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(
                target=self._autoscan_loop, name="bt-scanner", daemon=True
            )
            self._thread.start()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _start_scan(self) -> None:
        with self._lock:
            if self._scanning:
                return
            self._scanning = True
        threading.Thread(target=self._run_scan, name="bt-scan", daemon=True).start()

    def _autoscan_loop(self) -> None:
        while not self._stop.is_set():
            self._start_scan()
            self._stop.wait(_SCAN_INTERVAL)

    def _run_scan(self) -> None:
        """Run one bounded scan. bleak's API is async — each scan gets its
        own asyncio loop on this background thread."""
        try:
            import asyncio

            asyncio.run(self._scan_async())
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Bluetooth scan failed: %s", exc)
            with self._lock:
                self._error = str(exc)
        finally:
            with self._lock:
                self._scanning = False

    async def _scan_async(self) -> None:
        """Async scan pass — real bleak coroutines, bounded by timeout."""
        import asyncio

        from bleak import BleakScanner

        LOGGER.info("Bluetooth scan started (timeout=%ss)", _SCAN_TIMEOUT)
        devices: list[dict] = []

        def _cb(device, advertisement_data) -> None:  # noqa: ANN001
            # Dedupe by address, keep the strongest signal + latest name.
            addr = (device.address or "").upper()
            if not addr:
                return
            name = (device.name or (advertisement_data and advertisement_data.local_name) or "").strip()
            rssi = getattr(advertisement_data, "rssi", None) or getattr(device, "rssi", None)
            for existing in devices:
                if existing["address"] == addr:
                    if rssi is not None and (existing.get("rssi") is None or rssi > existing["rssi"]):
                        existing["rssi"] = rssi
                    if name and not existing["name"]:
                        existing["name"] = name
                    return
            devices.append(
                {
                    "address": addr,
                    "name": name or "Unknown Device",
                    "rssi": rssi,
                    "source": "bluetooth",
                    "paired": False,
                    "online": True,
                }
            )

        scanner = BleakScanner(detection_callback=_cb)
        try:
            await scanner.start()
            # Bounded wait — never let a hung adapter block the app.
            deadline = time.time() + _SCAN_TIMEOUT
            while time.time() < deadline and not self._stop.is_set():
                await asyncio.sleep(0.5)
        finally:
            try:
                await scanner.stop()
            except Exception:  # noqa: BLE001
                pass

        devices.sort(key=lambda d: d["rssi"] if d.get("rssi") is not None else -999, reverse=True)
        with self._lock:
            self._devices = devices
            self._last_scan = time.time()
            self._error = None
        LOGGER.info("Bluetooth scan complete: %d device(s)", len(devices))


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_SERVICE: BluetoothService | None = None
_SERVICE_LOCK = threading.Lock()


def get_bluetooth_service() -> BluetoothService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = BluetoothService()
    return _SERVICE


def get_bluetooth_devices() -> dict:
    """Convenience entry point — starts autoscan on first call."""
    service = get_bluetooth_service()
    service.start_autoscan()
    return service.get_devices()


def refresh_bluetooth() -> dict:
    """Convenience entry point for an immediate rescan."""
    return get_bluetooth_service().refresh()


def remove_bluetooth_device(address: str) -> bool:
    """Remove a cached Bluetooth device entry (does not unpair hardware)."""
    service = get_bluetooth_service()
    with service._lock:  # noqa: SLF001 — service internals, same package
        before = len(service._devices)  # noqa: SLF001
        service._devices = [  # noqa: SLF001
            d for d in service._devices if d.get("address", "").upper() != (address or "").upper()
        ]
        return len(service._devices) < before
