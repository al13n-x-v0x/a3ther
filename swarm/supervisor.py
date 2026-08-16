"""
supervisor.py — the Router/Commander.

For complex queries the supervisor:

1. builds a multi-step plan (LLM-generated JSON steps, with a rule-based
   fallback that splits on "and then / and / then"),
2. executes steps sequentially, routing each to the best-fit agent,
3. processes agent hand-offs (``transfer_to``) through the mailbox,
4. tracks every transition on the shared :class:`AgentState` canvas and
   the process-wide event log for the frontend terminal.
"""
from __future__ import annotations

import json
import logging
import re
import threading

from .agents import BaseAgent, create_agent, route_to_agent
from .events import get_event_log
from .queue import AgentMailbox
from .state import AgentState

LOGGER = logging.getLogger("a3ther.swarm")

_SPLIT_RE = re.compile(r"\s+(?:and\s+then|then|and|,|\.)\s+", re.IGNORECASE)


class SupervisorAgent:
    """Centralised routing + state orchestration."""

    def __init__(self, gateway=None, mailbox: AgentMailbox | None = None):
        self.gateway = gateway
        self.mailbox = mailbox or AgentMailbox()
        self._agents: dict[str, BaseAgent] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def _get_agent(self, name: str) -> BaseAgent:
        with self._lock:
            if name not in self._agents:
                self._agents[name] = create_agent(name, self.mailbox, self.gateway)
            return self._agents[name]

    # ------------------------------------------------------------------ #
    def plan(self, task: str) -> list[str]:
        """Decompose a task into ordered steps."""
        llm_steps = self._plan_llm(task)
        if llm_steps:
            return llm_steps
        # Rule-based fallback: split on connectors.
        parts = [p.strip() for p in _SPLIT_RE.split(task) if p.strip()]
        return parts or [task]

    def _plan_llm(self, task: str) -> list[str] | None:
        if self.gateway is None:
            return None
        try:
            prompt = (
                "Break this task into 1-4 concrete sequential steps. "
                "Return ONLY a JSON array of step strings. No prose.\n\nTask: "
                + (task or "")[:600]
            )
            reply = self.gateway.complete_text(prompt, max_tokens=400)
            data = json.loads(reply.strip().strip("`").removeprefix("json"))
            if isinstance(data, list) and data:
                return [str(s) for s in data]
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("LLM planning failed: %s", exc)
        return None

    # ------------------------------------------------------------------ #
    def run(self, task: str, on_event: callable | None = None) -> dict:
        """Execute a complex task end-to-end; returns the final result dict."""
        state = AgentState(task=task)
        log = get_event_log()
        log.emit("plan", f"Supervisor planning: {task[:100]}", agent="supervisor")
        if on_event:
            on_event("plan", {"task": task})

        steps = self.plan(task)
        state.set("steps", steps, by="supervisor")
        log.emit("plan", f"Plan: {len(steps)} step(s) → {steps}", agent="supervisor")

        results: list[str] = []
        try:
            for index, step in enumerate(steps, start=1):
                agent_name = route_to_agent(step)
                log.emit("start", f"Step {index}/{len(steps)} → {agent_name}", agent="supervisor")
                state.event("start", f"Step {index}: {agent_name} ← '{step[:60]}'", agent="supervisor")

                agent = self._get_agent(agent_name)
                # Run on the shared pool so UI stays responsive.
                future = self.mailbox.submit(agent.handle, step, state)
                result = future.result(timeout=300)

                results.append(f"[{agent_name}] {result}")
                state.set(f"step_{index}_result", result, by=agent_name)
                log.emit("result", f"{agent_name} finished step {index}", agent=agent_name)

                # Process any hand-off the agent queued.
                handoff = self.mailbox.receive(agent_name)
                if handoff:
                    target = self._agents.get(agent_name, agent).name
                    log.emit("transfer", f"hand-off processed: {handoff.get('from')} → {handoff.get('task', '')[:60]}", agent="supervisor")

            summary = "\n\n".join(results)
            state.set("final", summary, by="supervisor")
            state.completed = True
            log.emit("done", "Supervisor completed the task", agent="supervisor")
            if on_event:
                on_event("done", {"summary": summary[:200]})
            return {"ok": True, "steps": steps, "results": results, "summary": summary}
        except Exception as exc:  # noqa: BLE001
            log.emit("error", f"Supervisor failed: {exc}", agent="supervisor")
            state.event("error", str(exc), agent="supervisor")
            return {"ok": False, "steps": steps, "error": str(exc)}


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_SUPERVISOR: SupervisorAgent | None = None
_SUPERVISOR_LOCK = threading.Lock()


def get_supervisor() -> SupervisorAgent:
    """Return the process-wide supervisor singleton."""
    global _SUPERVISOR
    if _SUPERVISOR is None:
        with _SUPERVISOR_LOCK:
            if _SUPERVISOR is None:
                from gateway.router import get_gateway

                _SUPERVISOR = SupervisorAgent(gateway=get_gateway())
    return _SUPERVISOR


def run_task(task: str, on_event: callable | None = None) -> dict:
    """Convenience: run a task through the singleton supervisor."""
    return get_supervisor().run(task, on_event=on_event)
