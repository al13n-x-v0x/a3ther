"""
A3THER Model Context Protocol (MCP) host.

Turns A3THER into an MCP *client/host*: it reads ``config/mcp-servers.json``,
connects to external MCP servers over JSON-RPC 2.0 (stdio or HTTP), lists
their tools, and lets the brain call them as first-class capabilities.

Highlights
----------
- :mod:`mcp.transport` — dependency-free JSON-RPC 2.0 transports
  (``stdio`` via subprocess pipes, ``http`` via urllib + SSE parsing).
- :mod:`mcp.registry` — dynamic ``mcp-servers.json`` loading with
  hot-reload and persistent per-server enabled state.
- :mod:`mcp.host` — process-wide singleton managing all clients and
  exposing flattened ``server__tool`` names for the LLM tool pipeline.
"""
from .host import MCPHost, get_mcp_host

__all__ = ["MCPHost", "get_mcp_host"]
