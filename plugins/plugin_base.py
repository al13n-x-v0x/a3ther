"""
plugin_base.py — the plugin protocol.

Python plugins expose a class named ``Plugin`` that subclasses
:class:`A3THERPlugin`. JS plugins expose ``capabilities`` and a
``handle(capability, params)`` function (see ``bridge_node.js``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Capability:
    """One callable ability a plugin exposes to A3THER."""

    name: str
    description: str = ""
    parameters: dict = field(default_factory=dict)


class A3THERPlugin:
    """Base class for Python plugins.

    Subclasses set ``name``/``version``/``description``/``capabilities``
    and implement :meth:`handle`. The loader instantiates the class with
    the plugin directory and calls :meth:`on_load` after construction.
    """

    name = "base"
    version = "0.0.1"
    description = ""
    author = ""
    capabilities: list[Capability] = []

    def __init__(self, plugin_dir: str | Path | None = None):
        self.plugin_dir = Path(plugin_dir) if plugin_dir else None

    # ------------------------------------------------------------------ #
    def capabilities_list(self) -> list[dict]:
        """Manifest-shaped capability list for the UI/API."""
        return [
            {
                "name": cap.name,
                "description": cap.description,
                "parameters": cap.parameters,
            }
            for cap in self.capabilities
        ]

    def handle(self, capability: str, params: dict) -> str:
        """Execute ``capability`` with ``params`` and return a string result.

        Raise :class:`PluginError` (or any exception) to surface a failure
        to the caller; the manager wraps it into a readable message.
        """
        raise NotImplementedError(
            f"Plugin {self.name!r} does not implement handle()"
        )

    # ------------------------------------------------------------------ #
    def on_load(self) -> None:
        """Called once after the plugin module is imported and instantiated."""

    def on_unload(self) -> None:
        """Called when the plugin is disabled or the manager shuts down."""


class PluginError(RuntimeError):
    """Raised by plugins to report a user-facing failure."""
