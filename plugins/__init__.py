"""
A3THER Plugin System.

A plugin is a folder under ``plugins/`` (or anywhere in the plugin root)
containing an ``a3ther-plugin.json`` manifest plus either a Python entry
file (hot-loaded via importlib) or a JavaScript entry file (run through
``bridge_node.js`` over the same JSON-RPC stdio transport the MCP host
uses). Dropping a folder in updates the registry instantly on the next
scan — no restart required.

Quick start
-----------
.. code-block:: python

    from plugins.manager import get_plugin_manager

    mgr = get_plugin_manager()
    mgr.discover()
    for plugin in mgr.list_plugins():
        print(plugin.name, plugin.enabled)
"""
from .manager import PluginManager, get_plugin_manager

__all__ = ["PluginManager", "get_plugin_manager"]
