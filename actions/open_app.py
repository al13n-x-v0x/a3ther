"""
actions/open_app.py — OPEN_APP intent.

Launches a desktop application by name. On Windows this uses the ``start``
command (resolves registered app names like ``notepad``, ``chrome``,
``vscode``); other platforms fall back to ``shutil.which`` + ``Popen``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Any, Dict


def open_app(params: Dict[str, Any] | None = None) -> str:
    app = ((params or {}).get("app_name") or "").strip()
    if not app:
        return "No application name provided. Tell me which app to open."
    app = app.strip().lower()

    # Strip common phrasing so "open notepad" and "launch chrome" both work.
    for word in ("open ", "launch ", "start ", "please "):
        if app.startswith(word):
            app = app[len(word):].strip()
            break
    if not app:
        return "No application name left after cleanup."

    try:
        if os.name == "nt":
            # `start` resolves registered names (notepad, mspaint, …) AND
            # common executable names; Popen with shell avoids quoting pain.
            subprocess.Popen(
                ["cmd", "/c", "start", "", app],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return f"Opening {app}…"
        # POSIX: try the PATH first, then xdg-open as a last resort.
        exe = shutil.which(app)
        if exe:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opening {app}…"
        subprocess.Popen(["xdg-open", app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opening {app}…"
    except Exception as exc:  # noqa: BLE001
        return f"Could not open {app}: {exc}"


if __name__ == "__main__":  # pragma: no cover
    print(open_app({"app_name": "notepad"}))
