"""
core/engine_state.py — shared runtime state for the boot engine (main.py).

The engine and the FastAPI app run in the same process; this tiny module is
the bridge between them. The engine pushes preflight results, USB-watcher
state and log lines here; ``GET /api/engine/status`` exposes them so the HUD
can show the boot log without the engine importing the API layer.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_MAX_EVENTS = 200

#: Engine runtime state (mutable singleton).
STATE: dict = {
    "preflight": {"adb": None, "ffmpeg": None, "done": False},
    "usb_running": False,
    "usb_devices": [],
    "started_at": None,
    "events": [],  # ring buffer of log lines
}


def push_event(text: str) -> None:
    """Append one engine log line to the ring buffer (thread-safe)."""
    with _lock:
        STATE["events"].append(text)
        if len(STATE["events"]) > _MAX_EVENTS:
            del STATE["events"][: len(STATE["events"]) - _MAX_EVENTS]


def set_preflight(adb: dict, ffmpeg: dict) -> None:
    with _lock:
        STATE["preflight"] = {"adb": adb, "ffmpeg": ffmpeg, "done": True}


def set_usb_running(running: bool, devices: list | None = None) -> None:
    with _lock:
        STATE["usb_running"] = bool(running)
        if devices is not None:
            STATE["usb_devices"] = devices


def snapshot() -> dict:
    """Thread-safe copy for API responses."""
    with _lock:
        return {
            "preflight": dict(STATE["preflight"]),
            "usb_running": STATE["usb_running"],
            "usb_devices": list(STATE["usb_devices"]),
            "started_at": STATE["started_at"],
            "events": list(STATE["events"]),
        }
