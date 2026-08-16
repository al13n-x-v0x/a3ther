"""
Transport ABC for JSON-RPC 2.0 message exchange.

The MCP protocol is JSON-RPC 2.0. A transport must support:

- ``request(method, params, timeout)`` — send a request, block for the
  matching response, return the ``result`` object (raise on ``error``).
- ``notify(method, params)`` — fire-and-forget notification.
- ``connect()`` / ``close()`` — lifecycle.
"""
from __future__ import annotations

import abc
import itertools
from typing import Any


class TransportError(RuntimeError):
    """Raised on protocol, I/O or timeout failures."""


class Transport(abc.ABC):
    """Abstract JSON-RPC 2.0 transport."""

    name = "base"

    def __init__(self) -> None:
        self._ids = itertools.count(1)

    def next_id(self) -> int:
        return next(self._ids)

    @abc.abstractmethod
    def connect(self) -> None:
        """Establish the underlying channel."""

    @abc.abstractmethod
    def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        """Perform a JSON-RPC request and return the result."""

    @abc.abstractmethod
    def notify(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""

    @abc.abstractmethod
    def close(self) -> None:
        """Tear down the channel."""

    def __enter__(self) -> "Transport":
        self.connect()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
