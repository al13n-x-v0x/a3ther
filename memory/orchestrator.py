"""
memory/orchestrator.py — higher-level memory API used by the HUD.

Wraps :mod:`memory.memory_manager` with a small status / observe / query
surface so ``/api/features/memory/status``, ``/observe`` and ``/query`` can
run without the heavy vector-db stack. Keyword-overlap retrieval keeps it
dependency-free and instant; the API shape matches what the frontend expects
(``unit.text`` / ``unit.importance`` / ``unit.category`` + a score).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from . import memory_manager as mm

#: Overweight short function words — they would otherwise dominate matching.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for",
    "is", "are", "was", "were", "be", "been", "it", "its", "this", "that",
    "with", "as", "at", "by", "from", "your", "you", "my", "me", "i", "we",
    "they", "he", "she", "do", "does", "did", "have", "has", "had", "not",
    "can", "could", "will", "would", "should", "what", "when", "where",
    "how", "which", "who", "than", "then", "there", "here", "so", "too",
}


@dataclass
class MemoryUnit:
    """One retrievable memory item (API shape: text/importance/category)."""

    text: str
    importance: int = 3
    category: str = "memory"
    key: str = ""
    created: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryVectorIndex:
    """Tiny keyword-overlap index over the memory store (no deps)."""

    def __init__(self, orchestrator: "MemoryOrchestrator") -> None:
        self._orchestrator = orchestrator

    def _tokenize(self, text: str) -> List[str]:
        import re

        words = re.findall(r"[a-z0-9']+", (text or "").lower())
        return [w for w in words if w not in _STOPWORDS and len(w) > 1]

    def search(self, query: str, k: int = 5) -> List[Tuple[MemoryUnit, float]]:
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return []
        scored: List[Tuple[MemoryUnit, float]] = []
        for unit in self._orchestrator._all_units():
            text_tokens = self._tokenize(unit.text)
            if not text_tokens:
                continue
            overlap = len(set(q_tokens) & set(text_tokens))
            if overlap == 0:
                continue
            # Importance nudges the score up to 40%; recency as a tiebreak.
            recency = 1.0
            if unit.created:
                try:
                    age_h = (time.time() - time.mktime(
                        time.strptime(unit.created, "%Y-%m-%d %H:%M:%S")
                    )) / 3600.0
                    recency = max(0.2, 1.0 - age_h / (24 * 30))  # ~1 month half-life
                except Exception:  # noqa: BLE001
                    recency = 1.0
            score = (overlap / max(len(q_tokens), 1)) * (0.6 + 0.08 * unit.importance) * recency
            scored.append((unit, round(score, 4)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]


class MemoryOrchestrator:
    """Status / observe / query facade over the JSON memory store."""

    def __init__(self) -> None:
        self.vector = MemoryVectorIndex(self)

    # ---------------- status ---------------- #
    def status(self) -> dict:
        store = mm.load_memory()
        facts = len(store.get("facts") or {})
        long_term = len(store.get("long_term") or {})
        notes = len(store.get("notes") or {})
        summaries = len(store.get("session_summaries") or [])
        try:
            from config.paths import get_data_dir

            store_path = str(get_data_dir() / "memory.json")
        except Exception:  # noqa: BLE001
            store_path = ""
        return {
            "system": "A3THER Memory Vault",
            "status": "ONLINE",
            "stored": facts + long_term,
            "facts": facts,
            "long_term": long_term,
            "notes": notes,
            "session_summaries": summaries,
            "store": store_path,
        }

    # ---------------- observe ---------------- #
    def observe(self, text: str) -> bool:
        """Store a free-form observation as a memory entry (dedup by text)."""
        text = (text or "").strip()
        if not text:
            return False
        key = "obs_" + str(abs(hash(text.lower())))
        mm.save_memory(key, text, section="long_term")
        return True

    # ---------------- query ---------------- #
    def build_context(self, text: str, k: int = 5) -> str:
        """Build a compact context block for the prompt (or '' when empty)."""
        hits = self.vector.search(text, k=k)
        if not hits:
            # Fall back to the full prompt formatter so a fresh store still
            # injects session history + preferences.
            return mm.format_memory_for_prompt()
        lines = ["[RELEVANT MEMORY]"]
        for unit, score in hits:
            lines.append(f"- {unit.text} (importance {unit.importance})")
        return "\n".join(lines)

    # ---------------- internal ---------------- #
    def _all_units(self) -> List[MemoryUnit]:
        store = mm.load_memory()
        units: List[MemoryUnit] = []
        for section, category in (("facts", "fact"), ("long_term", "memory"), ("notes", "note")):
            for key, entry in (store.get(section) or {}).items():
                if isinstance(entry, dict):
                    value = entry.get("value")
                    units.append(
                        MemoryUnit(
                            text=str(value or ""),
                            importance=int(entry.get("importance", 3) or 3),
                            category=category,
                            key=str(key),
                            created=str(entry.get("created", "")),
                            metadata={"section": section},
                        )
                    )
                elif entry is not None:
                    units.append(
                        MemoryUnit(
                            text=str(entry),
                            importance=3,
                            category=category,
                            key=str(key),
                        )
                    )
        return units


# --------------------------------------------------------------------------- #
# Singleton
# --------------------------------------------------------------------------- #
_ORCHESTRATOR: MemoryOrchestrator | None = None
_ORCHESTRATOR_LOCK = threading.Lock()


def get_memory_orchestrator() -> MemoryOrchestrator:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        with _ORCHESTRATOR_LOCK:
            if _ORCHESTRATOR is None:
                _ORCHESTRATOR = MemoryOrchestrator()
    return _ORCHESTRATOR


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    orch = get_memory_orchestrator()
    orch.observe("The user's favourite color is cyan and they love remote control features.")
    print("status:", orch.status())
    print("context:", orch.build_context("favourite color", k=3))
