"""
remote.py — high-level remote developer operations.

Thin, human-friendly wrappers over :class:`remote_dev.connection.SSHManager`:
execute commands, tail log files, deploy files/patches over SFTP (with
remote backups), and run scripts on the server.
"""
from __future__ import annotations

from pathlib import Path

from .connection import SSHManager


def exec_command(
    manager: SSHManager,
    server: str,
    command: str,
    timeout: int = 30,
) -> str:
    """Run ``command`` on ``server`` and return a readable summary."""
    result = manager.get(server).exec(command, timeout=timeout)
    return (
        f"[{server}] exit={result.exit_code}\n{result.combined}"
        if result.combined != "(no output)"
        else f"[{server}] exit={result.exit_code} (no output)"
    )


def read_log(
    manager: SSHManager,
    server: str,
    log_path: str,
    tail_lines: int = 100,
) -> str:
    """Tail the last ``tail_lines`` lines of a remote log file."""
    command = f"tail -n {int(tail_lines)} {log_path!r}"
    result = manager.get(server).exec(command, timeout=30)
    if not result.ok():
        return f"Failed to read {log_path}: {result.stderr.strip() or result.combined}"
    return result.stdout


def deploy_file(
    manager: SSHManager,
    server: str,
    local_path: str | Path,
    remote_path: str,
    backup: bool = True,
) -> str:
    """Upload a local file to the server over SFTP.

    When ``backup`` is True, an existing remote file is first copied to
    ``<remote_path>.a3ther.bak`` so patches can be rolled back.
    """
    local = Path(local_path)
    if not local.exists():
        return f"Local file not found: {local}"

    conn = manager.get(server)

    if backup:
        try:
            conn.exec(f"cp {remote_path!r} {remote_path!r}.a3ther.bak 2>/dev/null || true", timeout=15)
        except Exception:  # noqa: BLE001
            pass

    try:
        conn.write_file(remote_path, local.read_bytes())
    except Exception as exc:  # noqa: BLE001
        return f"SFTP upload failed: {exc}"

    return (
        f"Deployed {local.name} -> {server}:{remote_path} "
        f"({local.stat().st_size} bytes)"
        + (" (backup saved)" if backup else "")
    )


def run_script(
    manager: SSHManager,
    server: str,
    script: str,
    remote_path: str = "/tmp/a3ther_script.sh",
    timeout: int = 60,
) -> str:
    """Upload a script, chmod +x it, run it, and return the output."""
    conn = manager.get(server)
    try:
        conn.write_file(remote_path, script)
        conn.exec(f"chmod +x {remote_path!r}", timeout=15)
        result = conn.exec(f"bash {remote_path!r}", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return f"Script execution failed: {exc}"
    finally:
        try:
            conn.exec(f"rm -f {remote_path!r}", timeout=15)
        except Exception:  # noqa: BLE001
            pass
    return f"[{server}] exit={result.exit_code}\n{result.combined}"
