"""
freaky_fix.py — the Freaky-Fix autonomous repair loop.

Loop contract
-------------
1. Run the failing command (captured via :class:`autopilot.executor.ProcessRunner`).
2. If exit code != 0 (or output signals an error), extract the failing
   file boundaries from the traceback.
3. Feed the precise error stack + file context to the strongest available
   LLM client (via the gateway), get a full corrected file, save it
   atomically (with a backup), and re-run.
4. Repeat up to ``max_attempts`` (default 3). If it never passes, the
   report is returned with ``success=False`` so the caller can alert the
   user instead of looping forever.

All rewrites are confined to ``scope_root`` (the repository root by
default) — files outside it are never touched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from config import base_dir

from .executor import ProcessRunner
from .repair import (
    atomic_rewrite,
    build_repair_prompt,
    classify_error,
    find_failing_files,
    is_within_scope,
    parse_fenced_code,
)

LOGGER = logging.getLogger("a3ther.autopilot")


@dataclass
class FixReport:
    """Result of one Freaky-Fix session."""

    success: bool
    attempts: int
    command: str
    final_output: str
    patched_files: list[Path] = field(default_factory=list)
    error_type: str = "none"
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Freaky-Fix {'PASSED' if self.success else 'FAILED'} after {self.attempts} attempt(s).",
            f"Command: {self.command}",
        ]
        if self.patched_files:
            lines.append("Patched: " + ", ".join(str(p) for p in self.patched_files))
        if self.messages:
            lines.append("Log:")
            lines.extend(f"  - {m}" for m in self.messages)
        return "\n".join(lines)


class FreakyFixLoop:
    """Run → diagnose → LLM-patch → rewrite → re-run, up to N attempts."""

    def __init__(
        self,
        gateway=None,
        scope_root: Path | None = None,
        max_attempts: int = 3,
        run_timeout: int = 30,
        llm_timeout: int = 120,
        max_tokens: int = 4096,
        logger=None,
    ):
        self.gateway = gateway
        self.scope_root = (scope_root or base_dir()).resolve()
        self.max_attempts = max(1, int(max_attempts))
        self.run_timeout = int(run_timeout)
        self.llm_timeout = int(llm_timeout)
        self.max_tokens = int(max_tokens)
        self._logger = logger or LOGGER

    # ------------------------------------------------------------------ #
    def fix(
        self,
        command: str | list[str],
        cwd: str | Path | None = None,
        timeout: int | None = None,
        preference: str | None = None,
    ) -> FixReport:
        """Run ``command`` and self-heal any failure it produces."""
        if self.gateway is None:
            # Lazy import to keep package import dependency-free.
            from gateway.router import get_gateway

            self.gateway = get_gateway()

        workdir = Path(cwd).resolve() if cwd else self.scope_root
        runner = ProcessRunner(cwd=str(workdir), default_timeout=timeout or self.run_timeout)

        last_output = ""
        error_type = "none"
        patched: list[Path] = []
        messages: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            self._log(f"attempt {attempt}/{self.max_attempts}: {command if isinstance(command, str) else ' '.join(command)}")
            result = runner.run(command, timeout=timeout)
            last_output = result.combined

            if result.ok() and classify_error(last_output) == "none":
                messages.append(f"Command passed on attempt {attempt}.")
                return FixReport(
                    success=True,
                    attempts=attempt,
                    command=str(command),
                    final_output=last_output,
                    patched_files=patched,
                    error_type="none",
                    messages=messages,
                )

            error_type = classify_error(last_output)
            if attempt == self.max_attempts:
                messages.append("Max attempts reached — reporting failure to the user.")
                break

            targets = find_failing_files(last_output, self.scope_root)
            if not targets:
                messages.append(
                    "Could not map the error to a file inside the project scope; "
                    "skipping autonomous repair."
                )
                break

            attempt_patched = False
            for path in targets:
                path = Path(path)
                if not is_within_scope(path, self.scope_root):
                    messages.append(f"Skipping {path}: outside repair scope.")
                    continue
                try:
                    code = path.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    messages.append(f"Cannot read {path}: {exc}")
                    continue

                prompt = build_repair_prompt(
                    failing_path=path,
                    current_code=code,
                    error_output=last_output,
                    error_type=error_type,
                    scope_root=self.scope_root,
                )

                # Prefer an explicitly requested provider, else the gateway's
                # best available one (fall back gracefully if the injected
                # gateway does not expose best_provider()).
                best = getattr(self.gateway, "best_provider", None)
                pref = preference or (best() if callable(best) else None)
                try:
                    fixed = self.gateway.complete_text(
                        prompt,
                        preference=pref,
                        max_tokens=self.max_tokens,
                        timeout=self.llm_timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    messages.append(f"LLM repair failed for {path}: {exc}")
                    continue

                clean = parse_fenced_code(fixed)
                if not clean or clean == code:
                    messages.append(f"LLM produced no change for {path}.")
                    continue

                try:
                    atomic_rewrite(path, clean)
                    patched.append(path)
                    attempt_patched = True
                    messages.append(f"Patched {path} ({len(clean)} chars).")
                except Exception as exc:  # noqa: BLE001
                    messages.append(f"Rewrite failed for {path}: {exc}")

            if not attempt_patched:
                messages.append("No files could be patched this attempt.")

        return FixReport(
            success=False,
            attempts=self.max_attempts,
            command=str(command),
            final_output=last_output,
            patched_files=patched,
            error_type=error_type,
            messages=messages,
        )

    # ------------------------------------------------------------------ #
    def _log(self, message: str) -> None:
        self._logger.info("[FreakyFix] %s", message)
