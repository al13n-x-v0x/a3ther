"""
orchestrator.py — the long-term memory workflow.

Background routine
------------------
1. ``observe(user_text, assistant_text)`` is called as messages flow.
2. The importance scorer filters out fluff.
3. LLM fact extraction (JSON triples + facts) with rule-based fallback.
4. Facts are embedded and upserted into the episodic :class:`VectorStore`.
5. Entities/relations are upserted into the semantic :class:`KnowledgeGraph`.

Context retrieval
-----------------
``build_context(query, k)`` searches both stores and returns a compact,
prompt-ready block of relevant historical facts — injected into the
system prompt without overflowing the context window.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time

from .embeddings import Embedder
from .importance import ImportanceScorer
from .knowledge_graph import KnowledgeGraph
from .vector_store import MemoryUnit, VectorStore

LOGGER = logging.getLogger("a3ther.memory")


class MemoryOrchestrator:
    """Coordinates episodic + semantic long-term memory."""

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        graph: KnowledgeGraph | None = None,
        scorer: ImportanceScorer | None = None,
        gateway=None,
    ):
        self.gateway = gateway
        self.vector = vector_store or VectorStore()
        self.graph = graph or KnowledgeGraph()
        self.scorer = scorer or ImportanceScorer(gateway=gateway)

        self._feed: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=256)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.stats = {"observed": 0, "stored": 0}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def observe(self, user_text: str, assistant_text: str = "") -> bool:
        """Queue a conversation turn for background processing."""
        text = (user_text or "").strip()
        if not text:
            return False
        self._feed.put((text, assistant_text or ""))
        self.stats["observed"] += 1
        self.ensure_worker()
        return True

    def ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker_loop, name="memory-worker", daemon=True
        )
        self._thread.start()

    def build_context(self, query: str, k: int = 5) -> str:
        """Retrieve relevant history from vectors + graph as a prompt block."""
        parts: list[str] = []

        hits = self.vector.search(query, k=k)
        if hits:
            lines = [
                f"- ({unit.category}) {unit.text[:300]}"
                for unit, _score in hits
            ]
            parts.append("[RECALLED MEMORIES]\n" + "\n".join(lines))

        entity_ids = self.graph.find_node(query)
        if entity_ids:
            block = self.graph.to_prompt_block(entity_ids)
            if block:
                parts.append(block)

        return "\n\n".join(parts)

    def status(self) -> dict:
        return {
            "observed": self.stats["observed"],
            "stored": self.stats["stored"],
            "episodic": self.vector.count(),
            "graph": self.graph.stats(),
        }

    # ------------------------------------------------------------------ #
    # Background worker
    # ------------------------------------------------------------------ #
    def _worker_loop(self) -> None:
        while True:
            try:
                user_text, assistant_text = self._feed.get(timeout=0.5)
            except queue.Empty:
                if self._feed.empty():
                    break
                continue
            try:
                self._process(user_text, assistant_text)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Memory processing failed: %s", exc)

    def _process(self, user_text: str, assistant_text: str) -> None:
        if not self.scorer.should_remember(user_text):
            return

        facts = self._extract_facts(user_text)
        unit_id = hashlib.sha1(user_text.encode("utf-8")).hexdigest()[:16]

        if not facts:
            # Store the raw statement as a low-confidence note.
            self.vector.upsert(
                MemoryUnit(
                    id=unit_id,
                    text=user_text,
                    created=time.time(),
                    updated=time.time(),
                    importance=self.scorer.score(user_text),
                    category="note",
                )
            )
            self.stats["stored"] += 1
            return

        for fact in facts:
            self._store_fact(fact)

    def _store_fact(self, fact: dict) -> None:
        fact_id = hashlib.sha1(json.dumps(fact, sort_keys=True).encode()).hexdigest()[:16]
        self.vector.upsert(
            MemoryUnit(
                id=fact_id,
                text=fact.get("text", ""),
                created=time.time(),
                updated=time.time(),
                importance=float(fact.get("importance", 0.6)),
                category=fact.get("category", "fact"),
                meta={"type": fact.get("type", "fact")},
            )
        )
        self.stats["stored"] += 1

        for triple in fact.get("triples", []):
            self.graph.add_edge(
                triple.get("subject", "user"),
                triple.get("relation", "KNOWS"),
                triple.get("object", "unknown"),
                weight=1.0,
                evidence=fact.get("text", "")[:120],
            )

    # ------------------------------------------------------------------ #
    # Fact extraction
    # ------------------------------------------------------------------ #
    def _extract_facts(self, text: str) -> list[dict]:
        llm_facts = self._extract_llm(text)
        if llm_facts:
            return llm_facts

        # Rule-based fallback triples.
        triples: list[dict] = []
        low = text.lower()
        patterns = [
            (r"my name is\s+([\w\s]+?)(?:\.|,|$)", "user", "IS", "person"),
            (r"i (?:work|am working) on\s+(.+?)(?:\.|,|$)", "user", "WORKS_ON", "project"),
            (r"i (?:like|love|prefer)\s+(.+?)(?:\.|,|$)", "user", "LIKES", "thing"),
            (r"i (?:use|am using)\s+(.+?)(?:\.|,|$)", "user", "USES", "tool"),
            (r"i (?:want|need)\s+(.+?)(?:\.|,|$)", "user", "WANTS", "goal"),
            (r"i live in\s+(.+?)(?:\.|,|$)", "user", "LOCATED_IN", "place"),
        ]
        for pattern, src, rel, node_type in patterns:
            match = re.search(pattern, low)
            if match and match.group(1).strip():
                obj = match.group(1).strip()
                triples.append(
                    {"subject": src, "relation": rel, "object": obj}
                )
                if not re.match(r"\b(user|a3ther|jarvis)\b", obj):
                    self.graph.add_node(obj, node_type)

        if not triples:
            return []
        return [{"text": text, "type": "statement", "category": "note", "triples": triples}]

    def _extract_llm(self, text: str) -> list[dict]:
        if self.gateway is None:
            return []
        try:
            prompt = (
                "Extract durable facts about the user from this statement. "
                "Return ONLY valid JSON: "
                '{"facts": [{"text": "...", "category": "preference|identity|project|plan", '
                '"triples": [{"subject": "...", "relation": "LIKES|WORKS_ON|USES|IS|WANTS", '
                '"object": "..."}]}]} '
                'Use "user" as the subject when the user is implied.\n\nStatement: '
                + (text or "")[:600]
            )
            reply = self.gateway.complete_text(prompt, max_tokens=512)
            data = json.loads(reply.strip().strip("`").removeprefix("json"))
            return list(data.get("facts") or [])
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("LLM fact extraction failed: %s", exc)
            return []


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_ORCHESTRATOR: MemoryOrchestrator | None = None
_ORCHESTRATOR_LOCK = threading.Lock()


def get_memory_orchestrator() -> MemoryOrchestrator:
    """Return the process-wide memory orchestrator singleton."""
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        with _ORCHESTRATOR_LOCK:
            if _ORCHESTRATOR is None:
                _ORCHESTRATOR = MemoryOrchestrator()
    return _ORCHESTRATOR
