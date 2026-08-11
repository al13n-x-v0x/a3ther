"""
MCP transport layer — JSON-RPC 2.0 over stdio and HTTP.

Both transports implement :class:`mcp.transport.base.Transport` and stay
stdlib-only so the MCP host works without any third-party dependency.
"""
from .base import Transport, TransportError
from .stdio_transport import StdioTransport
from .http_transport import HttpTransport

__all__ = ["Transport", "TransportError", "StdioTransport", "HttpTransport"]
