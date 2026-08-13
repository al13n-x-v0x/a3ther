"""
config/paths.py — where A3THER keeps its state.

The authoritative state folder lives in the OS app-data directory
(``%LOCALAPPDATA%/A3THER`` on Windows, ``~/.a3ther`` elsewhere) so it
survives reinstalls, is per-user, and never ships inside the repo or the
frozen exe. ``migrate_all()`` copies any legacy repo-relative state into the
app-data folder on first launch.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Dict, List

__all__ = ["get_data_dir", "data_path", "migrate_all"]


def get_data_dir() -> Path:
    """The A3THER state folder (created on demand)."""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        data_dir = Path(base) / "A3THER"
    else:
        data_dir = Path.home() / ".a3ther"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return data_dir


def data_path(relative: str) -> Path:
    """Resolve a path relative to the A3THER data folder (safe join)."""
    return get_data_dir() / relative


def _repo_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


#: Repo-relative state files that get migrated into app-data on first run.
_MIGRATE_CANDIDATES: List[str] = [
    "config/api_keys.json",
    "config/servers.json",
    "config/mcp_state.json",
]


def migrate_all() -> Dict[str, bool]:
    """Copy legacy repo-relative state into the app-data folder.

    Returns ``{filename: moved}`` for the caller to log. Existing app-data
    files win (never overwrite newer state with stale repo copies).
    """
    migrated: Dict[str, bool] = {}
    repo = _repo_root()
    data_dir = get_data_dir()
    for rel in _MIGRATE_CANDIDATES:
        src = repo / rel
        if not src.exists():
            continue
        dst = data_dir / rel
        try:
            if dst.exists():
                migrated[rel] = False  # already present — keep app-data copy
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            migrated[rel] = True
        except Exception:  # noqa: BLE001
            migrated[rel] = False
    return migrated


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    print("data dir:", get_data_dir())
    print("migrated:", migrate_all())
