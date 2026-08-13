"""
actions/computer_control.py — COMPUTER intent.

System-level actions: lock the workstation, screenshot, volume/brightness
(optional libs loaded lazily). Import-safe: nothing heavy at module level.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Dict


def lock() -> str:
    try:
        if os.name == "nt":
            import ctypes  # noqa: PLC0415

            ctypes.windll.user32.LockWorkStation()
            return "PC locked."
        subprocess.Popen(["loginctl", "lock-session"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "PC locked."
    except Exception as exc:  # noqa: BLE001
        return f"Could not lock the PC: {exc}"


def screenshot(folder: str | None = None) -> str:
    try:
        from PIL import ImageGrab  # type: ignore  # noqa: PLC0415

        shot = ImageGrab.grab()
        if not folder:
            try:
                from config.paths import get_data_dir

                folder = str(get_data_dir() / "screenshots")
            except Exception:  # noqa: BLE001
                folder = os.path.expanduser("~")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, time.strftime("shot-%Y%m%d-%H%M%S.png"))
        shot.save(path)
        return f"Screenshot saved → {path}"
    except Exception as exc:  # noqa: BLE001
        return f"Screenshot failed (Pillow installed?): {exc}"


def computer_control(params: Dict[str, Any] | None = None) -> str:
    params = params or {}
    action = (params.get("action") or "").lower()
    if action in ("lock", "lock_pc", "lock workstation"):
        return lock()
    if action in ("screenshot", "shot", "capture"):
        return screenshot(params.get("folder"))
    return "Supported actions: lock, screenshot."


if __name__ == "__main__":  # pragma: no cover
    print(computer_control({"action": "lock"}))
