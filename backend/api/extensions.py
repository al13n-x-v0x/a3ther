"""
backend/api/extensions.py — A3THER extensions API.

Exposes the multi-model gateway, plugin manager, MCP host, remote SSH
dev mode and the Freaky-Fix autopilot to the web dashboard:

API
---
- ``GET  /api/llm/status``                 — gateway provider status
- ``GET  /api/plugins``                    — plugin list
- ``POST /api/plugins/{name}/toggle``      — enable/disable (body: {"enabled": bool})
- ``POST /api/plugins/{name}/reload``      — hot-reload one plugin
- ``POST /api/plugins/reload``             — hot-reload all
- ``POST /api/plugins/{name}/run``         — run a capability (body: {"capability", "params"})
- ``GET  /api/mcp/servers``                — MCP server status
- ``POST /api/mcp/servers/{name}/connect`` — connect a server
- ``POST /api/mcp/servers/{name}/disconnect``
- ``GET  /api/mcp/tools``                  — flattened server__tool list
- ``POST /api/mcp/tools/call``             — call a tool (body: {"server", "tool", "arguments"})
- ``GET  /api/remote/servers``             — redacted SSH profiles
- ``POST /api/remote/servers/{name}/test`` — connectivity test
- ``POST /api/remote/servers/{name}/exec`` — run a command (body: {"command", "timeout"})
- ``GET  /api/remote/status``              — active dev session
- ``POST /api/devmode/handle``             — route text through the dev-mode listener
- ``POST /api/autopilot/run``              — Freaky-Fix a command (body: {"command", "cwd", "timeout", "max_attempts"})

UI (served from Frontend/)
- ``GET /plugins``, ``/plugins/plugins.js``, ``/plugins/plugins.css``
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["extensions"])
ui_router = APIRouter(tags=["plugins-ui"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND = BASE_DIR / "Frontend"

_initialized = False
_init_lock = threading.Lock()


# ------------------------------------------------------------------------- #
# Lazy initialization
# ------------------------------------------------------------------------- #
def ensure_initialized() -> None:
    """Build the gateway, scan plugins and connect enabled MCP servers.

    Safe to call from any endpoint and from the server startup hook.
    """
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        try:
            from gateway.router import get_gateway

            get_gateway()  # resolves providers (no network at construction)
        except Exception as exc:  # noqa: BLE001
            print(f"[Extensions] gateway init failed: {exc}")
        try:
            from plugins.manager import get_plugin_manager

            get_plugin_manager().discover()
        except Exception as exc:  # noqa: BLE001
            print(f"[Extensions] plugin scan failed: {exc}")
        try:
            from mcp.host import get_mcp_host

            get_mcp_host().ensure_started()
        except Exception as exc:  # noqa: BLE001
            print(f"[Extensions] MCP host start failed: {exc}")
        _initialized = True


def init_extensions() -> None:
    """Public startup hook used by backend/api/server.py."""
    ensure_initialized()


# ------------------------------------------------------------------------- #
# Request models
# ------------------------------------------------------------------------- #
class ToggleRequest(BaseModel):
    enabled: bool | None = None


class RunCapabilityRequest(BaseModel):
    capability: str
    params: dict = {}


class MCPCallRequest(BaseModel):
    server: str
    tool: str
    arguments: dict = {}


class ExecRequest(BaseModel):
    command: str
    timeout: int | None = 60


class DevModeRequest(BaseModel):
    message: str


class AutopilotRequest(BaseModel):
    command: str
    cwd: str | None = None
    timeout: int | None = 60
    max_attempts: int | None = 3


# ------------------------------------------------------------------------- #
# LLM gateway
# ------------------------------------------------------------------------- #
@router.get("/llm/status")
def llm_status():
    ensure_initialized()
    try:
        from gateway.router import get_gateway

        gateway = get_gateway()
        return {
            "providers": gateway.get_status(),
            "any_available": gateway.any_available(),
            "best_provider": gateway.best_provider(),
            "fallback_enabled": gateway.fallback_enabled,
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Plugins
# ------------------------------------------------------------------------- #
@router.get("/plugins")
def list_plugins():
    ensure_initialized()
    try:
        from plugins.manager import get_plugin_manager

        manager = get_plugin_manager()
        plugins = manager.list_plugins()
        mcp_caps = manager.describe_mcp_capabilities()
        return {
            "plugins": [p.__dict__ for p in plugins],
            "mcp_capabilities": mcp_caps,
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/plugins/{name}/toggle")
def toggle_plugin(name: str, body: ToggleRequest):
    ensure_initialized()
    try:
        from plugins.manager import get_plugin_manager

        enabled = body.enabled if body.enabled is not None else True
        info = get_plugin_manager().set_enabled(name, enabled)
        if info is None:
            return JSONResponse({"error": f"Unknown plugin: {name}"}, status_code=404)
        return {"ok": True, "plugin": info.__dict__}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/plugins/{name}/reload")
def reload_plugin(name: str):
    ensure_initialized()
    try:
        from plugins.manager import get_plugin_manager

        affected = get_plugin_manager().reload(name)
        if not affected:
            return JSONResponse({"error": f"Unknown plugin: {name}"}, status_code=404)
        info = get_plugin_manager().get(name)
        return {"ok": True, "plugin": info.__dict__ if info else None}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/plugins/reload")
def reload_all_plugins():
    ensure_initialized()
    try:
        from plugins.manager import get_plugin_manager

        affected = get_plugin_manager().reload()
        return {"ok": True, "reloaded": len(affected)}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/plugins/{name}/run")
def run_capability(name: str, body: RunCapabilityRequest):
    ensure_initialized()
    try:
        from plugins.manager import get_plugin_manager

        manager = get_plugin_manager()
        plugin = manager.get(name)
        if plugin is None:
            return JSONResponse({"error": f"Unknown plugin: {name}"}, status_code=404)
        if not plugin.loaded:
            return JSONResponse({"error": f"Plugin {name} is not loaded (disabled?)."}, status_code=400)
        result = manager.execute(body.capability, body.params)
        return {"ok": True, "capability": body.capability, "result": result}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# MCP host
# ------------------------------------------------------------------------- #
@router.get("/mcp/servers")
def mcp_servers():
    ensure_initialized()
    try:
        from mcp.host import get_mcp_host

        return {"servers": get_mcp_host().get_status()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


class MCPAddServerRequest(BaseModel):
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict | None = None
    env: dict | None = None
    description: str = ""
    enabled: bool = True


@router.post("/mcp/servers/add")
def mcp_add_server(body: MCPAddServerRequest):
    """Add an MCP server (stdio or HTTP) from the dashboard and hot-load it."""
    ensure_initialized()
    try:
        from mcp.host import get_mcp_host

        host = get_mcp_host()
        server = host.registry.add_server(
            name=body.name,
            transport=body.transport,
            command=body.command,
            args=body.args,
            url=body.url,
            headers=body.headers,
            env=body.env,
            description=body.description,
            enabled=body.enabled,
        )
        if body.enabled:
            host.connect_server(server.name)
        return {"ok": True, "server": {"name": server.name, "transport": server.transport}}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


class MCPCatalogInstallRequest(BaseModel):
    name: str
    source: str | None = None


@router.get("/mcp/catalog")
def mcp_catalog(source: str | None = None, q: str | None = None):
    """Browse the MCP catalog — curated servers + live community indexes.

    ``?source=punkpeye|wong2|official`` fetches that index live; ``?q=``
    filters names/descriptions. Curated entries are always returned.
    """
    try:
        from mcp.catalog import get_catalog

        return get_catalog(source=source, q=q)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/mcp/catalog/install")
def mcp_catalog_install(body: MCPCatalogInstallRequest):
    """Install a catalog entry (curated or community) into mcp-servers.json.

    For git-kind entries this clones + builds first; for community entries
    it uses the parsed one-command install hint (``npx -y ...`` / ``uvx ...``).
    Installed servers are enabled and hot-loaded immediately.
    """
    ensure_initialized()
    try:
        from mcp.catalog import install_entry
        from mcp.host import get_mcp_host

        result = install_entry(body.name, source=body.source)
        if result.get("ok"):
            host = get_mcp_host()
            host.connect_server(result["server"]["name"])
        return result
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/mcp/servers/{name}/connect")
def mcp_connect(name: str):
    ensure_initialized()
    try:
        from mcp.host import get_mcp_host

        return get_mcp_host().connect_server(name)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/mcp/servers/{name}/disconnect")
def mcp_disconnect(name: str):
    ensure_initialized()
    try:
        from mcp.host import get_mcp_host

        return get_mcp_host().disconnect_server(name)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/mcp/tools")
def mcp_tools():
    ensure_initialized()
    try:
        from mcp.host import get_mcp_host

        tools = get_mcp_host().list_tools()
        return {
            "tools": [
                {
                    "name": f"{t.server}__{t.name}",
                    "server": t.server,
                    "tool": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ]
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/mcp/tools/call")
def mcp_tool_call(body: MCPCallRequest):
    ensure_initialized()
    try:
        from mcp.host import get_mcp_host

        result = get_mcp_host().call_tool(body.server, body.tool, body.arguments)
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Remote SSH dev mode
# ------------------------------------------------------------------------- #
@router.get("/remote/servers")
def remote_servers():
    try:
        from remote_dev.config import load_profiles

        return {"servers": [p.redacted() for p in load_profiles()]}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/remote/servers/{name}/test")
def remote_test(name: str):
    try:
        from remote_dev.connection import SSHManager

        ok, message = SSHManager().test(name)
        return {"ok": ok, "message": message}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/remote/servers/{name}/exec")
def remote_exec(name: str, body: ExecRequest):
    try:
        from remote_dev.connection import SSHManager
        from remote_dev.remote import exec_command

        output = exec_command(SSHManager(), name, body.command, timeout=body.timeout or 60)
        return {"ok": True, "output": output}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/remote/status")
def remote_status():
    try:
        from remote_dev.dev_mode import get_dev_mode_manager

        manager = get_dev_mode_manager()
        return {
            "active": manager.active,
            "sessions": manager.ssh.active(),
            "message": manager.session_status(),
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/devmode/handle")
def devmode_handle(body: DevModeRequest):
    """Route any natural-language command through the dev-mode listener."""
    try:
        from remote_dev.dev_mode import get_dev_mode_manager

        return {"reply": get_dev_mode_manager().handle(body.message)}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Autopilot / Freaky-Fix
# ------------------------------------------------------------------------- #
@router.post("/autopilot/run")
def autopilot_run(body: AutopilotRequest):
    ensure_initialized()
    try:
        from autopilot.freaky_fix import FreakyFixLoop

        loop = FreakyFixLoop(
            max_attempts=body.max_attempts or 3,
            run_timeout=body.timeout or 60,
        )
        report = loop.fix(command=body.command, cwd=body.cwd)
        return {
            "ok": report.success,
            "attempts": report.attempts,
            "error_type": report.error_type,
            "patched_files": [str(p) for p in report.patched_files],
            "messages": report.messages,
            "final_output": report.final_output[:4000],
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# UI routes (Frontend plugins dashboard)
# ------------------------------------------------------------------------- #
@ui_router.get("/plugins")
def plugins_page():
    page = FRONTEND / "plugins.html"
    if page.exists():
        return FileResponse(str(page))
    return JSONResponse({"error": "Frontend/plugins.html not found"}, status_code=404)


@ui_router.get("/plugins/plugins.js")
def plugins_js():
    asset = FRONTEND / "plugins.js"
    return FileResponse(str(asset), media_type="application/javascript") if asset.exists() else JSONResponse({"error": "not found"}, status_code=404)


@ui_router.get("/plugins/plugins.css")
def plugins_css():
    asset = FRONTEND / "plugins.css"
    return FileResponse(str(asset), media_type="text/css") if asset.exists() else JSONResponse({"error": "not found"}, status_code=404)
