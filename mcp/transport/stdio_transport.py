"""
stdio_transport.py — JSON-RPC 2.0 over a subprocess's stdin/stdout.

Matches the official MCP stdio framing: newline-delimited JSON messages.
A background reader thread routes responses to waiting callers by id and
collects unsolicited server notifications.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from typing import Any

from .base import Transport, TransportError


def _resolve_command(command: list[str]) -> list[str]:
    """Resolve ``command[0]`` to a real executable path via PATH.

    On Windows, ``npx``/``uv`` are ``.cmd`` shims and ``subprocess.Popen``
    (shell=False) will not resolve them by bare name — every npm/uv-based
    MCP server would otherwise fail with FileNotFoundError. ``shutil.which``
    honours PATHEXT, so this makes ``command: "npx"`` work everywhere while
    keeping the config file portable.
    """
    if not command:
        return command
    exe = command[0]
    resolved = shutil.which(exe)
    if resolved:
        return [resolved] + list(command[1:])
    if sys.platform == "win32" and not exe.lower().endswith((".exe", ".cmd", ".bat")):
        for ext in (".cmd", ".bat", ".exe"):
            candidate = shutil.which(exe + ext)
            if candidate:
                return [candidate] + list(command[1:])
    return command


class StdioTransport(Transport):
    """Spawn a process and speak newline-delimited JSON-RPC with it."""

    name = "stdio"

    def __init__(
        self,
        command: list[str],
        env: dict | None = None,
        cwd: str | None = None,
        encoding: str = "utf-8",
    ):
        super().__init__()
        self.command = list(command)
        self.env = env or {}
        self.cwd = cwd
        self.encoding = encoding

        self._proc: subprocess.Popen | None = None
        self._write_lock = threading.Lock()
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, dict] = {}
        self._notifications: list[dict] = []
        self._notify_lock = threading.Lock()
        self._closed = False
        self._reader_dead = threading.Event()

    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        if self._proc is not None:
            return

        # Windows .cmd shims (npx.cmd, uv.cmd, …) need PATH resolution.
        self.command = _resolve_command(self.command)

        merged_env = dict(os.environ)
        merged_env.update(self.env or {})

        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "encoding": self.encoding,
            "errors": "replace",
            "bufsize": 1,
            "env": merged_env,
        }
        if self.cwd:
            kwargs["cwd"] = self.cwd
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._proc = subprocess.Popen(self.command, **kwargs)
        except FileNotFoundError as exc:
            raise TransportError(
                f"Could not launch MCP server: {self.command[0]!r} not found"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"Failed to launch MCP server: {exc}") from exc

        threading.Thread(target=self._read_loop, name="mcp-stdio-reader", daemon=True).start()

    # ------------------------------------------------------------------ #
    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue

                msg_id = msg.get("id")
                if isinstance(msg_id, int):
                    event = self._pending.pop(msg_id, None)
                    if event is not None:
                        self._results[msg_id] = msg
                        event.set()
                    continue

                # Server-initiated message / notification.
                if msg.get("method"):
                    with self._notify_lock:
                        self._notifications.append(msg)
        except Exception:  # noqa: BLE001 — reader must never kill the host
            pass
        finally:
            self._reader_dead.set()

    # ------------------------------------------------------------------ #
    def request(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        self.connect()
        if self._proc is None or self._proc.poll() is not None:
            raise TransportError("MCP stdio process is not running.")
        if self._reader_dead.is_set():
            raise TransportError("MCP server output stream closed unexpectedly.")

        msg_id = self.next_id()
        payload: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params

        event = threading.Event()
        self._pending[msg_id] = event

        try:
            with self._write_lock:
                assert self._proc.stdin is not None
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            self._pending.pop(msg_id, None)
            raise TransportError(f"Write to MCP server failed: {exc}") from exc

        if not event.wait(timeout):
            self._pending.pop(msg_id, None)
            raise TransportError(f"Timed out after {timeout}s waiting for {method!r}")
        if self._closed:
            # close() woke us to fail fast — never report a false success.
            raise TransportError(f"MCP transport closed while waiting for {method!r}")

        response = self._results.pop(msg_id, {})
        if "error" in response:
            error = response["error"]
            if isinstance(error, dict):
                message = error.get("message") or str(error)
            else:
                message = str(error)
            raise TransportError(f"MCP server error for {method!r}: {message}")
        return response.get("result")

    # ------------------------------------------------------------------ #
    def notify(self, method: str, params: dict | None = None) -> None:
        self.connect()
        if self._proc is None or self._proc.poll() is not None:
            return
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            with self._write_lock:
                assert self._proc.stdin is not None
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
        except Exception:  # noqa: BLE001 — notifications are best-effort
            pass

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        # Wake any waiters so they fail fast instead of hanging.
        for event in list(self._pending.values()):
            event.set()
        self._pending.clear()
