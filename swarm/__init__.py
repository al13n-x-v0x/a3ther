"""
A3THER Multi-Agent Swarm.

A supervisor routes complex tasks to specialised sub-agents (code,
research, automation, mail) over a shared :class:`swarm.state.AgentState`
canvas and an async mailbox, with explicit ``transfer_to`` hand-offs and
a live event stream for the frontend terminal.

- :mod:`swarm.state`      — shared state canvas + transition log
- :mod:`swarm.queue`      — async agent-to-agent mailboxes
- :mod:`swarm.agents`     — specialized agents + routing keywords
- :mod:`swarm.supervisor` — planning + execution orchestration
- :mod:`swarm.events`     — process-wide event stream (dashboard)
"""
from .events import EventLog, get_event_log
from .queue import AgentMailbox
from .state import AgentState
from .supervisor import SupervisorAgent, get_supervisor, run_task

__all__ = [
    "EventLog",
    "get_event_log",
    "AgentMailbox",
    "AgentState",
    "SupervisorAgent",
    "get_supervisor",
    "run_task",
]
