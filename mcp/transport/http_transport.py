"""
http_transport.py — JSON-RPC 2.0 over HTTP (streamable-HTTP style).

Sends POST requests with ``Accept: application/json, text/event-stream``.
If the server answers with an SSE stream (the modern MCP streamable-HTTP
mode), the ``data:`` payloads are parsed; plain JSON responses are used
as-is. Dependency-free via ``urllib.request``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import Transport, TransportError


def _parse_sse(body: str) -> list[dict]:
    """Extract JSON payloads from a text/event-stream body."""
    payloads: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        try:
            payloads.append(json.loads(data))
        except json.JSONDecodeError:
            continue
    return payloads


class HttpTransport(Transport):
    """Stateless JSON-RPC over HTTP(S)."""

    name = "http"

    def __init__(
        self,
        url: str,
        headers: dict | None = None,
        timeout: float = 30.0,
    ):
        super().__init__()
        self.base_url = url.rstrip("/")
        self.timeout = timeout
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }

    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        # Stateless transport — nothing to do. A ping() is used for health.
        return

    def _post(self, payload: dict, timeout: float | None = None) -> dict:
        request = urllib.request.Request(
            self.base_url + "/",
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                content_type = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise TransportError(f"HTTP {exc.code} from MCP server: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"Connection to MCP server failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TransportError("MCP HTTP request timed out.") from exc

        if "text/event-stream" in content_type:
            payloads = _parse_sse(body)
            if payloads:
                return payloads[-1]
            raise TransportError("Empty SSE response from MCP server.")
        try:
            return json.loads(body) if body.strip() else {}
        except json.JSONDecodeError as exc:
            raise TransportError(f"Invalid JSON from MCP server: {body[:200]!r}") from exc

    # ------------------------------------------------------------------ #
    def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        msg_id = self.next_id()
        payload: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params

        response = self._post(payload, timeout=timeout)
        if response.get("id") is not None and response.get("id") != msg_id:
            raise TransportError("MCP server replied with a mismatched request id.")
        if "error" in response:
            raise TransportError(f"MCP server error for {method!r}: {response['error']}")
        return response.get("result")

    # ------------------------------------------------------------------ #
    def notify(self, method: str, params: dict | None = None) -> None:
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._post(payload, timeout=5.0)
        except Exception:  # noqa: BLE001 — notifications are best-effort
            pass

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        return
