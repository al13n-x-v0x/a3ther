"""
codebase/agent.py — the autonomous workspace engineering loop.

Sequential pipeline::

    [write/update code] → [run test command] → [read errors]
    → [self-heal: snippet context + LLM patch] → [rewrite] → [re-run]

Runs up to ``max_attempts`` (default 3) before giving up and asking the
human. Reuses the autopilot process/repair machinery and the snippet
context assembler so every LLM call sees only relevant line windows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from autopilot.executor import ProcessRunner, RunResult
from autopilot.repair import (
    atomic_rewrite,
    build_repair_prompt,
    classify_error,
    find_failing_files,
    is_within_scope,
    parse_fenced_code,
)
from config import base_dir

from .context import assemble_context
from .indexer import CodeIndexer

LOGGER = logging.getLogger("a3ther.codebase")


@dataclass
class LoopReport:
    """Result of one workspace self-correction run."""

    success: bool
    attempts: int
    command: str
    patched_files: list[Path] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    final_output: str = ""

    def summary(self) -> str:
        return (
            f"Workspace loop {'PASSED' if self.success else 'FAILED'} after "
            f"{self.attempts} attempt(s).\nPatched: "
            + (", ".join(str(p) for p in self.patched_files) or "none")
        )


class CodeWorkspaceAgent:
    """Self-correcting code loop over a workspace directory."""

    def __init__(
        self,
        gateway=None,
        scope_root: Path | None = None,
        max_attempts: int = 3,
        run_timeout: int = 60,
        indexer: CodeIndexer | None = None,
    ):
        self.gateway = gateway
        self.scope_root = (scope_root or base_dir()).resolve()
        self.max_attempts = max(1, int(max_attempts))
        self.run_timeout = int(run_timeout)
        self.indexer = indexer or CodeIndexer()

    # ------------------------------------------------------------------ #
    def run(self, test_command: str | list[str], cwd: str | Path | None = None) -> LoopReport:
        """Execute the test loop over the workspace."""
        if self.gateway is None:
            from gateway.router import get_gateway

            self.gateway = get_gateway()

        workdir = Path(cwd).resolve() if cwd else self.scope_root
        runner = ProcessRunner(cwd=str(workdir), default_timeout=self.run_timeout)
        patched: list[Path] = []
        messages: list[str] = []
        last_output = ""

        for attempt in range(1, self.max_attempts + 1):
            LOGGER.info("Workspace attempt %d/%d", attempt, self.max_attempts)
            result: RunResult = runner.run(test_command)
            last_output = result.combined

            if result.ok() and classify_error(last_output) == "none":
                messages.append(f"Passed on attempt {attempt}.")
                return LoopReport(True, attempt, str(test_command), patched, messages, last_output)

            if attempt == self.max_attempts:
                messages.append("Max attempts reached — asking the human for guidance.")
                break

            error_type = classify_error(last_output)
            targets = find_failing_files(last_output, workdir) or find_failing_files(
                last_output, self.scope_root
            )
            if not targets:
                messages.append("Could not map the failure to a workspace file; skipping auto-fix.")
                break

            fixed_any = False
            for path in targets:
                path = Path(path)
                if not is_within_scope(path, self.scope_root):
                    messages.append(f"Skipping {path}: outside workspace scope.")
                    continue
                try:
                    code = path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:  # noqa: BLE001
                    messages.append(f"Cannot read {path}: {exc}")
                    continue

                # Token-saving context: only relevant snippets.
                snippet_ctx = assemble_context(path.stem, root=str(workdir), max_chars=4000)
                prompt = build_repair_prompt(path, code, last_output, error_type, self.scope_root)
                prompt = prompt.replace(
                    "=== FULL ERROR OUTPUT (from the failed run) ===",
                    "=== ERROR OUTPUT ===",
                )
                prompt += "\n\nRelevant workspace snippets:\n" + snippet_ctx[:2500]

                try:
                    best = getattr(self.gateway, "best_provider", None)
                    pref = best() if callable(best) else None
                    fixed = self.gateway.complete_text(
                        prompt, preference=pref, max_tokens=4096
                    )
                except Exception as exc:  # noqa: BLE001
                    messages.append(f"LLM patch failed for {path}: {exc}")
                    continue

                clean = parse_fenced_code(fixed)
                if not clean or clean == code:
                    messages.append(f"No change produced for {path}.")
                    continue
                try:
                    atomic_rewrite(path, clean)
                    patched.append(path)
                    fixed_any = True
                    messages.append(f"Patched {path}.")
                except Exception as exc:  # noqa: BLE001
                    messages.append(f"Rewrite failed for {path}: {exc}")

            if not fixed_any:
                messages.append("Nothing could be patched this attempt.")

        return LoopReport(False, self.max_attempts, str(test_command), patched, messages, last_output)
