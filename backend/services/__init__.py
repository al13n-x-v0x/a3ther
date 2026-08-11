"""
A3THER backend services — real system telemetry, location, weather, Bluetooth.

Every module degrades gracefully when an optional dependency or the network
is unavailable, so importing the package never raises.
"""
from __future__ import annotations

from .stats_service import get_stats_snapshot, get_top_processes

__all__ = [
    "get_stats_snapshot",
    "get_top_processes",
    "get_location",
    "get_city_label",
    "get_weather",
    "get_bluetooth_devices",
    "remove_bluetooth_device",
    "refresh_bluetooth",
    "get_devices",
    "add_device",
    "remove_device",
    "refresh_devices",
    "get_devices_snapshot",
]

# Lazily imported so a missing optional dependency in one service never
# breaks imports of the others or of the package itself.
def __getattr__(name: str):
    if name in ("get_location", "get_city_label"):
        from .location_service import get_city_label, get_location

        return {"get_location": get_location, "get_city_label": get_city_label}[name]
    if name == "get_weather":
        from .weather_service import get_weather

        return get_weather
    if name in ("get_bluetooth_devices", "remove_bluetooth_device", "refresh_bluetooth"):
        from .bluetooth_service import (
            get_bluetooth_devices,
            refresh_bluetooth,
            remove_bluetooth_device,
        )

        return {
            "get_bluetooth_devices": get_bluetooth_devices,
            "remove_bluetooth_device": remove_bluetooth_device,
            "refresh_bluetooth": refresh_bluetooth,
        }[name]
    if name in ("get_devices", "add_device", "remove_device", "refresh_devices", "get_devices_snapshot"):
        from .device_service import (
            add_device,
            get_devices,
            get_devices_snapshot,
            refresh_devices,
            remove_device,
        )

        return {
            "get_devices": get_devices,
            "add_device": add_device,
            "remove_device": remove_device,
            "refresh_devices": refresh_devices,
            "get_devices_snapshot": get_devices_snapshot,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
