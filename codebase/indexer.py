"""
codebase/indexer.py — fast local file + symbol indexer.

Builds an architecture map of a workspace without reading every line:
per file we store path, language, size, mtime, hash and the *symbols*
(classes/functions with line ranges) parsed via Python's ``ast`` (for
.py) or a lightweight regex scan (for .js/.ts). The index is cached as
JSON so re-scans are cheap.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from config import base_dir

LOGGER = logging.getLogger("a3ther.codebase")

INDEX_PATH = base_dir() / "memory" / "code_index.json"

# Directories never indexed.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".next", ".a3ther_backups", ".freebuff",
}
_SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp4", ".mp3",
    ".wav", ".zip", ".gz", ".exe", ".dll", ".so", ".dylib", ".bin",
    ".pdf", ".woff", ".woff2", ".ttf", ".db", ".sqlite", ".lock",
}

_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".html": "html",
    ".css": "css", ".json": "json", ".md": "markdown", ".sh": "shell",
    ".sql": "sql", ".c": "c", ".cpp": "cpp", ".h": "c", ".rs": "rust",
    ".go": "go", ".java": "java", ".rb": "ruby", ".php": "php",
}


@dataclass
class Symbol:
    """One indexed symbol with its source line range."""

    kind: str      # class | function | method | const
    name: str
    start: int
    end: int
    file: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "start": self.start, "end": self.end, "file": self.file}


def _python_symbols(source: str) -> list[Symbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    symbols: list[Symbol] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            symbols.append(Symbol("class", node.name, node.lineno, getattr(node, "end_lineno", node.lineno)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "method" if any(isinstance(p, ast.ClassDef) for p in ast.walk(tree) if p is not node and getattr(p, "body", None) and node in getattr(p, "body", [])) else "function"
            symbols.append(Symbol(kind, node.name, node.lineno, getattr(node, "end_lineno", node.lineno)))
    return symbols


_JS_SYMBOL_RE = re.compile(
    r"(?m)^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|const\s+(\w+)\s*=|let\s+(\w+)\s*=|var\s+(\w+)\s*=)"
)


def _js_symbols(source: str, language: str) -> list[Symbol]:
    symbols: list[Symbol] = []
    for match in _JS_SYMBOL_RE.finditer(source):
        name = next((g for g in match.groups() if g), match.group(0))
        lineno = source.count("\n", 0, match.start()) + 1
        kind = "class" if "class" in match.group(0) else "function" if "function" in match.group(0) else "const"
        symbols.append(Symbol(kind, name, lineno, lineno + 12))
    return symbols


class CodeIndexer:
    """Builds and queries a cached workspace index."""

    def __init__(self, index_path: str | Path | None = None):
        self.index_path = Path(index_path or INDEX_PATH)
        self._lock = threading.RLock()
        self._files: dict[str, dict] = {}  # rel_path -> file record
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            self._files = data.get("files", {})
        except Exception:  # noqa: BLE001
            self._files = {}

    def _save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps({"files": self._files, "updated": time.time()}, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    def index_directory(self, root: str | Path) -> int:
        """Walk ``root``, parse symbols, and update the cached index."""
        root = Path(root).resolve()
        if not root.is_dir():
            return 0
        updated = 0
        with self._lock:
            for path in root.rglob("*"):
                if path.is_dir():
                    continue
                rel = str(path.relative_to(root)).replace("\\", "/")
                if any(part in _SKIP_DIRS for part in path.parts):
                    continue
                if path.suffix.lower() in _SKIP_EXTS:
                    continue
                lang = _LANG_BY_EXT.get(path.suffix.lower(), "")
                if not lang:
                    continue
                try:
                    stat = path.stat()
                    digest = hashlib.md5(path.read_bytes()).hexdigest()
                except OSError:
                    continue
                cached = self._files.get(rel)
                if cached and cached.get("hash") == digest:
                    continue  # unchanged — cheap re-scan
                source = path.read_text(encoding="utf-8", errors="replace")
                if lang == "python":
                    symbols = _python_symbols(source)
                else:
                    symbols = _js_symbols(source, lang)
                self._files[rel] = {
                    "path": rel,
                    "language": lang,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "hash": digest,
                    "symbols": [s.to_dict() for s in symbols],
                }
                updated += 1
            self._save()
        return updated

    def refresh(self, root: str | Path) -> int:
        """Re-index; also drops files that no longer exist."""
        root = Path(root).resolve()
        self.index_directory(root)
        rels = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()}
        with self._lock:
            stale = [rel for rel in self._files if rel not in rels]
            for rel in stale:
                del self._files[rel]
            if stale:
                self._save()
        return len(stale)

    # ------------------------------------------------------------------ #
    def search_symbols(self, query: str, limit: int = 20) -> list[Symbol]:
        """Case-insensitive symbol / file-name search."""
        q = (query or "").lower()
        hits: list[Symbol] = []
        with self._lock:
            for rel, record in self._files.items():
                if q and q in rel.lower():
                    for s in record.get("symbols", [])[:8]:
                        hits.append(Symbol(s["kind"], s["name"], s["start"], s["end"], rel))
                if q:
                    for s in record.get("symbols", []):
                        if q in s["name"].lower():
                            hits.append(Symbol(s["kind"], s["name"], s["start"], s["end"], rel))
        # Rank: symbol-name hits first, then path hits.
        hits.sort(key=lambda s: (0 if q and q in s.name.lower() else 1, s.file))
        return hits[:limit]

    def file_record(self, rel_path: str) -> dict | None:
        return self._files.get(rel_path.replace("\\", "/"))

    def files(self) -> list[dict]:
        return list(self._files.values())

    def stats(self) -> dict:
        with self._lock:
            return {
                "files": len(self._files),
                "symbols": sum(len(f.get("symbols", [])) for f in self._files.values()),
            }
