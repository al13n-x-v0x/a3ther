"""
A3THER Security Sandbox.

Structural safety for anything the agent runs on the local OS:

- :mod:`security.policy`   — blocklist / risk classification
- :mod:`security.guard`    — command validation + human-in-the-loop gate
- :mod:`security.sandbox`  — the sandboxed executor (the only way scripts
  should be run)

Every "dangerous" command pauses and waits for explicit user approval
(GUI dialog or dashboard); irreversibly destructive patterns are blocked
outright and never executed.
"""
from .guard import ApprovalGate, CommandGuard, Decision, get_approval_gate
from .policy import Policy, RiskLevel, load_policy
from .sandbox import SandboxedExecutor

__all__ = [
    "ApprovalGate",
    "CommandGuard",
    "Decision",
    "get_approval_gate",
    "Policy",
    "RiskLevel",
    "load_policy",
    "SandboxedExecutor",
]
