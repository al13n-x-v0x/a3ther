# config/__init__.py
"""
A3THER configuration package.

Provides:
- ``get_config()``   — read ``config/api_keys.json``
- ``get_os()``       — platform detection ('windows' | 'mac' | 'linux')
- ``base_dir()``     — repository root (works frozen and from source)
- ``load_env_file()``/``get_env()`` — env-first secrets: a tiny dependency-free
  ``.env`` loader. Every secret in A3THER is resolved *environment first*,
  then falls back to ``config/api_keys.json`` (see gateway/router.py).
"""
import json
import os
import platform
from pathlib import Path

from . import paths as _paths

# Authoritative copy lives in the OS app-data dir; the repo copy stays as a
# compatibility mirror so the actions/* modules that read it directly keep
# working untouched.
_CONFIG_PATH = _paths.data_path("config/api_keys.json")
_MIRROR_PATH = Path(__file__).parent / "api_keys.json"

# Cache for the .env loader so we only parse the file once per process.
_ENV_LOADED = False


def _platform_os() -> str:
    """Auto-detect OS when config file is absent."""
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )


def base_dir() -> Path:
    """Absolute path of the repository root (parent of this package)."""
    if getattr(__import__("sys"), "frozen", False):
        return Path(__import__("sys").executable).parent
    return Path(__file__).resolve().parent.parent


def get_config() -> dict:
    """Return the JSON contents of the A3THER config file (or {}).

    Reads the app-data copy first (authoritative); falls back to the repo
    mirror so a checkout without prior migration still works.
    """
    for path in (_CONFIG_PATH, _MIRROR_PATH):
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            continue
    return {}


def save_config(update: dict) -> dict:
    """Merge ``update`` into the config and persist to BOTH the app-data
    copy (authoritative) and the repo mirror (compat for actions/*)."""
    data = get_config()
    data.update(update or {})
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    for path in (_CONFIG_PATH, _MIRROR_PATH):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
    return data


def config_path() -> Path:
    """The authoritative config file path (app-data)."""
    return _CONFIG_PATH


def get_os() -> str:
    """Returns: 'windows' | 'mac' | 'linux'"""
    return get_config().get("os_system", _platform_os()).lower()


def is_windows() -> bool:
    return get_os() == "windows"


def is_mac() -> bool:
    return get_os() == "mac"


def is_linux() -> bool:
    return get_os() == "linux"


def load_env_file(path: str | Path | None = None) -> None:
    """Parse a ``.env`` file at the repository root (or ``path``) into os.environ.

    Values already present in ``os.environ`` are never overwritten, so real
    environment variables always win over the ``.env`` file. Missing files are
    silently ignored. Parsing is cached per process.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    env_path = Path(path) if path is not None else base_dir() / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_env(name: str, default: str | None = None) -> str | None:
    """Return an environment variable, loading the root ``.env`` first if needed."""
    load_env_file()
    return os.environ.get(name, default)
