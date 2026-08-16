"""
core/startup.py — "Start with Windows" management.

Installs / removes a per-user autostart entry so A3THER boots with Windows.
Uses the HKCU ``Software\\Microsoft\\Windows\\CurrentVersion\\Run`` registry
key (no admin rights needed). The command is the app's real entry point:
the frozen exe when running frozen, otherwise ``pythonw main.py --background``
so the app starts hidden and is summoned via the tray icon or Alt+F1.

Pure stdlib (``winreg``) — no third-party deps. Non-Windows: every call
returns False with a clear reason.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY_NAME = "A3THER"


def supported() -> bool:
    """True on Windows (where the Run key exists)."""
    return os.name == "nt"


def _command_line() -> str:
    """The exact command autostart should run (quoted for the registry)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    # Prefer pythonw so no console window flashes at login.
    exe = _find_pythonw()
    script = Path(sys.argv[0]).resolve()
    return f'"{exe}" "{script}" --background'


def _find_pythonw() -> str:
    """Locate pythonw.exe next to the current interpreter (fallback: python.exe)."""
    base = Path(sys.executable).resolve()
    candidates = [
        base.with_name("pythonw.exe"),
        base.with_name("python.exe"),
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return str(base)


def get_startup() -> bool:
    """True when the A3THER Run entry is installed (and non-empty)."""
    if not supported():
        return False
    import winreg  # noqa: PLC0415

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, ENTRY_NAME)
        return bool(value and value.strip())
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_startup(enabled: bool) -> dict:
    """Install (``enabled=True``) or remove the autostart entry.

    Returns ``{"ok": bool, "enabled": bool, "command": str}`` so the HUD can
    show exactly what Windows will run.
    """
    if not supported():
        return {"ok": False, "enabled": False, "command": "", "error": "Windows only"}
    import winreg  # noqa: PLC0415

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = _command_line()
                winreg.SetValueEx(key, ENTRY_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, ENTRY_NAME)
                except FileNotFoundError:
                    pass  # not installed — that's the desired state
        return {"ok": True, "enabled": get_startup(), "command": _command_line() if enabled else ""}
    except OSError as exc:  # noqa: BLE001
        return {"ok": False, "enabled": get_startup(), "command": "", "error": str(exc)}


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    print("supported:", supported())
    print("currently:", get_startup())
    print("command would be:", _command_line())
