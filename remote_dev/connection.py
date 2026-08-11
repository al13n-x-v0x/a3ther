"""
connection.py — paramiko-backed SSH connection manager.

:class:`SSHManager` pools connections per server name so repeated
commands reuse one channel. Host keys are verified against
``~/.ssh/known_hosts`` when present (strict mode); in non-strict mode a
warning is logged before accepting an unknown key.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import ServerProfile, get_profile

try:
    import paramiko
    _PARAMIKO_OK = True
except ImportError:  # pragma: no cover — degraded mode
    paramiko = None  # type: ignore[assignment]
    _PARAMIKO_OK = False


@dataclass
class RemoteResult:
    """Result of one remote command."""

    exit_code: int
    stdout: str
    stderr: str
    command: str

    @property
    def combined(self) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(f"STDOUT:\n{self.stdout.strip()}")
        if self.stderr.strip():
            parts.append(f"STDERR:\n{self.stderr.strip()}")
        return "\n\n".join(parts) if parts else "(no output)"

    def ok(self) -> bool:
        return self.exit_code == 0


class SSHConnectionError(RuntimeError):
    """Raised for connection/auth failures (message is user-safe)."""


class SSHConnection:
    """One open SSH channel to a server."""

    def __init__(self, profile: ServerProfile):
        self.profile = profile
        self._client = None
        self._sftp = None

    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        if not _PARAMIKO_OK:
            raise SSHConnectionError(
                "The 'paramiko' package is not installed. Run: pip install paramiko"
            )
        if self._client is not None and self._client.get_transport() is not None:
            return

        client = paramiko.SSHClient()
        known_hosts = Path(self.profile.known_hosts).expanduser() if self.profile.known_hosts else Path.home() / ".ssh" / "known_hosts"

        if self.profile.strict_host_checking:
            # Strict mode must REJECT unknown keys. If there is no
            # known_hosts file we refuse to connect rather than silently
            # auto-accepting (which would defeat the whole point).
            if not known_hosts.exists():
                raise SSHConnectionError(
                    f"Strict host checking is enabled but no known_hosts file was found "
                    f"at {known_hosts}. Add the server's host key there, or set "
                    f"strict_host_checking: false in config/servers.json to allow "
                    f"first-connect acceptance."
                )
            client.load_host_keys(str(known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            # Non-strict: warn on unknown keys when we have a known_hosts
            # file, otherwise auto-add (first connect).
            client.set_missing_host_key_policy(
                paramiko.WarningPolicy() if known_hosts.exists() else paramiko.AutoAddPolicy()
            )

        kwargs: dict = {
            "hostname": self.profile.host,
            "port": self.profile.port,
            "username": self.profile.user or None,
            "timeout": self.profile.timeout,
            "allow_agent": True,
            "look_for_keys": True,
        }
        if self.profile.password:
            kwargs["password"] = self.profile.password
        if self.profile.key_path:
            kwargs["key_filename"] = str(Path(self.profile.key_path).expanduser())

        try:
            client.connect(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise SSHConnectionError(
                f"Cannot connect to {self.profile.user}@{self.profile.host}:"
                f"{self.profile.port} — {exc}"
            ) from exc

        self._client = client

    def is_alive(self) -> bool:
        """True when the underlying SSH transport is still open."""
        try:
            transport = self._client.get_transport() if self._client else None
            return bool(transport and transport.is_active())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    def exec(self, command: str, timeout: int = 30) -> RemoteResult:
        self.connect()
        assert self._client is not None
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return RemoteResult(exit_code=code, stdout=out, stderr=err, command=command)

    # ------------------------------------------------------------------ #
    def sftp(self):
        self.connect()
        assert self._client is not None
        if self._sftp is None:
            self._sftp = self._client.open_sftp()
        return self._sftp

    def read_file(self, remote_path: str) -> str:
        with self.sftp().open(remote_path, "r") as handle:
            return handle.read().decode("utf-8", errors="replace")

    def write_file(self, remote_path: str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        with self.sftp().open(remote_path, "wb") as handle:
            handle.write(data)

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        try:
            if self._sftp is not None:
                self._sftp.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._client is not None:
                self._client.close()
        except Exception:  # noqa: BLE001
            pass
        self._sftp = None
        self._client = None


class SSHManager:
    """Pool of open connections, keyed by server name/host."""

    def __init__(self):
        self._connections: dict[str, SSHConnection] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def get(self, name_or_host: str, force: bool = False) -> SSHConnection:
        """Return a (re)usable connection for a server profile."""
        profile = get_profile(name_or_host)
        if profile is None:
            raise SSHConnectionError(
                f"No SSH profile matches {name_or_host!r}. Check config/servers.json "
                "or set A3THER_SSH_HOST."
            )
        key = profile.name
        with self._lock:
            conn = self._connections.get(key)
            if conn is not None and not force and conn.is_alive():
                return conn
            new_conn = SSHConnection(profile)
            new_conn.connect()
            self._connections[key] = new_conn
            return new_conn

    def test(self, name_or_host: str) -> tuple[bool, str]:
        """Best-effort connectivity test (connect + echo)."""
        try:
            conn = self.get(name_or_host, force=True)
            result = conn.exec("echo a3ther-ok", timeout=10)
            ok = result.ok() and "a3ther-ok" in result.stdout
            return ok, "Connected." if ok else f"Unexpected response: {result.combined[:200]}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def close_all(self) -> None:
        with self._lock:
            for conn in self._connections.values():
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            self._connections.clear()

    def active(self) -> list[str]:
        return list(self._connections.keys())
