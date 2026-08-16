"""
knowledge_graph.py — semantic memory (concept/entity knowledge graph).

A lightweight, pure-Python directed graph persisted to JSON:

- nodes: ``{"id": "alice", "type": "person", "label": "Alice", "props": {...}}``
- edges: ``{"src": "user", "rel": "WORKS_ON", "dst": "a3ther", "weight": 1.0}``

``add_edge`` is deduplicating: re-asserting the same triple bumps the
weight (reinforcement) instead of creating a duplicate. Queries return
neighbours and paths for the context-retrieval pipeline.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from config import base_dir

LOGGER = logging.getLogger("a3ther.memory")

GRAPH_PATH = base_dir() / "memory" / "knowledge_graph.json"

# Canonical relation types the extractor recognises.
RELATIONS = {"IS", "LIKES", "WORKS_ON", "USES", "LOCATED_IN", "PREFERS", "WANTS", "KNOWS"}


class KnowledgeGraph:
    """Persistent adjacency graph of entities and relations."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or GRAPH_PATH)
        self._lock = threading.RLock()
        self._nodes: dict[str, dict] = {}
        self._edges: dict[str, dict] = {}  # key: (src|rel|dst)
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._nodes = data.get("nodes", {})
            self._edges = data.get("edges", {})
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Knowledge graph load failed: %s", exc)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"nodes": self._nodes, "edges": self._edges}, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ #
    def add_node(self, node_id: str, node_type: str = "entity", label: str = "", **props) -> None:
        node_id = node_id.lower()
        with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                self._nodes[node_id] = {
                    "id": node_id,
                    "type": node_type,
                    "label": label or node_id,
                    "props": {},
                    "first_seen": time.time(),
                }
            self._nodes[node_id]["props"].update(props)
            self._save()

    def add_edge(self, src: str, relation: str, dst: str, weight: float = 1.0, **props) -> None:
        """Assert a triple; duplicates reinforce (weight += ) not duplicate."""
        src, dst = src.lower(), dst.lower()
        rel = relation.upper()
        self.add_node(src, "entity")
        self.add_node(dst, "entity")
        key = f"{src}|{rel}|{dst}"
        with self._lock:
            edge = self._edges.get(key)
            if edge is None:
                self._edges[key] = {
                    "src": src,
                    "rel": rel,
                    "dst": dst,
                    "weight": float(weight),
                    "props": {},
                    "first_seen": time.time(),
                }
            else:
                edge["weight"] = float(edge.get("weight", 1.0)) + float(weight)
            self._edges[key]["props"].update(props)
            self._save()

    # ------------------------------------------------------------------ #
    def neighbors(self, node_id: str, relation: str | None = None, limit: int = 10) -> list[dict]:
        """Entities connected to ``node_id`` (outgoing + incoming)."""
        node_id = node_id.lower()
        out: list[dict] = []
        with self._lock:
            for edge in self._edges.values():
                if edge["src"] == node_id or edge["dst"] == node_id:
                    if relation and edge["rel"] != relation.upper():
                        continue
                    other = edge["dst"] if edge["src"] == node_id else edge["src"]
                    out.append(
                        {
                            "entity": other,
                            "relation": edge["rel"],
                            "direction": "out" if edge["src"] == node_id else "in",
                            "weight": edge["weight"],
                        }
                    )
        out.sort(key=lambda e: e["weight"], reverse=True)
        return out[:limit]

    def find_node(self, text: str) -> list[str]:
        """Return node ids whose label/props/type match a token in ``text``."""
        text = text.lower()
        found: list[str] = []
        with self._lock:
            for node_id, node in self._nodes.items():
                haystack = f"{node_id} {node.get('label', '')} {' '.join(node.get('props', {}).values())}".lower()
                if any(token in haystack for token in text.split() if len(token) > 2):
                    found.append(node_id)
        return found

    def stats(self) -> dict:
        with self._lock:
            return {"nodes": len(self._nodes), "edges": len(self._edges)}

    def to_prompt_block(self, node_ids: list[str], limit: int = 20) -> str:
        """Format a compact knowledge block for the system prompt."""
        lines: list[str] = []
        for node_id in node_ids[:6]:
            for edge in self.neighbors(node_id, limit=limit):
                lines.append(f"{edge['entity'].replace('_', ' ')} {edge['relation']} {node_id.replace('_', ' ')}".title())
        return "[KNOWN RELATIONSHIPS]\n" + "\n".join(lines[:limit]) if lines else ""
