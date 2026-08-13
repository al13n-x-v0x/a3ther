"""
actions/file_controller.py — FILE intent.

Lists files in a folder (default: the project root). Returns a short
summary — enough for the brain to answer "what files are here?" without a
full filesystem walk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict


def _default_root() -> Path:
    # Prefer the A3THER data dir; fall back to the repo root.
    try:
        from config import base_dir

        return base_dir()
    except Exception:  # noqa: BLE001
        return Path.home()


def list_files(params: Dict[str, Any] | None = None) -> str:
    target = (params or {}).get("path") or _default_root()
    try:
        root = Path(target).expanduser().resolve()
        if not root.exists():
            return f"Folder not found: {root}"
        entries = sorted(
            p for p in root.iterdir() if not p.name.startswith((".", "__"))
        )
        if not entries:
            return f"{root} is empty."
        dirs = [p.name for p in entries if p.is_dir()]
        files = [p.name for p in entries if p.is_file()]
        lines = [f"{root} — {len(dirs)} folders, {len(files)} files:"]
        for name in dirs[:12]:
            lines.append(f"  [dir]  {name}")
        for name in files[:20]:
            lines.append(f"        {name}")
        if len(dirs) > 12 or len(files) > 20:
            lines.append("  … (truncated)")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Could not list files: {exc}"


if __name__ == "__main__":  # pragma: no cover
    print(list_files({}))
