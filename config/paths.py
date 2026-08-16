"""
config/paths.py — where A3THER keeps its *state*.

Secrets, memory, MCP/SSH configs and caches live in the OS application-data
directory (NOT the repository), so a git checkout never contains user data
and the state survives updates:

- Windows: ``%LOCALAPPDATA%/A3THER``
- macOS:   ``~/Library/Application Support/A3THER``
- Linux:   ``$XDG_DATA_HOME/a3ther`` or ``~/.local/share/a3ther``

The first time any module asks for a state path, existing repo-local copies
(``config/api_keys.json``, ``memory/long_term.json``, …) are migrated over
once, so a long-standing install keeps its data with zero manual steps.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """Absolute path of the A3THER application-data directory."""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "A3THER"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "A3THER"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "a3ther"


def ensure_data_dir() -> Path:
    """Create the data directory if needed; returns it."""
    data_dir = get_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 — read-only home, fall back to repo
        data_dir = _REPO_DIR / ".a3ther-state"
        data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


#: Repo-relative files that get migrated into the data dir on first access.
_MIGRATABLE = (
    "config/api_keys.json",
    "config/servers.json",
    "config/mcp-servers.json",
    "config/weather_override.json",
    "config/security_policy.json",
    "memory/long_term.json",
    "backend/database/memory.db",
    "memory/vector_store.json",
    "memory/knowledge_graph.json",
)


def _migrate_file(repo_path: Path, target: Path) -> bool:
    """Copy a repo state file into the data dir once. Returns True if copied."""
    if target.exists() or not repo_path.exists():
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(repo_path.read_bytes())
        return True
    except Exception:  # noqa: BLE001
        return False


def data_path(name: str) -> Path:
    """Resolve ``name`` (relative to the data dir), migrating a repo copy first.

    The migration is lazy and idempotent: the first caller for a given file
    copies the repo version in if the data-dir copy is missing, so every
    consumer stays consistent no matter the import order.
    """
    ensure_data_dir()
    target = get_data_dir() / name
    if not target.exists():
        _migrate_file(_REPO_DIR / name, target)
    return target


def migrate_all() -> dict:
    """One-shot migration of every known state file. Returns {name: copied}."""
    ensure_data_dir()
    results: dict[str, bool] = {}
    for rel in _MIGRATABLE:
        results[rel] = _migrate_file(_REPO_DIR / rel, get_data_dir() / rel)
    return results


def repo_dir() -> Path:
    """The A3THER repository root."""
    return _REPO_DIR


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
