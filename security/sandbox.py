"""
sandbox.py — the sandboxed execution wrapper.

Every terminal command that A3THER runs goes through
:class:`SandboxedExecutor`, which:

1. validates it against the security policy,
2. pauses for human approval when the command is dangerous,
3. executes with a hard timeout, returning a structured result.

Safe commands run immediately; blocked commands never run; dangerous
commands wait for the user (GUI dialog or dashboard approval).
"""
from __future__ import annotations

import logging
from typing import Any

from autopilot.executor import ProcessRunner, RunResult

from .guard import ApprovalGate, CommandGuard, Decision
from .policy import RiskLevel, load_policy

LOGGER = logging.getLogger("a3ther.security")


class SandboxedExecutor:
    """Policy-checked command runner."""

    def __init__(self, guard: CommandGuard | None = None, gate: ApprovalGate | None = None):
        self.guard = guard or CommandGuard()
        self.gate = gate or ApprovalGate()
        self.runner = ProcessRunner()

    # ------------------------------------------------------------------ #
    def run(
        self,
        command: str | list[str],
        cwd: str | None = None,
        timeout: int = 30,
        interactive: bool = True,
    ) -> dict[str, Any]:
        """Run a command through the sandbox; returns a structured result."""
        label = command if isinstance(command, str) else " ".join(command)

        decision: Decision = self.guard.validate(label)
        if not decision.allowed and not decision.requires_approval:
            return {
                "ok": False,
                "blocked": True,
                "command": label,
                "reason": decision.reason,
                "risk": decision.risk.value,
            }

        if decision.requires_approval:
            approved = (
                self.gate.request_approval(label, decision.reason)
                if interactive
                else False
            )
            if not approved:
                return {
                    "ok": False,
                    "blocked": True,
                    "command": label,
                    "reason": "Denied by human approval gate.",
                    "risk": RiskLevel.CAUTION.value,
                }

        result: RunResult = self.runner.run(command, cwd=cwd, timeout=timeout)
        return {
            "ok": result.ok(),
            "blocked": False,
            "command": label,
            "risk": RiskLevel.SAFE.value,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }
