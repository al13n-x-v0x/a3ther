"""
host.py — the process-wide MCP host.

Owns the registry and one :class:`mcp.client.MCPClient` per configured
server, exposes flattened ``server__tool`` names (so two servers may both
export a tool called ``query`` without colliding), and can convert the
discovered tools into Ollama-style tool declarations for the LLM gateway.
"""
from __future__ import annotations

import threading
from typing import Any

from .client import MCPClient, ToolInfo
from .registry import MCPRegistry, ServerConfig
from .transport.base import TransportError
from .transport.http_transport import HttpTransport
from .transport.stdio_transport import StdioTransport

TOOL_SEPARATOR = "__"


class MCPHost:
    """Manages connections to all configured MCP servers."""

    def __init__(self, registry: MCPRegistry | None = None):
        self.registry = registry or MCPRegistry()
        self.clients: dict[str, MCPClient] = {}
        self.errors: dict[str, str] = {}
        self._lock = threading.RLock()
        self._started = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def ensure_started(self) -> None:
        """Connect every enabled server (idempotent, best-effort)."""
        with self._lock:
            if self._started:
                return
            self._started = True

        servers = self.registry.discover()
        for server in servers:
            if server.enabled:
                self._connect_server_locked(server)

    def start(self) -> None:
        self.ensure_started()

    def stop(self) -> None:
        with self._lock:
            for name, client in list(self.clients.items()):
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
            self.clients.clear()
            self._started = False

    # ------------------------------------------------------------------ #
    # Per-server control
    # ------------------------------------------------------------------ #
    def _connect_server_locked(self, server: ServerConfig) -> MCPClient:
        try:
            transport = self._build_transport(server)
            client = MCPClient(transport, server_name=server.name)
            client.connect()
            client.list_tools()  # prime the tool cache
            self.clients[server.name] = client
            self.errors.pop(server.name, None)
            return client
        except Exception as exc:  # noqa: BLE001
            self.errors[server.name] = str(exc)
            self.clients.pop(server.name, None)
            raise

    def connect_server(self, name: str) -> dict:
        """Connect one server; returns status info."""
        server = self.registry.get(name)
        if server is None:
            return {"ok": False, "error": f"Unknown MCP server: {name}"}
        with self._lock:
            try:
                client = self._connect_server_locked(server)
                tools = client.list_tools()
                return {"ok": True, "tools": len(tools)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

    def disconnect_server(self, name: str) -> dict:
        with self._lock:
            client = self.clients.pop(name, None)
        if client:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        self.errors.pop(name, None)
        return {"ok": True}

    @staticmethod
    def _build_transport(server: ServerConfig):
        if server.transport == "http":
            if not server.url:
                raise TransportError(f"MCP server {server.name!r}: missing 'url'")
            return HttpTransport(server.url, headers=server.headers or {})
        # stdio
        if not server.command:
            raise TransportError(f"MCP server {server.name!r}: missing 'command'")
        command = [server.command] + list(server.args or [])
        return StdioTransport(command=command, env=server.env or {})

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    def list_tools(self) -> list[ToolInfo]:
        """Flatten every connected server's tools."""
        tools: list[ToolInfo] = []
        for name, client in list(self.clients.items()):
            try:
                tools.extend(client.list_tools())
            except Exception:  # noqa: BLE001
                self.errors[name] = "tools/list failed"
        return tools

    def call_tool(self, server: str, tool: str, arguments: dict | None = None) -> Any:
        """Call ``tool`` on ``server`` and return the raw MCP result."""
        client = self.clients.get(server)
        if client is None:
            raise TransportError(
                f"MCP server {server!r} is not connected. "
                f"Enabled: {[s.name for s in self.registry.discover() if s.enabled]}"
            )
        return client.call_tool(tool, arguments or {})

    def call_llm_tool(self, tool_name: str, arguments: dict | None = None) -> Any:
        """Call a tool addressed by its flattened ``server__tool`` name."""
        if TOOL_SEPARATOR in tool_name:
            server, _, tool = tool_name.partition(TOOL_SEPARATOR)
            return self.call_tool(server, tool, arguments)
        raise TransportError(
            f"Tool {tool_name!r} is not prefixed with a server name "
            f"(expected 'server{TOOL_SEPARATOR}tool')."
        )

    def to_llm_tools(self) -> list[dict]:
        """Convert MCP tools to Ollama-style tool declarations for the gateway."""
        tools: list[dict] = []
        for info in self.list_tools():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": f"{info.server}{TOOL_SEPARATOR}{info.name}",
                        "description": info.description
                        or f"MCP tool {info.name} on {info.server}",
                        "parameters": info.input_schema
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return tools

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def get_status(self) -> list[dict]:
        """Dashboard-friendly status for every configured server."""
        out: list[dict] = []
        for server in self.registry.discover():
            client = self.clients.get(server.name)
            tool_count = 0
            if client:
                try:
                    tool_count = len(client.list_tools())
                except Exception:  # noqa: BLE001
                    pass
            out.append(
                {
                    "name": server.name,
                    "transport": server.transport,
                    "enabled": server.enabled,
                    "connected": client is not None,
                    "tools": tool_count,
                    "error": self.errors.get(server.name),
                    "protocol_version": client.protocol_version if client else "",
                }
            )
        return out


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_HOST: MCPHost | None = None
_HOST_LOCK = threading.Lock()


def get_mcp_host() -> MCPHost:
    """Return the process-wide MCP host singleton."""
    global _HOST
    if _HOST is None:
        with _HOST_LOCK:
            if _HOST is None:
                _HOST = MCPHost()
    return _HOST


def reset_mcp_host() -> None:
    global _HOST
    with _HOST_LOCK:
        if _HOST is not None:
            _HOST.stop()
        _HOST = None
