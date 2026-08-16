"""
agents.py — the swarm's specialized agents.

Each agent exposes a ``capability`` label, a ``describe()`` string, and a
``handle(task, state)`` method. Hand-offs are done via
:func:`transfer_to`, which posts a message to the target agent's inbox
and records the transition — the supervisor's loop picks it up.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .events import get_event_log
from .queue import AgentMailbox
from .state import AgentState

LOGGER = logging.getLogger("a3ther.swarm")


# Tool calls that may block on the network are executed on a worker thread
# with a hard cap, so a hung external call can never stall the swarm pool.
#
# Tradeoff: a future that times out keeps running in the background and keeps
# its pool slot until it returns, so a fully blackholed network can saturate
# the pool. That still degrades gracefully — every caller gets its time bound
# via future.result(timeout=...) — and never deadlocks. The pool lives for the
# process lifetime, which is intentional for a long-running agent daemon.
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="swarm-tool")


def _call_with_timeout(
    fn: Callable[[], str],
    timeout: float = 30.0,
    fallback: Callable[[], str] | None = None,
) -> str:
    """Run ``fn`` with a hard time budget; on failure call ``fallback`` (lazily).

    ``fallback`` is a callable so it is only invoked when the call actually
    failed or exceeded its budget — no wasted work on the success path.
    """
    try:
        future = _EXECUTOR.submit(fn)
        return future.result(timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Tool call timed out or failed (%s) — using fallback", exc)
        if fallback is not None:
            try:
                return fallback()
            except Exception as inner:  # noqa: BLE001
                LOGGER.warning("Fallback also failed: %s", inner)
        return ""


class BaseAgent:
    """Base class: capability, describe, handle."""

    name = "base"

    def __init__(self, mailbox: AgentMailbox | None = None, gateway=None, state: AgentState | None = None):
        self.mailbox = mailbox or AgentMailbox()
        self.gateway = gateway
        self.state = state

    def describe(self) -> str:
        return f"{self.name} agent — {self.capability}"

    def handle(self, task: str, state: AgentState) -> str:  # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    def transfer_to(self, agent_name: str, task: str, state: AgentState, payload: dict | None = None) -> str:
        """Explicitly hand a task to a sibling agent (async hand-off)."""
        message = {"task": task, "payload": payload or {}, "from": self.name}
        self.mailbox.send(agent_name, message)
        get_event_log().emit(
            "transfer",
            f"{self.name} → {agent_name}: {task[:80]}",
            agent=self.name,
            target=agent_name,
        )
        state.event("transfer", f"{self.name} handed '{task[:60]}' to {agent_name}", agent=self.name)
        return f"Transferred to {agent_name}: {task}"


def _llm_fallback(gateway, prompt: str, max_tokens: int = 512) -> str:
    if gateway is None:
        return f"(no gateway configured) {prompt[:120]}"
    return gateway.complete_text(prompt, max_tokens=max_tokens)


class CodeAgent(BaseAgent):
    name = "code"
    capability = "write, refactor, test and fix code in the workspace"

    def handle(self, task: str, state: AgentState) -> str:
        try:
            from codebase.tools import execute_tool
            from codebase.indexer import CodeIndexer

            state.event("event", f"CodeAgent working: {task[:60]}", agent=self.name)
            indexer = CodeIndexer()
            if not indexer.files():
                indexer.index_directory(".")
            # Symbol search informs the task; then delegate generation to LLM.
            result = _llm_fallback(
                self.gateway,
                f"You are the code agent. Resolve this task using the workspace index. "
                f"Task: {task}\nKnown symbols: {indexer.search_symbols(task[:40], limit=6)}",
                max_tokens=800,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            return f"CodeAgent error: {exc}"


class ResearchAgent(BaseAgent):
    name = "research"
    capability = "web research, summarisation and source gathering"

    def handle(self, task: str, state: AgentState) -> str:
        try:
            from actions.web_search import web_search

            state.event("event", f"ResearchAgent searching: {task[:60]}", agent=self.name)

            def _search() -> str:
                return str(web_search({"query": task}))

            # Bounded network call — never hang the swarm worker pool. The
            # LLM fallback is lazy: it only runs if the search fails/times out.
            result = _call_with_timeout(
                _search,
                timeout=30.0,
                fallback=lambda: _llm_fallback(
                    self.gateway, f"Summarise research on: {task}"
                ),
            )
            # result is the search output or the fallback; the `or` covers an
            # empty success/failure edge case.
            return result or _llm_fallback(self.gateway, f"Summarise research on: {task}")
        except Exception as exc:  # noqa: BLE001
            return f"ResearchAgent error: {exc}"


class AutomationAgent(BaseAgent):
    name = "automation"
    capability = "open apps and drive the desktop through the sandbox"

    def handle(self, task: str, state: AgentState) -> str:
        try:
            from actions.open_app import open_app
            from security.sandbox import SandboxedExecutor

            state.event("event", f"AutomationAgent acting: {task[:60]}", agent=self.name)
            # "open <app>" → open_app; otherwise a sandboxed shell command.
            lowered = task.lower()
            if "open " in lowered and "app" in lowered or lowered.startswith("open "):
                app = task.split("open ", 1)[-1].strip().split(" and ")[0].strip()
                return open_app({"app_name": app})
            if lowered.startswith("run "):
                command = task.split("run ", 1)[-1].strip()
                result = SandboxedExecutor().run(command, timeout=30)
                return f"exit={result.get('exit_code')} ok={result.get('ok')}\n{result.get('stdout', '')[:300]}"
            return f"AutomationAgent: unsupported task '{task}' — say 'run <command>' or 'open <app>'."
        except Exception as exc:  # noqa: BLE001
            return f"AutomationAgent error: {exc}"


class MailAgent(BaseAgent):
    name = "mail"
    capability = "compose and send messages"

    def handle(self, task: str, state: AgentState) -> str:
        try:
            from actions.send_message import send_message

            state.event("event", f"MailAgent composing: {task[:60]}", agent=self.name)
            return str(send_message({"message": task}))
        except Exception as exc:  # noqa: BLE001
            return f"MailAgent error: {exc}"


# ------------------------------------------------------------------------- #
# Registry
# ------------------------------------------------------------------------- #
AGENT_CLASSES: dict[str, type[BaseAgent]] = {
    "code": CodeAgent,
    "research": ResearchAgent,
    "automation": AutomationAgent,
    "mail": MailAgent,
}

# Keyword → agent routing for the supervisor's rule-based fallback.
ROUTING_KEYWORDS = {
    "code": ["code", "script", "write", "refactor", "bug", "test", "python", "function", "implement"],
    "research": ["research", "search", "find", "investigate", "compare", "learn", "what is", "top "],
    "automation": ["open", "arrange", "window", "desktop", "run ", "terminal", "install"],
    "mail": ["mail", "email", "send ", "message"],
}


def create_agent(name: str, mailbox: AgentMailbox, gateway) -> BaseAgent:
    cls = AGENT_CLASSES.get(name)
    if cls is None:
        raise ValueError(f"Unknown agent: {name}")
    return cls(mailbox=mailbox, gateway=gateway)


def route_to_agent(task: str) -> str:
    """Pick the best agent for a task from its keywords."""
    low = (task or "").lower()
    best, best_score = "research", 0  # research is a sensible default
    for name, keywords in ROUTING_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in low)
        if score > best_score:
            best, best_score = name, score
    return best
