"""
embeddings.py — text embeddings for the episodic vector store.

Preferred backend: ``sentence-transformers`` (accurate semantic
embeddings). If it is not installed, a deterministic lexical hashing
embedder kicks in — a fixed 512-dim bag-of-words with signed token hashes
— so the memory system works offline with zero heavy dependencies. Cosine
similarity behaves identically on both.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading

import numpy as np

LOGGER = logging.getLogger("a3ther.memory")

DIM = 512
_WORD_RE = re.compile(r"[a-z0-9']+")


class Embedder:
    """Normalised vector embeddings with a dual backend."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _ensure(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name)
                LOGGER.info("Embedder: sentence-transformers (%s)", self.model_name)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "sentence-transformers unavailable (%s) — using lexical hash "
                    "embeddings (offline fallback)",
                    exc,
                )
                self._model = False  # sentinel for "fallback mode"

    def embed(self, text: str) -> np.ndarray:
        """Return a normalised float32 embedding vector."""
        self._ensure()
        text = (text or "").strip()
        if self._model:
            try:
                vec = self._model.encode([text], normalize_embeddings=True)[0]
                return np.asarray(vec, dtype=np.float32)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Transformer embed failed (%s) — falling back", exc)
        return _lexical_embed(text)

    def embed_many(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        return np.stack([self.embed(t) for t in texts])


def _lexical_embed(text: str) -> np.ndarray:
    """Deterministic 512-dim signed hashing bag-of-words (no dependencies)."""
    vec = np.zeros(DIM, dtype=np.float32)
    for token in _WORD_RE.findall((text or "").lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[index] += sign
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors (both expected normalised)."""
    return float(np.dot(a, b))
