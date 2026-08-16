"""
codebase/tools.py — native workspace tools exposed to LLM function calling.

Tools (with OpenAI/Ollama-style JSON schemas)::

    index_directory(root)      → rebuild the symbol index
    read_code_file(path, start_line, end_line) → read a file (or a window)
    search_symbols(query)      → find symbols/files by name
    replace_code_block(path, old_block, new_block) → safe in-place replace
    create_new_file(path, content) → write a new file (with parents)

The schemas are in :data:`TOOL_SCHEMAS` — drop them straight into the
gateway's ``tools`` argument.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from autopilot.repair import atomic_rewrite, is_within_scope
from config import base_dir

from .indexer import CodeIndexer

LOGGER = logging.getLogger("a3ther.codebase")

DEFAULT_SCOPE = base_dir()


@dataclass
class Workspace:
    """A sandboxed workspace root + its indexer."""

    root: Path
    indexer: CodeIndexer


def _scope() -> Path:
    return DEFAULT_SCOPE.resolve()


def _safe_path(path: str) -> Path:
    """Resolve a path inside the workspace scope (rejects escapes)."""
    target = Path(path)
    if not target.is_absolute():
        target = _scope() / target
    target = target.resolve()
    if not is_within_scope(target, _scope()):
        raise PermissionError(f"Path outside workspace scope: {path}")
    return target


# ------------------------------------------------------------------------- #
# Implementations
# ------------------------------------------------------------------------- #
def tool_index_directory(root: str = ".") -> str:
    indexer = CodeIndexer()
    scope_root = Path(root).resolve() if root not in (".", "") else _scope()
    updated = indexer.index_directory(scope_root)
    stats = indexer.stats()
    return (
        f"Indexed {scope_root}: {updated} files updated. "
        f"Total {stats['files']} files, {stats['symbols']} symbols."
    )


def tool_read_code_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    file_path = _safe_path(path)
    if not file_path.is_file():
        return f"File not found: {path}"
    source = file_path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    if start_line is not None or end_line is not None:
        start = max(1, int(start_line or 1))
        end = min(len(lines), int(end_line or len(lines)))
        body = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines[start - 1:end], start=start - 1))
        return f"{file_path} [lines {start}-{end}]\n{body}"
    return f"{file_path} ({len(lines)} lines)\n" + "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))


def tool_search_symbols(query: str, limit: int = 20) -> str:
    indexer = CodeIndexer()
    hits = indexer.search_symbols(query, limit=limit)
    if not hits:
        return f"No symbols matched {query!r}."
    lines = [f"{h.file}:{h.start} {h.kind} {h.name}"]
    return "\n".join(lines[:limit])


def tool_replace_code_block(path: str, old_block: str, new_block: str) -> str:
    file_path = _safe_path(path)
    if not file_path.is_file():
        return f"File not found: {path}"
    source = file_path.read_text(encoding="utf-8", errors="replace")
    if old_block not in source:
        return f"old_block not found in {path} — provide the exact text to replace."
    if source.count(old_block) > 1:
        return f"old_block appears {source.count(old_block)} times in {path} — make it unique."
    updated = source.replace(old_block, new_block)
    atomic_rewrite(file_path, updated)
    return f"Replaced block in {path} ({len(old_block)} → {len(new_block)} chars)."


def tool_create_new_file(path: str, content: str) -> str:
    file_path = _safe_path(path)
    if file_path.exists():
        return f"Refusing to overwrite existing file: {path}"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return f"Created {path} ({len(content)} chars)."


# ------------------------------------------------------------------------- #
# Tool schemas (LLM function-calling)
# ------------------------------------------------------------------------- #
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "index_directory",
            "description": "Scan a workspace directory and build a cached symbol index (classes/functions with line numbers).",
            "parameters": {
                "type": "object",
                "properties": {"root": {"type": "string", "description": "Directory to index (default: workspace root)"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_code_file",
            "description": "Read a source file, optionally a specific line window, with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_symbols",
            "description": "Find symbols (classes/functions/files) by name across the indexed workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_code_block",
            "description": "Replace an exact block of text inside a file with new content (safe, backed up).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_block": {"type": "string"},
                    "new_block": {"type": "string"},
                },
                "required": ["path", "old_block", "new_block"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_new_file",
            "description": "Create a new file with content (refuses to overwrite).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
]

_TOOL_IMPLS = {
    "index_directory": lambda args: tool_index_directory(args.get("root", ".")),
    "read_code_file": lambda args: tool_read_code_file(
        args.get("path", ""), args.get("start_line"), args.get("end_line")
    ),
    "search_symbols": lambda args: tool_search_symbols(args.get("query", ""), int(args.get("limit", 20))),
    "replace_code_block": lambda args: tool_replace_code_block(
        args.get("path", ""), args.get("old_block", ""), args.get("new_block", "")
    ),
    "create_new_file": lambda args: tool_create_new_file(args.get("path", ""), args.get("content", "")),
}


def execute_tool(name: str, args: dict) -> str:
    """Dispatch a tool call by name (for the LLM tool loop / API)."""
    impl = _TOOL_IMPLS.get(name)
    if impl is None:
        return f"Unknown tool: {name}"
    try:
        return impl(args or {})
    except PermissionError as exc:
        return f"Security error: {exc}"
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Tool %s failed", name)
        return f"Tool {name} failed: {exc}"
