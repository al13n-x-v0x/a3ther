"""
memory/memory_manager.py — JSON-backed long-term memory.

Storage layout (``memory.json`` in the A3THER data folder)::

    {
      "facts":        {key: {"value": ..., "importance": n, "created": ...}},
      "notes":        {key: {"value": ...}},
      "preferences":  {key: value},
      "session_summaries": ["…", "…"],
      "long_term":    {key: {"value": ..., "importance": n, "created": ...}}
    }

Everything degrades gracefully: a missing/corrupt file reads as an empty
store, and writes are best-effort (memory must never crash the brain).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_lock = threading.RLock()
_MAX_SUMMARIES = 40
_MAX_ENTRIES = 500


def _store_path() -> Path:
    try:
        from config.paths import get_data_dir

        base = get_data_dir()
    except Exception:  # noqa: BLE001
        base = Path.home() / ".a3ther"
    base.mkdir(parents=True, exist_ok=True)
    return base / "memory.json"


def _empty_memory() -> dict:
    return {
        "facts": {},
        "notes": {},
        "preferences": {},
        "session_summaries": [],
        "long_term": {},
    }


def _load_file() -> dict:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            store = _empty_memory()
            for key in store:
                if isinstance(raw.get(key), type(store[key])):
                    store[key] = raw[key]
            return store
    except Exception:  # noqa: BLE001
        pass
    return _empty_memory()


def _save_file(store: dict) -> None:
    try:
        _store_path().write_text(json.dumps(store, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _recursive_update(base: dict, updates: dict) -> dict:
    out = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _recursive_update(out[key], value)
        else:
            out[key] = value
    return out


def _trim_to_limit(entries: dict) -> dict:
    """Drop oldest entries beyond _MAX_ENTRIES (keeps the file small)."""
    if len(entries) <= _MAX_ENTRIES:
        return entries
    ordered = sorted(entries.items(), key=lambda kv: str(kv[1].get("created", "")))
    return dict(ordered[-_MAX_ENTRIES:])


def _truncate_value(value: Any, limit: int = 2000) -> Any:
    """Keep memory JSON files small — cap long text values."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def _all_entries() -> dict:
    """Flatten facts + long_term into one {key: {"value", "importance"}} map."""
    store = _load_file()
    merged: dict = {}
    for section in ("facts", "long_term"):
        for key, entry in (store.get(section) or {}).items():
            if isinstance(entry, dict):
                merged[key] = entry
    return merged


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def load_memory() -> dict:
    """Full memory store (dict copy)."""
    return _load_file()


def update_memory(data: dict) -> dict:
    """Recursively merge ``data`` into the store and persist. Returns store."""
    with _lock:
        store = _load_file()
        store = _recursive_update(store, data)
        for section in ("facts", "long_term", "notes"):
            store[section] = _trim_to_limit(store[section])
        _save_file(store)
        return store


def remember(key: str, value: Any, importance: int = 3) -> bool:
    """Store a durable fact under ``key`` (importance 1-5)."""
    with _lock:
        store = _load_file()
        store.setdefault("facts", {})[key] = {
            "value": _truncate_value(value),
            "importance": max(1, min(5, int(importance))),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store["facts"] = _trim_to_limit(store["facts"])
        _save_file(store)
        return True


def forget(key: str) -> bool:
    """Remove a fact (and any long-term entry with the same key)."""
    with _lock:
        store = _load_file()
        removed = False
        for section in ("facts", "long_term"):
            if key in store.get(section, {}):
                del store[section][key]
                removed = True
        if removed:
            _save_file(store)
        return removed


def save_session_summary(summary: str) -> bool:
    """Append a conversation summary to the rolling history (capped)."""
    with _lock:
        store = _load_file()
        summaries = store.setdefault("session_summaries", [])
        summaries.append(_truncate_value(str(summary), limit=3000))
        store["session_summaries"] = summaries[-_MAX_SUMMARIES:]
        _save_file(store)
        return True


def pop_last_session() -> Optional[str]:
    """Return and remove the most recent session summary (or None)."""
    with _lock:
        store = _load_file()
        summaries = store.get("session_summaries") or []
        if not summaries:
            return None
        last = summaries.pop()
        _save_file(store)
        return last


def save_memory(key: str, value: Any, section: str = "long_term") -> bool:
    """Generic entry point (aliased as ``memory_save`` by the brain).

    Writes into ``long_term`` by default, or ``notes`` / ``preferences``.
    """
    with _lock:
        store = _load_file()
        if section not in ("long_term", "notes", "preferences", "facts"):
            section = "long_term"
        if section == "preferences":
            store[section][key] = _truncate_value(value)
        else:
            store[section][key] = {
                "value": _truncate_value(value),
                "importance": 3,
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        store[section] = _trim_to_limit(store[section])
        _save_file(store)
        return True


def format_memory_for_prompt(memory_data: dict | None = None) -> str:
    """Render the store as a compact prompt block for the LLM.

    Empty stores return "" (the caller treats it as no memory). Output looks
    like::

        [MEMORY]
        - <fact>: value
        [SESSION HISTORY]
        - <summary>
    """
    data = memory_data if isinstance(memory_data, dict) else load_memory()
    lines: List[str] = []

    entries = _all_entries() if memory_data is None else {
        **(data.get("facts") or {}),
        **(data.get("long_term") or {}),
    }
    # Merge on top of each other is fine — prefer non-empty values.
    if entries:
        lines.append("[MEMORY]")
        for key, entry in sorted(entries.items()):
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value not in (None, ""):
                lines.append(f"- {key}: {value}")

    notes = data.get("notes") or {}
    note_values = [
        n.get("value") if isinstance(n, dict) else n
        for n in notes.values()
        if n
    ]
    if note_values:
        lines.append("[NOTES]")
        for value in note_values[-10:]:
            if value not in (None, ""):
                lines.append(f"- {value}")

    prefs = data.get("preferences") or {}
    if prefs:
        lines.append("[PREFERENCES]")
        for key, value in prefs.items():
            if value not in (None, ""):
                lines.append(f"- {key}: {value}")

    summaries = data.get("session_summaries") or []
    if summaries:
        lines.append("[SESSION HISTORY]")
        for summary in summaries[-6:]:
            lines.append(f"- {summary}")

    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    update_memory({"notes": {"test": {"value": "hello"}}})
    remember("user_name", "Shellified", importance=5)
    save_session_summary("User asked about the memory module.")
    print(format_memory_for_prompt())
    print("popped:", pop_last_session())
