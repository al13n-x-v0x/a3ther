"""
core/silent.py — run subprocesses without flashing console windows.

On Windows, ``subprocess`` children inherit a console by default — that's
the PowerShell window that pops up when the HUD reads GPU specs, when a
toast notification fires, when an MCP stdio server starts, etc.

This module provides two things:

* ``activate()`` — a global, idempotent monkeypatch of ``subprocess.Popen``
  that injects ``CREATE_NO_WINDOW`` (+ a hidden-window ``STARTUPINFO``) into
  every child unless the caller already set ``creationflags``. Call it once
  at boot (launcher.py / main.py) and NO subprocess — ours or a library's —
  can flash a console again.

* ``run`` / ``popen`` / ``check_output`` — drop-in wrappers for call sites
  that want to be explicit about it.
"""

from __future__ import annotations

import subprocess
import sys

if sys.platform == "win32":
    _CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    def _hide_kwargs(kwargs: dict) -> dict:
        if kwargs.get("creationflags") is None:
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        if kwargs.get("startupinfo") is None and hasattr(subprocess, "STARTUPINFO"):
            info = subprocess.STARTUPINFO()
            info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = info
        return kwargs

else:

    def _hide_kwargs(kwargs: dict) -> dict:  # noqa: ARG001
        return kwargs


_ACTIVATED = False


def activate() -> None:
    """Globally hide console windows for every subprocess (idempotent)."""
    global _ACTIVATED
    if _ACTIVATED or sys.platform != "win32":
        return
    _orig_init = subprocess.Popen.__init__

    def _patched_init(self, *args: object, **kwargs: object) -> None:
        if isinstance(kwargs, dict):
            kwargs = dict(kwargs)
            _hide_kwargs(kwargs)  # type: ignore[arg-type]
        _orig_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _patched_init  # type: ignore[method-assign]
    _ACTIVATED = True


# --------------------------------------------------------------------------- #
# Explicit wrappers (belt and braces for the hot spots)
# --------------------------------------------------------------------------- #


def run(args, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, **_hide_kwargs(kwargs))


def popen(args, **kwargs) -> subprocess.Popen:
    return subprocess.Popen(args, **_hide_kwargs(kwargs))


def check_output(args, **kwargs) -> bytes:
    return subprocess.check_output(args, **_hide_kwargs(kwargs))


def check_call(args, **kwargs) -> int:
    return subprocess.check_call(args, **_hide_kwargs(kwargs))
