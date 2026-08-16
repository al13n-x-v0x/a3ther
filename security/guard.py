"""
guard.py — validation + human-in-the-loop approval.

Two components:

- :class:`CommandGuard` — parses a command against the policy and returns
  a structured decision: allow / needs-approval / blocked.
- :class:`ApprovalGate` — the HITL gate. Dangerous commands pause; the
  user confirms either through a native GUI dialog (tkinter) or through
  the extensions API (``/api/security/approvals/{id}/decide``) which the
  dashboard can poll and answer. Denied or timed-out requests never run.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from dataclasses import dataclass, field

from .policy import Policy, RiskLevel, load_policy

LOGGER = logging.getLogger("a3ther.security")


@dataclass
class Decision:
    """Outcome of validating one command."""

    allowed: bool
    risk: RiskLevel
    reason: str = ""
    requires_approval: bool = False
    approval_id: str | None = None
    sanitized: str = ""

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "risk": self.risk.value,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "approval_id": self.approval_id,
            "sanitized": self.sanitized,
        }


class CommandGuard:
    """Classify commands and enforce the blocklist."""

    def __init__(self, policy: Policy | None = None):
        self.policy = policy or load_policy()

    def validate(self, command: str) -> Decision:
        risk = self.policy.classify(command)
        if risk == RiskLevel.BLOCKED:
            return Decision(
                allowed=False,
                risk=risk,
                reason="Blocked by security policy (irreversible or destructive pattern).",
            )
        if risk == RiskLevel.CAUTION:
            return Decision(
                allowed=False,
                risk=risk,
                requires_approval=True,
                reason="Classified as dangerous — human approval required.",
            )
        return Decision(allowed=True, risk=RiskLevel.SAFE)


class ApprovalGate:
    """Pollable, GUI-capable human-in-the-loop gate."""

    def __init__(self, policy: Policy | None = None, use_gui: bool = True):
        self.policy = policy or load_policy()
        self.use_gui = use_gui
        self._ids = itertools.count(1)
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def pending(self) -> list[dict]:
        """Dashboard-friendly list of awaiting approvals."""
        with self._lock:
            return [
                {"id": a["id"], "command": a["command"], "reason": a["reason"]}
                for a in self._pending.values()
                if a["approved"] is None  # only unresolved requests
            ]

    def decide(self, approval_id: str, approved: bool) -> bool:
        """Resolve a pending approval (from GUI or API)."""
        with self._lock:
            entry = self._pending.get(approval_id)
            if entry is None:
                return False
            entry["approved"] = bool(approved)
            entry["event"].set()
        LOGGER.info("Approval %s → %s", approval_id, "APPROVED" if approved else "DENIED")
        return True

    def request_approval(self, command: str, reason: str, timeout: int | None = None) -> bool:
        """Block until the user approves/denies (or the timeout expires)."""
        approval_id = f"ap-{next(self._ids)}"
        event = threading.Event()
        with self._lock:
            self._pending[approval_id] = {
                "id": approval_id,
                "command": command,
                "reason": reason,
                "event": event,
                "approved": None,  # None = unresolved; True/False = decided
            }

        # Fire a native GUI dialog on a background thread (best-effort).
        if self.use_gui:
            threading.Thread(
                target=self._show_gui_dialog,
                args=(approval_id, command, reason),
                daemon=True,
            ).start()

        timeout_s = timeout if timeout is not None else self.policy.approval_timeout_seconds
        event.wait(timeout=max(1, timeout_s))

        with self._lock:
            entry = self._pending.pop(approval_id, None)
            if entry is None:
                # Decided via API while we waited.
                return False  # conservative default on race
            return bool(entry.get("approved"))

    # ------------------------------------------------------------------ #
    def _show_gui_dialog(self, approval_id: str, command: str, reason: str) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            answer = messagebox.askyesno(
                "A3THER — Security Approval Required",
                f"{reason}\n\nCommand:\n{command}\n\nRun it?",
            )
            root.destroy()
            self.decide(approval_id, bool(answer))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("GUI approval unavailable (%s) — waiting on API/console", exc)


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_GATE: ApprovalGate | None = None
_GATE_LOCK = threading.Lock()


def get_approval_gate() -> ApprovalGate:
    global _GATE
    if _GATE is None:
        with _GATE_LOCK:
            if _GATE is None:
                _GATE = ApprovalGate()
    return _GATE
