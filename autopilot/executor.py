"""
ProcessRunner — deterministic subprocess execution with full capture.

Captures stdout, stderr, exit code and timeout state for arbitrary
commands and for single files in a known language. Used by the Freaky-Fix
loop and the extensions API (``/api/autopilot/run``).
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunResult:
    """Everything the executor knows about one command invocation."""

    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None

    @property
    def combined(self) -> str:
        """Both streams, labelled, suitable for feeding back to an LLM."""
        parts: list[str] = []
        if self.stdout.strip():
            parts.append(f"STDOUT:\n{self.stdout.strip()}")
        if self.stderr.strip():
            parts.append(f"STDERR:\n{self.stderr.strip()}")
        if self.error:
            parts.append(f"EXEC ERROR:\n{self.error}")
        return "\n\n".join(parts) if parts else "(no output)"

    def ok(self) -> bool:
        """True when the process exited 0 and did not time out."""
        return not self.timed_out and self.exit_code == 0


_INTERPRETERS: dict[str, list[str]] = {
    ".py": [sys.executable],
    ".js": ["node"],
    ".ts": ["npx", "ts-node"],
    ".mjs": ["node"],
    ".sh": ["bash"],
    ".ps1": ["powershell", "-File"],
    ".rb": ["ruby"],
    ".php": ["php"],
}


class ProcessRunner:
    """Run commands or files, always returning a RunResult (never raising)."""

    def __init__(self, cwd: str | Path | None = None, default_timeout: int = 30):
        self.cwd = str(cwd) if cwd else None
        self.default_timeout = default_timeout

    # ------------------------------------------------------------------ #
    def run(
        self,
        command: str | list[str],
        cwd: str | Path | None = None,
        timeout: int | None = None,
        env: dict | None = None,
        shell: bool = False,
    ) -> RunResult:
        """Run a command and capture the result.

        ``command`` may be a string (``shell=True``) or an argv list.
        """
        timeout = timeout or self.default_timeout
        workdir = str(cwd) if cwd else self.cwd

        if isinstance(command, str):
            argv: list[str] = command.split()
            use_shell = shell
        else:
            argv = list(command)
            use_shell = False

        label = " ".join(argv) if argv else str(command)

        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if workdir:
            popen_kwargs["cwd"] = workdir
        if env:
            merged = dict(__import__("os").environ)
            merged.update(env)
            popen_kwargs["env"] = merged
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        proc: subprocess.Popen | None = None
        try:
            # Popen (not subprocess.run) so the failsafe can track the child
            # PID and kill the whole tree on timeout or abort.
            if use_shell:
                proc = subprocess.Popen(str(command), shell=True, **popen_kwargs)
            else:
                proc = subprocess.Popen(argv, **popen_kwargs)

            try:
                from sync.failsafe import track_pid, untrack_pid
            except Exception:  # noqa: BLE001 — sync layer optional
                track_pid = untrack_pid = lambda _pid: None

            track_pid(proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            finally:
                untrack_pid(proc.pid)

            return RunResult(
                command=label,
                exit_code=proc.returncode,
                stdout=stdout or "",
                stderr=stderr or "",
            )
        except subprocess.TimeoutExpired:
            # Kill the whole tree — a timed-out child left running is a leak.
            if proc is not None and proc.poll() is None:
                try:
                    if sys.platform == "win32":
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True, timeout=5,
                        )
                    else:
                        import os

                        try:
                            os.killpg(proc.pid, 9)
                        except ProcessLookupError:
                            proc.kill()
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
            # Drain whatever the child already wrote before the kill — the
            # Freaky-Fix loop feeds partial output back to the LLM.
            partial_out = partial_err = ""
            if proc is not None:
                try:
                    partial_out, partial_err = proc.communicate(timeout=2)
                except Exception:  # noqa: BLE001 — pipes may already be gone
                    pass
            return RunResult(
                command=label,
                exit_code=None,
                stdout=partial_out or "",
                stderr=partial_err or "",
                timed_out=True,
                error=f"Timed out after {timeout}s — process tree killed.",
            )
        except FileNotFoundError:
            return RunResult(
                command=label,
                exit_code=None,
                stdout="",
                stderr="",
                error=f"Command not found: {argv[0] if argv else command}",
            )
        except OSError as exc:
            return RunResult(
                command=label,
                exit_code=None,
                stdout="",
                stderr="",
                error=f"OS error: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return RunResult(
                command=label,
                exit_code=None,
                stdout="",
                stderr="",
                error=f"Execution error: {exc}",
            )

    # ------------------------------------------------------------------ #
    def run_file(
        self,
        path: str | Path,
        args: list[str] | None = None,
        timeout: int | None = None,
    ) -> RunResult:
        """Run a single source file with the right interpreter."""
        file_path = Path(path)
        interpreter = _INTERPRETERS.get(file_path.suffix.lower())
        if not interpreter:
            return RunResult(
                command=str(file_path),
                exit_code=None,
                stdout="",
                stderr="",
                error=f"No interpreter registered for {file_path.suffix}.",
            )
        argv = interpreter + [str(file_path)] + list(args or [])
        return self.run(argv, cwd=str(file_path.parent), timeout=timeout)
