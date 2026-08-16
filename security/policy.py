"""
policy.py — the structural safety layer.

Commands are classified into three risk levels by regex pattern matching
against a policy file (``config/security_policy.json``):

- SAFE     — allowed to run without approval.
- CAUTION  — potentially destructive; requires human-in-the-loop approval
             before execution.
- BLOCKED  — never executed, ever (irreversible system destruction).

Patterns are separated by platform (``all`` + ``windows``/``linux``/``mac``)
so OS-native foot-guns are caught on the right systems.
"""
from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from config import base_dir

POLICY_PATH = base_dir() / "config" / "security_policy.json"


class RiskLevel(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    BLOCKED = "blocked"


@dataclass
class Policy:
    """Parsed security policy with compiled regexes."""

    blocked: list[re.Pattern] = field(default_factory=list)
    dangerous: list[re.Pattern] = field(default_factory=list)
    approval_timeout_seconds: int = 120
    auto_approve_safe: bool = True
    raw: dict = field(default_factory=dict)

    def classify(self, command: str) -> RiskLevel:
        text = (command or "").strip().lower()
        if not text:
            return RiskLevel.SAFE
        if any(p.search(text) for p in self.blocked):
            return RiskLevel.BLOCKED
        if any(p.search(text) for p in self.dangerous):
            return RiskLevel.CAUTION
        return RiskLevel.SAFE


_DEFAULT_POLICY = {
    "approval_timeout_seconds": 120,
    "auto_approve_safe": True,
    "blocked_patterns": {
        "all": [
            r"\brm\s+-rf\s+/",
            r"\brm\s+-rf\s+[a-z]:[/\\]",
            r"\bmkfs\b",
            r"\bdd\s+if=/dev/zero",
            r"\bdiskpart\b",
            r"\bformat\s+[a-z]:",
            r"\bnet\s+user\b",
            r"\bnetsh\s+advfirewall",
            r"reg\s+(add|delete|delete)\s+HKLM",
            r"shutdown\s+/\s*s",
            r"shutdown\s+-h",
            r"del\s+/s\s+/q\s+[a-z]:[/\\]",
            r"rd\s+/s\s+/q\s+[a-z]:[/\\]",
            r"remove-item\s+-recurse\s+-force\s+[a-z]:[/\\]",
            r"powershell\s+-enc",
            r"curl\s+[^\s|]+\s*\|\s*(ba)?sh",
            r"wget\s+[^\s|]+\s*\|\s*(ba)?sh",
            r":\(\)\{\s*:\|\s*:\s*&\s*\}\s*;",
            r"taskkill\s+/f\s+/im\s+system",
            r"chmod\s+-r\s+777\s+/",
            r"sudo\s+rm\s+-rf\s+/",
        ],
        "windows": [
            r"del\s+/s\s+/q",
            r"rd\s+/s\s+/q",
            r"rmdir\s+/s\s+/q",
            r"format\s+[a-z]:",
        ],
        "linux": [
            r"chown\s+-r\s+[^:]+:/\s*/",
            r"\bmkfs\.",
            r"echo\s+[^\n]*\s*>\s*/dev/sd",
        ],
        "mac": [
            r"diskutil\s+erase",
            r"sudo\s+rm\s+-rf\s+/",
        ],
    },
    "dangerous_patterns": {
        "all": [
            r"\brm\s+-rf\b",
            r"\brm\s+-r\b",
            r"\bdel\s+/[sq]",
            r"\bgit\s+push\s+.*--force",
            r"\bgit\s+reset\s+--hard",
            r"\bpip\s+uninstall\b",
            r"\bnpm\s+uninstall\s+-g\b",
            r"\bdrop\s+table\b",
            r"\bdelete\s+from\b",
            r"\bshutdown\b",
            r"\breboot\b",
            r"\brestart\b",
            r"\btaskkill\b",
            r"\bkill\s+-9\b",
            r"\bpkill\b",
            r"\bsystemctl\s+stop\b",
            r"\bservice\s+stop\b",
            r"\breg\s+(add|delete)\b",
            r"\bchmod\b",
            r"\bchown\b",
            r"\bdd\s+of=",
            r"\bmkfs",
            r"\bformat\s",
            r"python\s+-m\s+pip\s+uninstall",
            r"git\s+clean\s+-f",
            r"docker\s+(rm\s+-f|system\s+prune|volume\s+rm)",
        ],
    },
}


def _ensure_policy_file() -> None:
    if not POLICY_PATH.exists():
        POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
        POLICY_PATH.write_text(
            json.dumps(_DEFAULT_POLICY, indent=2), encoding="utf-8"
        )


def load_policy(path: str | Path | None = None) -> Policy:
    """Load (and seed) the security policy from config/security_policy.json."""
    _ensure_policy_file()
    path = Path(path or POLICY_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = _DEFAULT_POLICY

    os_key = {
        "Windows": "windows",
        "Darwin": "mac",
    }.get(platform.system(), "linux")

    def _compile(group: str) -> list[re.Pattern]:
        patterns = list(data.get(group, {}).get("all", []))
        patterns += list(data.get(group, {}).get(os_key, []))
        return [re.compile(p, re.IGNORECASE) for p in patterns]

    return Policy(
        blocked=_compile("blocked_patterns"),
        dangerous=_compile("dangerous_patterns"),
        approval_timeout_seconds=int(data.get("approval_timeout_seconds", 120) or 120),
        auto_approve_safe=bool(data.get("auto_approve_safe", True)),
        raw=data,
    )


def classify(command: str, policy: Policy | None = None) -> RiskLevel:
    """Convenience classifier."""
    return (policy or load_policy()).classify(command)
