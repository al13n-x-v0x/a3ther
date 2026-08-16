"""
importance.py — decides what is worth remembering.

A two-tier scorer:

1. Heuristic tier (always runs): rewards preference/identity signals,
   named entities, project words, and specific detail; penalises filler.
2. LLM tier (optional): when a gateway provider is available, asks the
   model for a 0-1 importance score for borderline statements.

``should_remember(text)`` combines both and applies the threshold.
"""
from __future__ import annotations

import logging
import re

LOGGER = logging.getLogger("a3ther.memory")

# Strong signals — these statements are almost always worth storing.
_HIGH_SIGNALS = (
    "my name is", "i am", "i work", "i study", "i live", "my birthday",
    "i prefer", "i like", "i love", "i hate", "i want", "i need", "i use",
    "my favorite", "my favourite", "i'm building", "i'm working", "i'm learning",
    "remember", "don't forget", "never forget", "project", "deadline",
    "i have a", "i play", "my job", "my role",
)

_FILLER = (
    "hello", "hi ", "hey ", "thanks", "thank you", "ok", "okay", "yeah",
    "hmm", "um ", "uh ", "lol", "what's up", "how are you", "good morning",
    "good night", "bye", "see you",
)

_ENTITY_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_NUM_RE = re.compile(r"\d")


def heuristic_score(text: str) -> float:
    """0-1 heuristic importance for a statement."""
    low = (text or "").strip().lower()
    if not low:
        return 0.0

    score = 0.2  # base — anything with content has some value

    if any(s in low for s in _HIGH_SIGNALS):
        score += 0.35
    entities = len(set(_ENTITY_RE.findall(text or "")))
    score += min(0.2, entities * 0.04)
    if _NUM_RE.search(text or ""):
        score += 0.1
    words = len(low.split())
    if words >= 6:
        score += 0.1
    for filler in _FILLER:
        if low.strip() == filler or low.startswith(filler + " "):
            score -= 0.3
            break
    if any(q in low for q in ("what", "can you", "please tell")):
        score -= 0.2  # questions rarely need storing

    return max(0.0, min(1.0, score))


class ImportanceScorer:
    """Hybrid heuristic + LLM importance scorer."""

    def __init__(self, gateway=None, threshold: float = 0.45):
        self.gateway = gateway
        self.threshold = threshold

    def score(self, text: str) -> float:
        """Final 0-1 score (heuristic, boosted by LLM for borderline cases)."""
        base = heuristic_score(text)
        if self.threshold <= base:
            return base
        if base < 0.15:
            return base  # clearly fluff — don't burn an LLM call
        llm = self._llm_score(text)
        return max(base, llm or base)

    def should_remember(self, text: str) -> bool:
        return self.score(text) >= self.threshold

    # ------------------------------------------------------------------ #
    def _llm_score(self, text: str) -> float | None:
        if self.gateway is None:
            return None
        try:
            prompt = (
                "Rate how important this statement is for long-term memory "
                "about the user (preferences, identity, projects, plans). "
                "Reply with ONLY a number 0.0 to 1.0.\n\nStatement: "
                + (text or "")[:400]
            )
            reply = self.gateway.complete_text(prompt, max_tokens=8)
            return max(0.0, min(1.0, float(reply.strip())))
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("LLM importance scoring failed: %s", exc)
            return None
