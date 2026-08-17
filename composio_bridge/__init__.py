"""
composio_bridge — plug A3THER into Composio's 250+ app integrations.

Composio (composio.dev) is the "Zapier for AI agents": one API key gives the
agent access to Gmail, Slack, GitHub, Notion, Spotify, Discord, WhatsApp,
Google Sheets and hundreds more apps with OAuth-managed connections.

Two paths into Composio, both env-first and both honest when not configured:

1. **REST API** — with ``COMPOSIO_API_KEY`` set (env or
   ``config/api_keys.json`` → ``composio_api_key``), A3THER can list the
   available tools and execute actions directly against
   ``https://backend.composio.dev/api/v1``.

2. **MCP apps** — every Composio app also speaks MCP at
   ``https://mcp.composio.dev/{app}``. ``add_mcp_app()`` drops a ready-made
   HTTP entry into ``config/mcp-servers.json`` (with the ``x-api-key`` header)
   and hot-loads it through A3THER's existing MCP host — so the voice brain
   and the Extensions dashboard can call the app's tools like any other MCP
   server.

No key → ``status()`` says exactly what to do. Bad key → the HTTP error
surfaces verbatim. Nothing is faked.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

LOGGER = logging.getLogger("a3ther.composio")

_BASE = "https://backend.composio.dev/api/v1"
_MCP_BASE = "https://mcp.composio.dev"

#: Popular Composio apps exposed as one-click MCP additions.
POPULAR_APPS = [
    ("gmail", "Gmail — read/send mail"),
    ("github", "GitHub — repos, issues, PRs"),
    ("slack", "Slack — channels, messages"),
    ("googlecalendar", "Google Calendar — events"),
    ("googlesheets", "Google Sheets — spreadsheets"),
    ("notion", "Notion — pages, databases"),
    ("whatsapp", "WhatsApp — chats"),
    ("spotify", "Spotify — playback, playlists"),
    ("discord", "Discord — channels, webhooks"),
    ("telegram", "Telegram — messages"),
    ("youtube", "YouTube — uploads, comments (Composio route)"),
    ("twilio", "Twilio — SMS"),
    ("reddit", "Reddit — posts, comments"),
    ("linear", "Linear — issues, projects"),
    ("asana", "Asana — tasks, projects"),
    ("hubspot", "HubSpot — CRM"),
    ("figma", "Figma — files, comments"),
    ("medium", "Medium — stories"),
]


def _api_key() -> str:
    """Env-first: COMPOSIO_API_KEY, then config/api_keys.json → composio_api_key."""
    key = (os.environ.get("COMPOSIO_API_KEY") or "").strip()
    if key:
        return key
    try:
        from config import get_config

        return str(get_config().get("composio_api_key") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _request(method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    """JSON HTTP helper against the Composio REST API. Returns (status, data)."""
    url = f"{_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    key = _api_key()
    if key:
        req.add_header("x-api-key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            data = {"detail": raw[:400]}
        return exc.code, data
    except Exception as exc:  # noqa: BLE001
        return 0, {"detail": f"{type(exc).__name__}: {exc}"}


def status() -> dict:
    """Key present? Reachable? How many tools does the account see?"""
    key = _api_key()
    if not key:
        return {
            "ok": False,
            "configured": False,
            "reachable": False,
            "error": (
                "Composio isn't configured — set the COMPOSIO_API_KEY env var "
                "(or add composio_api_key to config/api_keys.json). Get a free "
                "key at composio.dev, then refresh."
            ),
            "setup_steps": "1) Sign up at https://composio.dev 2) copy your API key "
            "3) set COMPOSIO_API_KEY (env) or config/api_keys.json → composio_api_key.",
        }
    code, data = _request("GET", "/tools?page_size=1")
    if code in (200, 201):
        return {
            "ok": True,
            "configured": True,
            "reachable": True,
            "tools_total": (data.get("total") or len(data.get("items") or [])),
            "note": "Composio connected — add apps with the buttons below, or call actions via the REST API.",
        }
    return {
        "ok": False,
        "configured": True,
        "reachable": False,
        "error": f"Composio API rejected the key (HTTP {code}): {data.get('detail') or data}",
        "setup_steps": "Double-check the key at https://composio.dev/dashboard.",
    }


def list_tools(limit: int = 25) -> dict:
    """List the tools available to the configured account."""
    code, data = _request("GET", f"/tools?page_size={min(max(int(limit), 1), 50)}")
    if code not in (200, 201):
        return {"ok": False, "error": f"HTTP {code}: {data.get('detail') or data}"}
    items = data.get("items") or []
    tools = [
        {
            "name": t.get("name") or t.get("actionName") or "",
            "app": (t.get("appName") or t.get("app") or "").lower(),
            "description": (t.get("description") or t.get("displayName") or "")[:140],
        }
        for t in items
    ]
    return {"ok": True, "tools": tools, "count": len(tools), "total": data.get("total")}


def execute(action: str, params: dict | None = None) -> dict:
    """Execute a Composio action (needs a connected account for the app).

    ``action`` is the tool's action name, e.g. ``GMAIL_SEND_EMAIL``. OAuth apps
    require the account to be connected on composio.dev first — if it isn't,
    Composio returns a connection error which we surface honestly with the
    connect URL.
    """
    action = (action or "").strip()
    if not action:
        return {"ok": False, "error": "action name is required (e.g. GMAIL_SEND_EMAIL)"}
    code, data = _request(
        "POST", "/actions/execute", {"action": action, "params": dict(params or {})}
    )
    if code not in (200, 201):
        detail = data.get("detail") or data
        err = detail if isinstance(detail, str) else json.dumps(detail)[:300]
        hint = (
            " Connect the app first: https://composio.dev/dashboard "
            "(OAuth apps need a connected account)."
        ) if "connect" in err.lower() or "account" in err.lower() else ""
        return {"ok": False, "error": f"HTTP {code}: {err}{hint}"}
    return {"ok": True, "result": data.get("response_data") or data}


def mcp_servers() -> list[dict]:
    """The MCP entry shapes for the popular apps (ready for mcp-servers.json)."""
    key = _api_key()
    return [
        {
            "name": f"composio-{app}",
            "transport": "http",
            "url": f"{_MCP_BASE}/{app}",
            "headers": {"x-api-key": key} if key else {"x-api-key": "{{COMPOSIO_API_KEY}}"},
            "enabled": bool(key),
            "description": desc,
        }
        for app, desc in POPULAR_APPS
    ]


def add_mcp_app(app: str) -> dict:
    """Add one Composio app as an MCP server (hot-loaded into the registry)."""
    entry = next((e for e in POPULAR_APPS if e[0] == app), None)
    if entry is None:
        return {"ok": False, "error": f"unknown composio app '{app}' — pick from: {', '.join(a for a, _ in POPULAR_APPS)}"}
    key = _api_key()
    if not key:
        return {"ok": False, "error": status()["setup_steps"]}
    try:
        from mcp.registry import MCPRegistry

        server = MCPRegistry().add_server(
            name=f"composio-{app}",
            transport="http",
            url=f"{_MCP_BASE}/{app}",
            headers={"x-api-key": key},
            description=entry[1],
            enabled=True,
        )
        return {"ok": True, "server": {"name": server.name, "url": server.url}, "note": f"{app} added — call its tools like any MCP server (Extensions tab / voice)."}
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to add composio app %s", app)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
