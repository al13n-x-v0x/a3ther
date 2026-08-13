"""
memory — A3THER long-term memory.

Simple, dependency-free JSON-backed memory store. Conversation summaries,
facts, notes and preferences persist across sessions in the A3THER data
folder. Exposes the exact API surface the brain and the features API expect:

* :func:`memory.memory_manager.load_memory` / ``update_memory`` / ``remember``
  / ``forget`` — the read/write primitives.
* :func:`memory.memory_manager.format_memory_for_prompt` — turns the store
  into a compact prompt block.
* :func:`memory.memory_manager.save_session_summary` / ``pop_last_session``
  — rolling conversation history.
* :mod:`memory.orchestrator` — the higher-level status / observe / query API
  used by ``/api/features/memory/*``.
"""

from __future__ import annotations

from .memory_manager import (  # noqa: F401
    forget,
    format_memory_for_prompt,
    load_memory,
    pop_last_session,
    remember,
    save_memory,
    save_session_summary,
    update_memory,
)

__all__ = [
    "load_memory",
    "update_memory",
    "format_memory_for_prompt",
    "remember",
    "forget",
    "save_session_summary",
    "pop_last_session",
    "save_memory",
]
