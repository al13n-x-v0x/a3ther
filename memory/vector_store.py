"""
vector_store.py — episodic memory (semantic similarity search).

A small, dependency-light vector store persisted as JSON: every memory
unit gets an embedding, and ``search()`` returns the nearest neighbours
by cosine similarity. ``upsert`` deduplicates on ``id`` — re-remembering
the same fact updates it in place instead of creating a duplicate entry.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from config import base_dir

from .crypto import decrypt_str, encrypt_str
from .embeddings import Embedder

LOGGER = logging.getLogger("a3ther.memory")

STORE_PATH = base_dir() / "memory" / "episodic.json"


@dataclass
class MemoryUnit:
    """One stored episodic memory."""

    id: str
    text: str
    created: float
    updated: float
    importance: float = 0.5
    category: str = "note"
    encrypted: bool = False
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        text = encrypt_str(self.text) if self.encrypted else self.text
        return {
            "id": self.id,
            "text": text,
            "created": self.created,
            "updated": self.updated,
            "importance": self.importance,
            "category": self.category,
            "encrypted": self.encrypted,
            "meta": self.meta,
        }


class VectorStore:
    """Persistent cosine-similarity memory store."""

    def __init__(self, path: str | Path | None = None, embedder: Embedder | None = None):
        self.path = Path(path or STORE_PATH)
        self.embedder = embedder or Embedder()
        self._lock = threading.RLock()
        self._units: dict[str, MemoryUnit] = {}
        self._vectors: dict[str, np.ndarray] = {}
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for item in data.get("units", []):
                unit = MemoryUnit(
                    id=item["id"],
                    text=item.get("text", ""),
                    created=item.get("created", time.time()),
                    updated=item.get("updated", time.time()),
                    importance=item.get("importance", 0.5),
                    category=item.get("category", "note"),
                    encrypted=bool(item.get("encrypted", False)),
                    meta=item.get("meta", {}),
                )
                unit.text = decrypt_str(unit.text) if unit.encrypted else unit.text
                self._units[unit.id] = unit
                self._vectors[unit.id] = self.embedder.embed(unit.text)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Vector store load failed: %s", exc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"units": [u.to_dict() for u in self._units.values()]}, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    def upsert(self, unit: MemoryUnit) -> None:
        """Insert or update in place (dedupe by id) and re-embed."""
        with self._lock:
            existing = self._units.get(unit.id)
            if existing is not None:
                unit.created = existing.created
                unit.updated = time.time()
            else:
                unit.created = unit.created or time.time()
                unit.updated = unit.updated or unit.created
            self._units[unit.id] = unit
            self._vectors[unit.id] = self.embedder.embed(unit.text)
            self._save()

    def delete(self, unit_id: str) -> bool:
        with self._lock:
            removed = self._units.pop(unit_id, None)
            self._vectors.pop(unit_id, None)
            if removed is not None:
                self._save()
            return removed is not None

    def get(self, unit_id: str) -> MemoryUnit | None:
        return self._units.get(unit_id)

    def count(self) -> int:
        return len(self._units)

    # ------------------------------------------------------------------ #
    def search(self, query: str, k: int = 5) -> list[tuple[MemoryUnit, float]]:
        """Nearest memories by cosine similarity."""
        with self._lock:
            if not self._units:
                return []
            query_vec = self.embedder.embed(query)
            scored: list[tuple[str, float]] = []
            for unit_id, vec in self._vectors.items():
                scored.append((unit_id, float(np.dot(query_vec, vec))))
            scored.sort(key=lambda item: item[1], reverse=True)
            return [
                (self._units[unit_id], score)
                for unit_id, score in scored[:k]
                if score > 0.1
            ]

    def all(self) -> list[MemoryUnit]:
        return list(self._units.values())
