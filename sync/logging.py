"""
sync/logging.py — color-coded logging for the mesh.

ANSI 256-color tags per subsystem. Colors are only emitted when the
stream supports them (Windows terminals, or any tty), so logs piped to
files stay clean.
"""
from __future__ import annotations

import sys

# ── ANSI helpers ────────────────────────────────────────────────────────────
_CODES = {
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "cyan": "\x1b[36m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "red": "\x1b[31m",
}

_TAG_COLORS = {
    "MESH": "cyan",
    "NODE": "blue",
    "BROADCAST": "magenta",
    "MOBILE": "green",
    "FAILSAFE": "red",
    "SYNC": "yellow",
}


def _supports_color() -> bool:
    try:
        return bool(sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


_COLOR = _supports_color()


def _wrap(tag: str, text: str) -> str:
    color = _TAG_COLORS.get(tag.upper(), "dim")
    if not _COLOR:
        return f"[{tag}] {text}"
    code = _CODES.get(color, _CODES["dim"])
    return f"{code}{_CODES['bold']}[{tag}]{_CODES['reset']} {_CODES[color]}{text}{_CODES['reset']}"


def log(tag: str, message: str) -> None:
    """Print one color-tagged line: ``[TAG] message``."""
    try:
        print(_wrap(tag, message))
    except Exception:
        print(f"[{tag}] {message}")
