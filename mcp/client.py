"""
client.py — a single MCP client connection.

Implements the client side of the Model Context Protocol handshake and the
core methods used by the host: ``initialize``, ``notifications/initialized``,
``tools/list``, ``tools/call`` and ``ping``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .transport.base import Transport, TransportError

# Protocol version we request; the server may echo an older one it supports.
PROTOCOL_VERSION = "2025-06-18"


@dataclass
class ToolInfo:
    """A tool exposed by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    server: str = ""


class MCPClient:
    """One connected MCP server."""

    def __init__(self, transport: Transport, server_name: str = "", client_name: str = "a3ther"):
        self.transport = transport
        self.server_name = server_name
        self.client_name = client_name
        self.server_info: dict = {}
        self.protocol_version: str = ""
        self._tools: list[ToolInfo] | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        """Run the MCP initialize handshake."""
        self.transport.connect()
        # 90s: first connect to an npx/uvx server downloads the package, which
        # regularly exceeds a 30s cap on a cold cache.
        result = self.transport.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "0.1.0"},
            },
            timeout=90,
        )
        self.server_info = result.get("serverInfo") or {}
        self.protocol_version = result.get("protocolVersion", "")
        self.transport.notify("notifications/initialized")
        self._tools = None

    def close(self) -> None:
        try:
            self.transport.close()
        finally:
            self._tools = None

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    def list_tools(self, force: bool = False) -> list[ToolInfo]:
        """Return the server's declared tools (cached unless ``force``)."""
        if self._tools is not None and not force:
            return self._tools

        result = self.transport.request("tools/list", {}, timeout=30)
        tools: list[ToolInfo] = []
        for item in result.get("tools", []) if isinstance(result, dict) else []:
            tools.append(
                ToolInfo(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    input_schema=item.get("inputSchema") or {},
                    server=self.server_name,
                )
            )
        self._tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict | None = None, timeout: float = 60.0) -> Any:
        """Invoke a tool on this server."""
        return self.transport.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )

    def ping(self, timeout: float = 10.0) -> bool:
        """Health check — True when the server answers a ping."""
        try:
            self.transport.request("ping", {}, timeout=timeout)
            return True
        except TransportError:
            return False
