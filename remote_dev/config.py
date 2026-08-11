"""
config.py — SSH server profiles.

Profiles live in ``config/servers.json`` (gitignored, never commit
passwords). A default ``env`` profile is synthesised from the
``A3THER_SSH_*`` environment variables:

- ``A3THER_SSH_HOST`` (required for the env profile)
- ``A3THER_SSH_USER``
- ``A3THER_SSH_PORT`` (default 22)
- ``A3THER_SSH_KEY_PATH``
- ``A3THER_SSH_PASSWORD`` (discouraged — prefer a key)
- ``A3THER_SSH_TIMEOUT`` (default 15)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from config import base_dir, get_env

# SSH profiles live in the OS app-data dir; repo copy migrates once.
try:
    from config.paths import data_path as _data_path

    SERVERS_PATH = _data_path("config/servers.json")
except Exception:  # noqa: BLE001
    SERVERS_PATH = base_dir() / "config" / "servers.json"

TEMPLATE = {
    "servers": [
        {
            "name": "example-prod",
            "host": "192.168.1.10",
            "port": 22,
            "user": "deploy",
            "key_path": "~/.ssh/id_ed25519",
            "timeout": 15,
            "description": "Rename me and point at a real server.",
        }
    ]
}


@dataclass
class ServerProfile:
    """One SSH destination."""

    name: str
    host: str
    port: int = 22
    user: str = ""
    key_path: str | None = None
    password: str | None = None
    timeout: int = 15
    strict_host_checking: bool = True
    description: str = ""
    raw: dict = field(default_factory=dict)

    def redacted(self) -> dict:
        """Dashboard-safe representation — never includes the password."""
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "key_path": self.key_path,
            "has_password": bool(self.password),
            "timeout": self.timeout,
            "strict_host_checking": self.strict_host_checking,
            "description": self.description,
        }


def ensure_config_file() -> None:
    """Create a starter servers.json when none exists."""
    if not SERVERS_PATH.exists():
        SERVERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SERVERS_PATH.write_text(json.dumps(TEMPLATE, indent=2), encoding="utf-8")


def _env_profile() -> ServerProfile | None:
    host = get_env("A3THER_SSH_HOST")
    if not host:
        return None
    return ServerProfile(
        name="env",
        host=host,
        port=int(get_env("A3THER_SSH_PORT", "22") or 22),
        user=get_env("A3THER_SSH_USER") or "",
        key_path=get_env("A3THER_SSH_KEY_PATH"),
        password=get_env("A3THER_SSH_PASSWORD"),
        timeout=int(get_env("A3THER_SSH_TIMEOUT", "15") or 15),
        description="Synthesised from A3THER_SSH_* environment variables.",
    )


def load_profiles() -> list[ServerProfile]:
    """Load all profiles from servers.json plus the env profile."""
    ensure_config_file()
    profiles: list[ServerProfile] = []
    try:
        data = json.loads(SERVERS_PATH.read_text(encoding="utf-8"))
        for item in data.get("servers") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            profiles.append(
                ServerProfile(
                    name=str(item["name"]),
                    host=str(item.get("host", "")),
                    port=int(item.get("port", 22) or 22),
                    user=str(item.get("user", "")),
                    key_path=item.get("key_path"),
                    password=item.get("password"),  # config fallback (discouraged)
                    timeout=int(item.get("timeout", 15) or 15),
                    strict_host_checking=bool(item.get("strict_host_checking", True)),
                    description=str(item.get("description", "")),
                    raw=item,
                )
            )
    except Exception:
        pass

    env_profile = _env_profile()
    if env_profile is not None:
        # Env profile wins by name unless a real one is already defined.
        if not any(p.name == "env" for p in profiles):
            profiles.append(env_profile)

    return profiles


def get_profile(name_or_host: str) -> ServerProfile | None:
    """Find a profile by name, host, or host:port suffix."""
    target = (name_or_host or "").strip()
    if not target:
        return None
    for profile in load_profiles():
        if profile.name == target or profile.host == target:
            return profile
    # Also try "<host>:<port>" style.
    for profile in load_profiles():
        if f"{profile.host}:{profile.port}" == target:
            return profile
    return None
