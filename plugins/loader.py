"""
loader.py — hot-loads plugin folders into the running process.

- Python plugins: loaded with ``importlib`` under a unique module name,
  instantiated, and ``on_load()`` fired.
- JavaScript plugins: a Node subprocess runs ``bridge_node.js`` with the
  plugin folder; the bridge speaks newline-delimited JSON-RPC over stdio
  — the exact transport the MCP host implements — so :class:`LoadedPlugin`
  wraps it with a uniform ``call()`` surface for both languages.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import base_dir
from mcp.transport.base import TransportError

from .manifest import Manifest
from .plugin_base import A3THERPlugin, PluginError

BRIDGE_JS = Path(__file__).resolve().parent / "bridge_node.js"

# Unique names so reloads never collide in sys.modules.
_LOAD_COUNTER = 0

# sys.path reference counts (plugin_dir -> count) and the module name
# registered per plugin, so hot-unload can clean up after itself.
_INSERTED_PATHS: dict[str, int] = {}
_REGISTERED_MODULES: dict[str, str] = {}  # plugin name -> unique module name


class PluginLoadError(RuntimeError):
    """Raised when a plugin cannot be imported or instantiated."""


@dataclass
class LoadedPlugin:
    """Uniform wrapper around a loaded Python or JS plugin."""

    manifest: Manifest
    python: A3THERPlugin | None = None
    js_bridge: Any | None = None
    capabilities: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.manifest.name

    def call(self, capability: str, params: dict | None = None) -> str:
        """Execute a capability; returns a string suitable for the brain/UI."""
        params = params or {}
        try:
            if self.python is not None:
                result = self.python.handle(capability, params)
            elif self.js_bridge is not None:
                result = self.js_bridge.call(capability, params)
            else:
                raise PluginError("Plugin has no runtime loaded.")
        except PluginError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PluginError(f"Plugin {self.name!r} failed: {exc}") from exc
        if result is None:
            result = ""
        return result if isinstance(result, str) else str(result)

    def unload(self) -> None:
        if self.python is not None:
            try:
                self.python.on_unload()
            except Exception:  # noqa: BLE001
                pass
            try:
                plugin_dir = self.manifest.path.parent if self.manifest.path else None
                if plugin_dir is not None:
                    unload_python_plugin(plugin_dir, self.manifest.name)
            except Exception:  # noqa: BLE001
                pass
        if self.js_bridge is not None:
            try:
                self.js_bridge.close()
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------------------- #
# Python loader
# ------------------------------------------------------------------------- #
def load_python_plugin(plugin_dir: Path, manifest: Manifest) -> A3THERPlugin:
    """Import and instantiate a Python plugin from its entry file."""
    global _LOAD_COUNTER
    entry = plugin_dir / manifest.entry
    if not entry.exists():
        raise PluginLoadError(f"Entry file not found: {entry}")

    # Make the repo root importable so plugins can use 'from plugins...'.
    root = base_dir()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Reference-count the plugin dir so unload can remove it when the last
    # plugin using it goes away.
    plugin_key = str(plugin_dir)
    if plugin_key not in sys.path:
        sys.path.insert(0, plugin_key)
    _INSERTED_PATHS[plugin_key] = _INSERTED_PATHS.get(plugin_key, 0) + 1

    _LOAD_COUNTER += 1
    module_name = f"_a3ther_plugin_{manifest.safe_module_name()}_{_LOAD_COUNTER}"
    _REGISTERED_MODULES[manifest.name] = module_name

    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"Cannot build import spec for {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(module_name, None)
        _REGISTERED_MODULES.pop(manifest.name, None)
        raise PluginLoadError(f"Import of {entry} failed: {exc}") from exc

    plugin_cls = getattr(module, "Plugin", None)
    if plugin_cls is None:
        # Fallback: find the first A3THERPlugin subclass in the module.
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, A3THERPlugin)
                and value is not A3THERPlugin
            ):
                plugin_cls = value
                break
    if plugin_cls is None:
        raise PluginLoadError(
            f"Plugin {manifest.name!r} must define a class named 'Plugin' "
            "subclassing A3THERPlugin."
        )

    try:
        instance = plugin_cls(plugin_dir=str(plugin_dir))
    except TypeError:
        instance = plugin_cls()
    instance.on_load()
    return instance


# ------------------------------------------------------------------------- #
# JavaScript loader
# ------------------------------------------------------------------------- #
class JsPluginBridge:
    """Node bridge speaking JSON-RPC over stdio (reuses StdioTransport)."""

    def __init__(self, plugin_dir: Path, node: str = "node"):
        from mcp.transport.stdio_transport import StdioTransport

        self.plugin_dir = Path(plugin_dir)
        self.transport = StdioTransport(command=[node, str(BRIDGE_JS), str(self.plugin_dir)])

    def connect(self) -> None:
        self.transport.connect()

    def capabilities(self) -> list[dict]:
        try:
            result = self.transport.request("capabilities", {}, timeout=15) or {}
        except TransportError as exc:
            raise PluginLoadError(str(exc)) from exc
        if isinstance(result, dict) and result.get("error"):
            raise PluginLoadError(str(result["error"]))
        return list((result or {}).get("capabilities") or [])

    def call(self, capability: str, params: dict | None = None) -> str:
        try:
            result = self.transport.request(
                "call",
                {"capability": capability, "arguments": params or {}},
                timeout=60,
            )
        except TransportError as exc:
            raise PluginError(str(exc)) from exc
        if isinstance(result, dict) and result.get("error"):
            raise PluginError(str(result["error"]))
        return result if isinstance(result, str) else str(result)

    def close(self) -> None:
        self.transport.close()


def load_js_plugin(plugin_dir: Path, manifest: Manifest) -> JsPluginBridge:
    """Spawn the Node bridge for a JavaScript plugin."""
    if not Path(plugin_dir / manifest.entry).exists():
        raise PluginLoadError(f"Entry file not found: {plugin_dir / manifest.entry}")
    bridge = JsPluginBridge(plugin_dir)
    try:
        bridge.connect()
    except Exception as exc:  # noqa: BLE001
        raise PluginLoadError(f"Node bridge failed to start: {exc}") from exc
    return bridge


# ------------------------------------------------------------------------- #
# Dispatch
# ------------------------------------------------------------------------- #
def unload_python_plugin(plugin_dir: Path, name: str) -> None:
    """Reverse the sys.modules / sys.path side effects of a Python load."""
    module_name = _REGISTERED_MODULES.pop(name, None)
    if module_name:
        sys.modules.pop(module_name, None)
    key = str(Path(plugin_dir))
    if key in _INSERTED_PATHS:
        _INSERTED_PATHS[key] -= 1
        if _INSERTED_PATHS[key] <= 0:
            _INSERTED_PATHS.pop(key, None)
            if key in sys.path:
                sys.path.remove(key)


def load_plugin(plugin_dir: Path, manifest: Manifest) -> LoadedPlugin:
    """Load any plugin by its manifest type into a LoadedPlugin wrapper."""
    plugin_dir = Path(plugin_dir)

    if manifest.plugin_type == "python":
        python = load_python_plugin(plugin_dir, manifest)
        return LoadedPlugin(
            manifest=manifest,
            python=python,
            capabilities=python.capabilities_list(),
        )

    if manifest.plugin_type == "javascript":
        bridge = load_js_plugin(plugin_dir, manifest)
        return LoadedPlugin(
            manifest=manifest,
            js_bridge=bridge,
            capabilities=bridge.capabilities(),
        )

    if manifest.plugin_type == "mcp":
        # MCP-type plugins are thin references to a server managed by the
        # MCP host; the manager surfaces them as capability entries.
        return LoadedPlugin(
            manifest=manifest,
            capabilities=list(manifest.capabilities),
        )

    raise PluginLoadError(
        f"Unsupported plugin type: {manifest.plugin_type!r}"
    )
