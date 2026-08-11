"""
repair.py — traceback analysis and safe file rewriting.

Turn raw failure output into an actionable repair: locate the failing
file boundaries inside the process scope, classify the error, build the
prompt for the repair LLM, and apply rewritten files atomically with
backups.
"""
from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

# Matches "File \"path/to/x.py\", line 12" and optionally ", in func".
_TRACEBACK_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([\w\.<>]+))?')

_BACKUP_DIR_NAME = ".a3ther_backups"


@dataclass
class TraceFrame:
    """One stack frame extracted from a traceback."""

    path: str
    line: int | None
    function: str = ""


def parse_traceback(output: str) -> list[TraceFrame]:
    """Extract all ``File "...", line N`` frames from output."""
    frames: list[TraceFrame] = []
    for match in _TRACEBACK_RE.finditer(output or ""):
        frames.append(
            TraceFrame(
                path=match.group(1),
                line=int(match.group(2)),
                function=match.group(3) or "",
            )
        )
    return frames


def classify_error(output: str) -> str:
    """Classify failure output: dependency/import/syntax/runtime or none."""
    low = (output or "").lower()

    if any(k in low for k in ("no module named", "modulenotfounderror", "importerror", "cannot import")):
        return "dependency_error"

    if "syntaxerror" in low or "invalid syntax" in low:
        return "syntax_error"

    if any(
        k in low
        for k in (
            "traceback",
            "exception",
            "error:",
            "nameerror",
            "typeerror",
            "attributeerror",
            "valueerror",
            "keyerror",
            "indexerror",
            "zerodivisionerror",
            "filenotfounderror",
            "permissionerror",
            "assertionerror",
        )
    ):
        return "runtime_error"

    return "none"


def is_within_scope(path: Path, scope_root: Path) -> bool:
    """True when ``path`` resolves inside ``scope_root`` (symlink-safe)."""
    try:
        return path.resolve().is_relative_to(scope_root.resolve())
    except Exception:
        return False


def find_failing_files(output: str, scope_root: Path) -> list[Path]:
    """Map traceback frames to real files under ``scope_root``.

    Frames are returned in traceback order (outermost first), de-duplicated,
    and only paths that exist inside the scope are kept. The *most specific*
    (deepest) frame is typically the last entry.
    """
    root = scope_root.resolve()
    found: list[Path] = []
    seen: set[str] = set()

    for frame in parse_traceback(output or ""):
        raw = Path(frame.path)
        # Try absolute match, then basename resolution against the scope.
        candidates = [raw] if raw.is_absolute() else []
        candidates.append(root / raw)
        candidates.append(root / raw.name)

        for candidate in candidates:
            resolved = candidate.resolve()
            if not is_within_scope(resolved, root):
                continue
            if not resolved.exists():
                continue
            key = str(resolved)
            if key in seen:
                break
            seen.add(key)
            found.append(resolved)
            break

    return found


def error_summary(output: str, max_chars: int = 3000) -> str:
    """A compact, LLM-friendly slice of the failure output."""
    text = (output or "").strip()
    if not text:
        return "(no output)"
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def build_repair_prompt(
    failing_path: Path,
    current_code: str,
    error_output: str,
    error_type: str,
    scope_root: Path,
    line_hint: int | None = None,
    max_code_chars: int = 6000,
) -> str:
    """Assemble the Freaky-Fix repair prompt for the LLM.

    Includes the precise error stack, the failing file's context and
    boundaries, and strict output rules (full fixed file only).
    """
    code = current_code or "(file is empty)"
    if len(code) > max_code_chars:
        code = code[:max_code_chars] + "\n# ... [truncated]"

    rel = failing_path.resolve().relative_to(scope_root.resolve())

    line_hint_text = (
        f"\nThe traceback points at line {line_hint} in this file."
        if line_hint
        else ""
    )

    return f"""You are an expert debugger inside the A3THER autonomous repair loop.
A command failed and I need you to produce a complete fixed version of ONE file.

Failing file (relative to project root): {rel}
Error classification: {error_type}{line_hint_text}

=== FULL ERROR OUTPUT (from the failed run) ===
{error_summary(error_output)}

=== CURRENT (BROKEN) CONTENTS OF {rel} ===
```{code}```

Rules:
- Output ONLY the complete fixed file content. No explanation, no markdown
  fences around the answer, no commentary before or after.
- Fix every error visible in the error output; keep all working logic intact.
- Preserve the file's existing public API (function/class names and
  signatures) unless the error requires changing them.
- Do not invent imports that are not from the standard library or already
  used elsewhere in this project.
- The file will be saved verbatim and the failing command re-run.

Fixed content for {rel}:"""


def parse_fenced_code(text: str) -> str:
    """Strip a single markdown code fence if the model wrapped its answer."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop the opening fence line (```lang or ```).
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Drop a trailing fence line.
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def atomic_rewrite(path: Path, content: str) -> Path:
    """Write ``content`` to ``path`` atomically, keeping a dated backup.

    The backup lives in ``<project>/.a3ther_backups/`` so every autonomous
    rewrite can be inspected or rolled back.
    """
    path = Path(path)
    # Backups live in a hidden sibling folder of the rewritten file, e.g.
    # <repo>/src/.a3ther_backups/ for <repo>/src/foo.py.
    backup_root = path.parent / _BACKUP_DIR_NAME
    backup_root.mkdir(parents=True, exist_ok=True)

    if path.exists():
        # Microsecond precision avoids overwriting a backup taken in the
        # same second during rapid successive repairs. (%f is not valid in
        # time.strftime on Windows, so microseconds come from time.time().)
        micros = f"{int(time.time() * 1_000_000) % 1_000_000:06d}"
        stamp = f"{time.strftime('%Y%m%d_%H%M%S')}_{micros}"
        backup = backup_root / f"{path.stem}_{stamp}{path.suffix}"
        shutil.copy2(path, backup)

    tmp = path.with_suffix(path.suffix + ".a3ther.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return path
