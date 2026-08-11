"""
registry.py — dynamic MCP server configuration.

Reads ``config/mcp-servers.json``, hot-reloads it when the file changes,
and persists per-server *enabled* toggles (from the dashboard) in a
separate state file so the registry file itself stays a pure template.

``config/mcp-servers.json`` shape
---------------------------------
.. code-block:: json

    {
      "servers": [
        {
          "name": "filesystem",
          "transport": "stdio",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
          "env": {},
          "enabled": true
        },
        {
          "name": "sqlite",
          "transport": "http",
          "url": "http://127.0.0.1:8001",
          "headers": {},
          "enabled": false
        }
      ]
    }
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from config import base_dir


@dataclass
class ServerConfig:
    """One entry from mcp-servers.json."""

    name: str
    transport: str = "stdio"          # "stdio" | "http"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    enabled: bool = True
    raw: dict = field(default_factory=dict)


TEMPLATE = {
    "servers": [
        {
            "name": "example-filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
            "env": {},
            "enabled": False,
            "description": "Sample stdio server — install Node and enable to try.",
        },
        {
            "name": "example-http",
            "transport": "http",
            "url": "http://127.0.0.1:8001",
            "headers": {},
            "enabled": False,
            "description": "Sample HTTP server — point at any JSON-RPC endpoint.",
        },
    ]
}


class MCPRegistry:
    """Loads and hot-reloads MCP server config."""

    def __init__(self, config_path: str | Path | None = None, state_path: str | Path | None = None):
        # Config + state live in the OS app-data dir; repo copies migrate once.
        try:
            from config.paths import data_path as _data_path

            default_config = _data_path("config/mcp-servers.json")
            default_state = _data_path("config/mcp_state.json")
        except Exception:  # noqa: BLE001
            default_config = base_dir() / "config" / "mcp-servers.json"
            default_state = base_dir() / "config" / "mcp_state.json"
        self.config_path = Path(config_path or default_config)
        self.state_path = Path(state_path or default_state)
        self._lock = threading.RLock()
        self._mtime: float | None = None
        self.servers: list[ServerConfig] = []
        self._enabled_state: dict[str, bool] = {}

        self._ensure_config_file()
        self._load_state()
        self.discover(force=True)

    # ------------------------------------------------------------------ #
    def _ensure_config_file(self) -> None:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(TEMPLATE, indent=2), encoding="utf-8"
            )

    def _load_state(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._enabled_state = {
                k: bool(v) for k, v in (data.get("servers") or {}).items()
            }
        except Exception:
            self._enabled_state = {}

    def _save_state(self) -> None:
        try:
            self.state_path.write_text(
                json.dumps({"servers": self._enabled_state}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    def discover(self, force: bool = False) -> list[ServerConfig]:
        """Reload from disk when the file changed (or ``force``)."""
        with self._lock:
            try:
                mtime = self.config_path.stat().st_mtime
            except Exception:
                return list(self.servers)

            if not force and self._mtime == mtime:
                return list(self.servers)
            self._mtime = mtime

            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return list(self.servers)

            servers: list[ServerConfig] = []
            for item in data.get("servers") or []:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                # Enabled state: persisted toggle wins, else manifest default.
                enabled = self._enabled_state.get(
                    item["name"], bool(item.get("enabled", True))
                )
                servers.append(
                    ServerConfig(
                        name=item["name"],
                        transport=str(item.get("transport", "stdio")).lower(),
                        command=item.get("command"),
                        args=list(item.get("args") or []),
                        url=item.get("url"),
                        headers=dict(item.get("headers") or {}),
                        env=dict(item.get("env") or {}),
                        enabled=enabled,
                        raw=item,
                    )
                )
            self.servers = servers
            return list(servers)

    def reload(self) -> bool:
        """Hot-reload; returns True when the file changed and was re-read."""
        before = self._mtime
        self.discover(force=False)
        return self._mtime != before

    def get(self, name: str) -> ServerConfig | None:
        self.discover()
        for server in self.servers:
            if server.name == name:
                return server
        return None

    def set_enabled(self, name: str, enabled: bool) -> ServerConfig | None:
        """Persist an enabled toggle and apply it to the live config."""
        with self._lock:
            self._enabled_state[name] = bool(enabled)
            self._save_state()
        server = self.get(name)
        if server:
            server.enabled = bool(enabled)
        return server

    def add_server(
        self,
        name: str,
        transport: str = "stdio",
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict | None = None,
        env: dict | None = None,
        description: str = "",
        enabled: bool = True,
    ) -> ServerConfig:
        """Add a new MCP server to mcp-servers.json (hot-reloadable).

        The user drops in any JSON-RPC 2.0 stdio/HTTP server — a database
        tool, a web scraper, Chrome DevTools — and it becomes available to
        the agent immediately after ``reload()``.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("server name is required")
        transport = (transport or "stdio").lower()
        if transport not in ("stdio", "http"):
            raise ValueError("transport must be 'stdio' or 'http'")
        if transport == "stdio" and not command:
            raise ValueError("stdio servers need a 'command' (e.g. npx, python)")
        if transport == "http" and not url:
            raise ValueError("http servers need a 'url'")

        entry = {
            "name": name,
            "transport": transport,
            "command": command,
            "args": list(args or []),
            "url": url,
            "headers": dict(headers or {}),
            "env": dict(env or {}),
            "enabled": bool(enabled),
            "description": description or f"MCP server '{name}' added from the dashboard",
        }
        with self._lock:
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                data = {"servers": []}
            servers = data.setdefault("servers", [])
            # Replace an existing entry with the same name.
            servers[:] = [s for s in servers if s.get("name") != name]
            servers.append(entry)
            self.config_path.write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
            self._enabled_state[name] = bool(enabled)
            self._save_state()
        self.reload()
        return ServerConfig(
            name=name,
            transport=transport,
            command=command,
            args=list(args or []),
            url=url,
            headers=dict(headers or {}),
            env=dict(env or {}),
            enabled=bool(enabled),
            raw=entry,
        )
