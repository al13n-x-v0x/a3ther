"""
A3THER Codebase Agent.

Deep workspace interaction with a fast local symbol index and a
self-correcting test loop:

- :mod:`codebase.indexer`  — AST/regex symbol indexing (architecture map)
- :mod:`codebase.tools`    — LLM function-calling tools for the workspace
- :mod:`codebase.context`  — token-saving snippet context assembly
- :mod:`codebase.agent`    — generate → run tests → read errors → self-heal
"""
from .agent import CodeWorkspaceAgent, LoopReport
from .context import assemble_context
from .indexer import CodeIndexer
from .tools import TOOL_SCHEMAS, execute_tool

__all__ = [
    "CodeWorkspaceAgent",
    "LoopReport",
    "assemble_context",
    "CodeIndexer",
    "TOOL_SCHEMAS",
    "execute_tool",
]
