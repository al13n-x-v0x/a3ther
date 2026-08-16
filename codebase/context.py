"""
codebase/context.py — the token-saving snippet assembler.

Given a query (or an error), :func:`assemble_context` finds the most
relevant files via the symbol index and returns ONLY the relevant line
windows — with line numbers and file boundaries — instead of dumping
500-line files into the LLM context. Cap on total characters keeps the
prompt budget bounded.
"""
from __future__ import annotations

from pathlib import Path

from .indexer import CodeIndexer, Symbol


def _window(source: str, start: int, end: int, pad: int = 4, max_chars: int = 1400) -> str:
    lines = source.splitlines()
    start = max(0, start - 1 - pad)
    end = min(len(lines), end + pad)
    out = []
    for i in range(start, end):
        line = lines[i]
        if len("".join(out)) > max_chars:
            out.append(f"  ... ({len(lines) - i} more lines)")
            break
        out.append(f"{i+1:4d} | {line}")
    return "\n".join(out)


def _file_context(rel_path: str, source: str, symbols: list[Symbol], query_terms: set[str]) -> str:
    """Pick the most relevant symbol windows in one file."""
    ranked: list[Symbol] = []
    for symbol in symbols:
        score = 0
        if query_terms:
            if any(term in symbol.name.lower() for term in query_terms):
                score += 2
        ranked.append((score, symbol))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if ranked and ranked[0][0] > 0:
        chosen = [symbol for _score, symbol in ranked[:2]]
    elif symbols:
        chosen = symbols[:1]  # fall back to the file's first symbol
    else:
        chosen = []

    blocks = [_window(source, s.start, s.end) for s in chosen]
    header = f"### {rel_path} ({len(source.splitlines())} lines)"
    return header + "\n" + "\n\n".join(blocks) if blocks else header + "\n(file has no indexed symbols)"


def assemble_context(query: str, root: str | Path | None = None, max_chars: int = 6000) -> str:
    """Return a compact, line-numbered snippet context for ``query``.

    ``root`` defaults to the current working directory; the index is
    rebuilt if it is empty.
    """
    indexer = CodeIndexer()
    if not indexer.files():
        indexer.index_directory(root or ".")
    elif root is not None:
        indexer.refresh(root)

    terms = {t for t in (query or "").lower().split() if len(t) > 2}
    hits = indexer.search_symbols(query, limit=10)
    if not hits:
        return "(no relevant symbols found in workspace index)"

    root_path = Path(root or ".").resolve()
    parts: list[str] = []
    used: set[str] = set()
    char_budget = max_chars

    for hit in hits:
        if hit.file in used:
            continue
        rel_path = hit.file
        path = root_path / rel_path
        if not path.is_file():
            continue
        record = indexer.file_record(rel_path) or {}
        symbols = [Symbol(s["kind"], s["name"], s["start"], s["end"], rel_path) for s in record.get("symbols", [])]
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        block = _file_context(rel_path, source, symbols, terms)
        if len(block) > char_budget:
            block = block[: char_budget - 3] + "..."
        parts.append(block)
        used.add(rel_path)
        char_budget -= len(block)
        if char_budget <= 400:
            break

    return "[RELEVANT CODE SNIPPETS (line numbers are absolute in each file)]\n\n" + "\n\n".join(parts)
