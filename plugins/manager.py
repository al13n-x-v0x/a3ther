"""
manager.py — the plugin registry and lifecycle owner.

Responsibilities
----------------
- Scan the plugin root for ``a3ther-plugin.json`` manifests.
- Load/unload plugin runtimes (Python importlib / JS Node bridge).
- Persist enable/disable toggles to ``config/plugins_state.json``.
- Hot-reload changed plugins (mtime tracking).
- Dispatch capability calls: ``execute(capability, params)`` finds the
  first loaded plugin exposing that capability and runs it.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from config import base_dir

from .loader import LoadedPlugin, PluginLoadError, load_plugin
from .manifest import MANIFEST_FILENAME, Manifest, discover_manifests

# Capabilities that are fulfilled by the MCP host (registered at runtime).
MCP_CAPABILITY_PREFIX = "mcp::"


@dataclass
class PluginInfo:
    """Dashboard/API snapshot of one plugin."""

    name: str
    version: str
    description: str
    author: str
    plugin_type: str
    enabled: bool
    loaded: bool
    capabilities: list[dict] = field(default_factory=list)
    error: str | None = None
    path: str = ""
    entry: str = ""


class PluginManager:
    """Owns the plugin lifecycle for the whole process."""

    def __init__(self, plugins_root: str | Path | None = None, state_path: str | Path | None = None):
        self.plugins_root = Path(plugins_root or base_dir() / "plugins")
        self.state_path = Path(state_path or base_dir() / "config" / "plugins_state.json")
        self._lock = threading.RLock()

        self._manifests: dict[str, Manifest] = {}          # name -> manifest
        self._loaded: dict[str, LoadedPlugin] = {}          # name -> runtime
        self._errors: dict[str, str] = {}                   # name -> load error
        self._mtimes: dict[str, float] = {}                 # name -> manifest mtime
        self._enabled_state: dict[str, bool] = {}

        self._load_state()
        self.discover(force=True)

    # ------------------------------------------------------------------ #
    # State persistence (enable/disable toggles)
    # ------------------------------------------------------------------ #
    def _load_state(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._enabled_state = {k: bool(v) for k, v in (data.get("plugins") or {}).items()}
        except Exception:
            self._enabled_state = {}

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"plugins": self._enabled_state}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Discovery / hot reload
    # ------------------------------------------------------------------ #
    def discover(self, force: bool = False) -> int:
        """Scan the plugin root; load new/changed plugins. Returns count loaded."""
        with self._lock:
            manifests = discover_manifests(self.plugins_root)
            by_name: dict[str, Manifest] = {}
            for manifest in manifests:
                by_name[manifest.name] = manifest

            loaded_count = 0
            # Detect removals — fully purge plugins that vanished from disk
            # so they never linger as ghost entries in the dashboard.
            for name in list(self._manifests.keys()):
                if name not in by_name:
                    self._unload_locked(name)
                    self._manifests.pop(name, None)
                    self._mtimes.pop(name, None)
                    self._enabled_state.pop(name, None)

            for name, manifest in by_name.items():
                self._manifests[name] = manifest
                mtime = self._mtime_of(manifest)
                changed = self._mtimes.get(name) != mtime
                self._mtimes[name] = mtime

                enabled = self._enabled_state.get(name, manifest.enabled)
                already_loaded = name in self._loaded

                if not enabled and already_loaded:
                    self._unload_locked(name)
                    continue
                if not enabled:
                    continue
                if already_loaded and not changed and not force:
                    continue
                if not already_loaded and name in self._errors and not changed and not force:
                    continue

                try:
                    plugin_dir = manifest.path.parent if manifest.path else self.plugins_root / name
                    loaded = load_plugin(plugin_dir, manifest)
                    self._loaded[name] = loaded
                    self._errors.pop(name, None)
                    loaded_count += 1
                except PluginLoadError as exc:
                    self._errors[name] = str(exc)
                    self._loaded.pop(name, None)

            return loaded_count

    @staticmethod
    def _mtime_of(manifest: Manifest) -> float:
        try:
            return manifest.path.stat().st_mtime if manifest.path else 0.0
        except Exception:
            return 0.0

    def reload(self, name: str | None = None) -> list[str]:
        """Force-reload one plugin (or all) and return affected names."""
        with self._lock:
            if name is None:
                affected = list(self._manifests.keys())
                self.discover(force=True)
                return affected
            manifest = self._manifests.get(name)
            if manifest is None:
                return []
            self._unload_locked(name)
            self._mtimes.pop(name, None)
            self.discover(force=True)
            return [name] if name in self._manifests else []

    # ------------------------------------------------------------------ #
    def _unload_locked(self, name: str) -> None:
        loaded = self._loaded.pop(name, None)
        if loaded is not None:
            loaded.unload()
        self._errors.pop(name, None)

    # ------------------------------------------------------------------ #
    # Enable / disable
    # ------------------------------------------------------------------ #
    def set_enabled(self, name: str, enabled: bool) -> PluginInfo | None:
        with self._lock:
            self._enabled_state[name] = bool(enabled)
            self._save_state()
            if enabled:
                self.discover(force=True)   # loads the plugin
            else:
                self._unload_locked(name)
        return self.get(name)

    def unload(self, name: str) -> None:
        with self._lock:
            self._unload_locked(name)

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get(self, name: str) -> PluginInfo | None:
        manifest = self._manifests.get(name)
        if manifest is None:
            return None
        loaded = self._loaded.get(name)
        return PluginInfo(
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            author=manifest.author,
            plugin_type=manifest.plugin_type,
            enabled=self._enabled_state.get(name, manifest.enabled),
            loaded=loaded is not None,
            capabilities=list(loaded.capabilities) if loaded else list(manifest.capabilities),
            error=self._errors.get(name),
            path=str(manifest.path) if manifest.path else "",
            entry=manifest.entry,
        )

    def list_plugins(self) -> list[PluginInfo]:
        """Snapshot of every discovered plugin (sorted by name)."""
        with self._lock:
            return [self.get(name) for name in sorted(self._manifests) if self.get(name) is not None]

    # ------------------------------------------------------------------ #
    # Capability dispatch
    # ------------------------------------------------------------------ #
    def find_by_capability(self, capability: str) -> LoadedPlugin | None:
        with self._lock:
            for loaded in self._loaded.values():
                for cap in loaded.capabilities:
                    if cap.get("name") == capability:
                        return loaded
        return None

    def execute(self, capability: str, params: dict | None = None) -> str:
        """Run a capability across all loaded plugins."""
        if capability.startswith(MCP_CAPABILITY_PREFIX):
            # Route to the MCP host: mcp::<server>__<tool>
            from mcp.host import get_mcp_host

            tool_name = capability[len(MCP_CAPABILITY_PREFIX):]
            result = get_mcp_host().call_llm_tool(tool_name, params or {})
            return result if isinstance(result, str) else str(result)

        plugin = self.find_by_capability(capability)
        if plugin is None:
            raise PluginLoadError(f"No loaded plugin exposes capability {capability!r}")
        return plugin.call(capability, params or {})

    # ------------------------------------------------------------------ #
    def describe_mcp_capabilities(self) -> list[dict]:
        """Expose connected MCP tools as dashboard-visible capabilities."""
        try:
            from mcp.host import get_mcp_host

            tools = get_mcp_host().list_tools()
        except Exception:  # noqa: BLE001
            return []
        return [
            {
                "name": MCP_CAPABILITY_PREFIX + f"{tool.server}__{tool.name}",
                "description": tool.description or f"MCP tool {tool.name}",
                "parameters": tool.input_schema,
                "origin": "mcp",
            }
            for tool in tools
        ]


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_MANAGER: PluginManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_plugin_manager() -> PluginManager:
    """Return the process-wide plugin manager singleton."""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = PluginManager()
    return _MANAGER


def reset_plugin_manager() -> None:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            for plugin in _MANAGER.list_plugins():
                _MANAGER.unload(plugin.name)
        _MANAGER = None
