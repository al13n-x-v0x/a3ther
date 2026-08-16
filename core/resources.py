"""
core/resources.py — resolve bundled asset paths.

Assets (the A3THER logo + variants) live in ``assets/`` at the repo root.
In a frozen PyInstaller build they are bundled via the spec's ``datas`` and
land inside the bundle (``sys._MEIPASS`` for onefile, the ``_internal``
folder for onedir). This helper finds them in both modes, so the tray icon,
the quick popup, the HUD favicon and the exe itself can all use the real logo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ASSETS_DIR_NAME = "assets"


def assets_dir() -> str:
    """Return the directory that holds the bundled assets (existing)."""
    # Frozen: PyInstaller extracts the bundle to sys._MEIPASS (onefile) or
    # uses the app dir (onedir). Assets are copied next to the code there.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / _ASSETS_DIR_NAME
        if candidate.is_dir():
            return str(candidate)
    # Source checkout: assets/ sits next to the core/ package.
    candidate = Path(__file__).resolve().parent.parent / _ASSETS_DIR_NAME
    if candidate.is_dir():
        return str(candidate)
    return str(candidate)


def asset_path(name: str) -> str:
    """Full path to a bundled asset (logo.png, logo.ico, logo_tray.png, …)."""
    return os.path.join(assets_dir(), name)


def asset_exists(name: str) -> bool:
    return os.path.isfile(asset_path(name))
