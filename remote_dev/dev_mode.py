"""
dev_mode.py — the "act as a dev on <server>" command listener.

The manager owns a session state: once a session is open, subsequent
natural-language commands (``run ...``, ``deploy <local> to <remote>``,
``read the logs from <path>``, ``disconnect``) route to the active server.

Trigger phrases
---------------
- "act as a dev on <server>"
- "dev mode on <server>"
- "connect to server <server>"
- "work on the server <server>"

Wire this into the brain's intent router and the frontend voice engine so
both text and voice commands reach :meth:`DevModeManager.handle`.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import get_profile
from .connection import SSHConnectionError, SSHManager
from .remote import deploy_file, exec_command, read_log, run_script

_START_PATTERNS = [
    re.compile(r"act\s+as\s+a\s+dev\s+on\s+([\w\.\-:]+)", re.IGNORECASE),
    re.compile(r"dev\s+mode\s+on\s+([\w\.\-:]+)", re.IGNORECASE),
    re.compile(r"connect\s+to\s+server\s+([\w\.\-:]+)", re.IGNORECASE),
    re.compile(r"work\s+on\s+(?:the\s+)?server\s+([\w\.\-:]+)", re.IGNORECASE),
]


@dataclass
class DevCommand:
    """A parsed dev-mode command."""

    kind: str          # start | exec | deploy | log | script | stop | status
    server: str | None
    payload: str = ""
    remote_path: str | None = None
    raw: str = ""


class DevModeManager:
    """Routes developer-mode text/voice commands to SSH sessions."""

    def __init__(self, ssh: SSHManager | None = None):
        self.ssh = ssh or SSHManager()
        self.active: str | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #
    def parse(self, text: str) -> DevCommand | None:
        """Parse a natural-language command into a DevCommand (or None)."""
        if not text or not text.strip():
            return None
        raw = text.strip()
        low = raw.lower()

        # Session start patterns first.
        for pattern in _START_PATTERNS:
            match = pattern.search(low)
            if match:
                return DevCommand(kind="start", server=match.group(1), raw=raw)

        # Everything below requires an active session.
        if not self.active:
            return None

        if any(k in low for k in ("disconnect", "end session", "stop session", "log off server")):
            return DevCommand(kind="stop", server=self.active, raw=raw)

        if "status" in low and ("session" in low or "server" in low):
            return DevCommand(kind="status", server=self.active, raw=raw)

        # "deploy <local> to <remote>"
        deploy_match = re.search(r"deploy\s+([^\s]+)\s+to\s+([^\s]+)", low)
        if deploy_match:
            return DevCommand(
                kind="deploy",
                server=self.active,
                payload=deploy_match.group(1),
                remote_path=deploy_match.group(2),
                raw=raw,
            )

        # "read the logs from <path>" / "tail <path>"
        log_match = re.search(r"(?:read|tail|show)\s+(?:the\s+)?(?:logs?\s+from\s+|log\s+)?([\/\w\.\-]+)", low)
        if log_match and any(k in low for k in ("log", "tail")):
            return DevCommand(kind="log", server=self.active, payload=log_match.group(1), raw=raw)

        # "run script <local-path>" — upload and execute a local file
        script_match = re.search(r"run\s+script\s+([^\s]+)", low)
        if script_match:
            return DevCommand(kind="script", server=self.active, payload=script_match.group(1), raw=raw)

        # "run <command> on the server" / "run <command>"
        run_match = re.search(r"run\s+(.+?)(?:\s+on\s+the\s+server)?$", low)
        if run_match:
            return DevCommand(kind="exec", server=self.active, payload=run_match.group(1).strip(), raw=raw)

        return None

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    def handle(self, text: str) -> str:
        """Entry point for brain/voice: parse a command and execute it."""
        command = self.parse(text)
        if command is None:
            if self.active:
                return (
                    f"Developer mode is active on {self.active}. "
                    "Say 'run <command>', 'deploy <local> to <remote>', "
                    "'read the logs from <path>', or 'disconnect'."
                )
            return (
                "No developer session is active. Say: "
                "'a3ther, act as a dev on <server>' to start one."
            )

        try:
            if command.kind == "start":
                return self.start_session(command.server)
            if command.kind == "stop":
                return self.end_session()
            if command.kind == "status":
                return self.session_status()
            if command.kind == "exec":
                return self._exec_on_active(command.payload)
            if command.kind == "deploy":
                return self._deploy_on_active(command.payload, command.remote_path)
            if command.kind == "log":
                return self._log_on_active(command.payload)
            if command.kind == "script":
                return self._script_on_active(command.payload)
        except SSHConnectionError as exc:
            return f"Remote dev error: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"Remote dev error: {exc}"
        return "Command not understood."

    # ------------------------------------------------------------------ #
    # Session management
    # ------------------------------------------------------------------ #
    def start_session(self, server: str) -> str:
        if get_profile(server) is None:
            return (
                f"No SSH profile matches {server!r}. Add it to config/servers.json "
                "or set A3THER_SSH_HOST."
            )
        with self._lock:
            # Force a fresh connection so credentials are validated now.
            try:
                conn = self.ssh.get(server, force=True)
                probe = conn.exec("echo a3ther-dev-session-ok && uname -a", timeout=15)
            except Exception as exc:  # noqa: BLE001
                return f"Could not open the session: {exc}"
            self.active = server
        header = probe.stdout.strip() or probe.combined
        return (
            f"Developer mode active on {server}. "
            f"Connection verified: {header[:160]}"
        )

    def end_session(self) -> str:
        with self._lock:
            name = self.active
            self.active = None
        if name:
            try:
                self.ssh.close_all()
            except Exception:  # noqa: BLE001
                pass
            return f"Disconnected from {name}. Developer mode ended."
        return "No active developer session."

    def session_status(self) -> str:
        if not self.active:
            return "No active developer session."
        return f"Developer session active on {self.active}. Open channels: {self.ssh.active()}"

    # ------------------------------------------------------------------ #
    # Session-scoped actions
    # ------------------------------------------------------------------ #
    def _require_active(self) -> str:
        if not self.active:
            raise SSHConnectionError("No active developer session. Start one with 'act as a dev on <server>'.")
        return self.active

    def _exec_on_active(self, command: str) -> str:
        server = self._require_active()
        return exec_command(self.ssh, server, command, timeout=60)

    def _deploy_on_active(self, local: str, remote: str | None) -> str:
        server = self._require_active()
        if not remote:
            return "Deploy needs a remote path: 'deploy <local> to <remote>'."
        return deploy_file(self.ssh, server, local, remote)

    def _log_on_active(self, path: str) -> str:
        server = self._require_active()
        return read_log(self.ssh, server, path, tail_lines=100)

    def _script_on_active(self, local: str) -> str:
        """Upload a local script to the active server and run it."""
        server = self._require_active()
        path = Path(local).expanduser()
        if not path.exists():
            return f"Local script not found: {path}"
        try:
            script = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return f"Could not read {path}: {exc}"
        remote_path = f"/tmp/a3ther_{path.name}"
        return run_script(self.ssh, server, script, remote_path=remote_path)


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_MANAGER: DevModeManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_dev_mode_manager() -> DevModeManager:
    """Return the process-wide dev-mode manager singleton."""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = DevModeManager()
    return _MANAGER
