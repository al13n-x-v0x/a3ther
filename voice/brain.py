"""
voice/brain.py — turns a spoken transcript into a device action + a spoken reply.

Flow
----
    transcript
        → (1) native intent matcher  (no LLM, sub-ms, works with NO API key)
        → (2) gateway LLM (Gemini preferred) with a device-tool schema
    → executes through the mesh broadcast engine (local host hooks + every
      connected phone/device node run in parallel)
    → returns the reply text the pipeline speaks aloud.

The native matcher runs FIRST so common commands ("flash my phone",
"open notepad", "what time is it") work instantly and even when no API
key is configured. Anything the matcher does not recognise falls back to
the multi-model gateway, which may emit an ``ACTION:`` line that the brain
parses and executes, or just answer conversationally.

Safety: no raw shell from voice. The action vocabulary is the allowlisted
mesh command set (open/close apps, flash, notify, screenshot, phone
navigation, diagnostics). Unknown actions are refused with a spoken error.
"""
from __future__ import annotations

import logging
import re
import time

LOGGER = logging.getLogger("a3ther.voice.brain")

# --------------------------------------------------------------------------- #
# Native intent matchers — (regex, command, params-fn or None)
# Each rule matches a natural phrasing and maps it to a mesh command.
# --------------------------------------------------------------------------- #
_NATIVE_RULES: list[tuple[re.Pattern, str, object]] = []


def _rule(pattern: str, command: str, params: object = None) -> None:
    _NATIVE_RULES.append((re.compile(pattern, re.IGNORECASE), command, params))


def _p(**kwargs) -> dict:
    return kwargs


# -- phone / device control -------------------------------------------------- #


def _pin_match(m: re.Match) -> dict:
    text = m.string
    digits = re.search(r"\b\d{4,8}\b", text)
    return _p(kind="pin", value=digits.group(0) if digits else "")


def _pattern_match(m: re.Match) -> dict:
    text = m.string
    # 1-9 keypad notation, e.g. 1-5-9.
    dots = re.search(r"\b[0-9](?:-[0-9]){1,7}\b", text)
    return _p(kind="pattern", value=dots.group(0) if dots else "")


# Full unlock (wake → remembered PIN/pattern → verify). Matches BEFORE the
# plain "unlock the screen" rule and the generic open-app rule.
_rule(r"\bunlock\b\s+my\s+(phone|device)\b|\bopen\b\s+my\s+(phone|device)\b", "unlock_phone")
# Remember / forget a credential:
_rule(r"\bmy pin is\b|\bpin is\b|\bremember (my )?pin\b|\bmy password is\b|\bpassword is\b|\bremember (my )?password\b", "phone_secret", _pin_match)
_rule(r"\bmy pattern is\b|\bpattern is\b|\bremember (my )?pattern\b", "phone_secret", _pattern_match)
_rule(r"\bforget (my )?(pin|password|pattern)\b", "phone_secret_delete")
_rule(r"\bflash\b.*\b(phone|screen|device)\b", "flash_screen")
_rule(r"\b(flash|blink)\b.*\b(mine|my|screen)\b", "flash_screen")
_rule(r"\bunlock\b.*\b(phone|screen|device)\b", "unlock_interface")
_rule(r"\b(sleep|lock)\b.*\b(phone|device)\b", "system_sleep")
_rule(r"\bwake up\b.*\b(phone|device)\b", "unlock_interface")
_rule(r"\bgo home\b", "go_home")
_rule(r"\b(recent apps|recent)\b", "recent_apps")
_rule(r"\bpress back\b|\bgo back\b", "back")
_rule(r"\b(open|launch)\b\s+(?:the\s+)?app\b", "open_app")
_rule(r"\bclose\b.*\b(app|application|program|window)\b", "close_app")
_rule(r"\bscreenshot\b|\bcapture\b.*\bscreen\b|\btake a picture of the screen\b", "screenshot")
_rule(r"\blook at\b.*\bmy (phone|screen)\b|\bwhat(?:'s| is) on my (phone|screen)\b|\bread my (phone|screen)\b", "__look_at_phone__")
_rule(r"\bdiagnostic", "initialize_diagnostic")
_rule(r"\bnotify\b.*\b(phone|me)\b|\bsend\b.*\bnotification\b", "push_notification")


def _open_app_match(m: re.Match) -> dict:
    text = m.string
    # Pull the app name after "open"/"launch", dropping filler words.
    rest = re.sub(r"^.*?\b(?:open|launch)\b\s+(?:the\s+)?", "", text, flags=re.IGNORECASE)
    rest = rest.strip(" .?!,;")
    for word in ("on my phone", "on the phone", "on my laptop", "on the laptop", "please", "for me"):
        rest = rest.lower().replace(word.lower(), "").strip()
    if not rest:
        rest = "browser"
    return _p(app=rest[:60])


def _open_url_match(m: re.Match) -> dict:
    text = m.string
    m2 = re.search(r"https?://\S+", text)
    if m2:
        return _p(url=m2.group(0)[:200])
    rest = re.sub(r"^.*?\b(?:open|go to)\b\s+", "", text, flags=re.IGNORECASE)
    rest = re.sub(r"\b(on|in)\b.*$", "", rest, flags=re.IGNORECASE).strip(" .?!,")
    return _p(url=(rest if rest else "google.com")[:200])


def _type_match(m: re.Match) -> dict:
    text = m.string
    rest = re.sub(r"^.*?\b(?:type|write)\b\s+", "", text, flags=re.IGNORECASE)
    rest = re.sub(r"\b(in|into|on)\b.*$", "", rest, flags=re.IGNORECASE).strip(" .?!,")
    return _p(text=(rest if rest else text)[:300])


def _notify_match(m: re.Match) -> dict:
    text = m.string
    body = re.sub(r"^.*?\b(?:notify|notification)\b\s+(?:me\s+)?(?:that\s+)?", "", text, flags=re.IGNORECASE)
    body = body.strip(" .?!,") or "Command from A.3.T.H.E.R."
    return _p(title="A.3.T.H.E.R.", body=body[:180])


# "open <app> on my phone" → drive the Android phone via ADB (with
# auto-unlock). Registered before the generic open-app rule so the phrase
# doesn't fall through to the laptop.
_rule(r"\b(?:open|launch)\b\s+(?:the\s+)?\w[\w .\-]*?(?=\s+on\s+my\s+(?:phone|device)\b)", "android_control_open", _open_app_match)

# Domains / explicit URLs go to open_url (before the generic open-app rule).
_rule(r"\b(?:open|go to)\b\s+(?:the\s+)?(?:website|site|url|link)\b", "open_url", _open_url_match)
_rule(r"\b(?:open|go to)\b\s+https?://\S+", "open_url", _open_url_match)
_rule(r"\b(?:open|go to)\b\s+[\w\-]+(?:\.[\w\-]+)+\b", "open_url", _open_url_match)
_rule(r"\bgo to\b\s+[\w\-]+\b", "open_url", _open_url_match)

_rule(r"\b(?:open|launch)\b\s+(?:\w[\w .\-]*?)(?=\s+on\s+my\s+(?:phone|device)|$)", "open_app", _open_app_match)
_rule(r"\btype\b\s+\S.*", "type_text", _type_match)
_rule(r"\b(?:write|type)\b\s+\w[\w\s.\-]{1,100}", "type_text", _type_match)

# -- plain conversational answers (no broadcast needed) ---------------------- #
_rule(r"\bwhat time is it\b|\bcurrent time\b|\btime now\b", "__time__")
_rule(r"\bwhat(?:'s| is) the date\b|\btoday'?s date\b", "__date__")
_rule(r"\bwho are you\b|\bwhat are you\b|\byour name\b", "__who__")
_rule(r"\bhow are you\b|\bhow do you feel\b|\bhow are you doing\b", "__how__")
_rule(r"\bwhat can you do\b|\bhelp\b", "__help__")


# -- MCP tools (any connected server, no LLM needed) ------------------------ #

def _mcp_native_match(m: re.Match) -> dict:
    """Resolve "use the <tool> tool [on <server>]" against connected MCP tools.

    Spoken names are normalised (spaces → underscores) and matched loosely
    (exact, suffix after the server's dot-prefix, substring). Returns
    ``{"tool": "server__tool"}`` for the first hit, or an empty tool with the
    server hint so the action can say which tool exists instead.
    """
    text = m.string
    spoken = ""
    mm = re.search(
        r"\b(?:use|call|run|invoke|trigger)\s+(?:the\s+)?([\w.\- ]+?)\s+(?:tool|command|mcp)",
        text, re.IGNORECASE,
    )
    if mm:
        spoken = mm.group(1).strip()
    if not spoken:
        mm = re.search(r"\bcall\s+([\w.\-]+)", text, re.IGNORECASE)
        spoken = mm.group(1).strip() if mm else ""
    if not spoken:
        return _p(tool="")

    wanted = spoken.lower().replace(" ", "_")
    server_hint = ""
    sm = re.search(r"\b(?:on|using|via)\s+([\w\-]+?)\s*(?:mcp|server)?\s*$", text, re.IGNORECASE)
    if sm:
        server_hint = sm.group(1).lower()

    try:
        from mcp.host import get_mcp_host

        tools = get_mcp_host().list_tools()
    except Exception:  # noqa: BLE001
        tools = []

    candidates: list[str] = []
    for t in tools:
        if server_hint and server_hint not in t.server.lower():
            continue
        name = t.name.lower()
        if name == wanted or name.endswith("." + wanted) or wanted in name or name in wanted:
            candidates.append(f"{t.server}__{t.name}")
    if not candidates:
        return _p(tool="", server=server_hint)
    return _p(tool=candidates[0], server=server_hint)


_rule(r"\b(?:use|call|run|invoke|trigger)\b[^\n]*\b(?:tool|command|mcp)\b", "mcp_tool", _mcp_native_match)
_rule(r"\bcall\s+[\w.\-]+\s*(?:tool|command)?$", "mcp_tool", _mcp_native_match)


# --------------------------------------------------------------------------- #
# Native fast path — returns a spoken reply, or None to fall through to the LLM
# --------------------------------------------------------------------------- #
def _native(text: str) -> str | None:
    for pattern, command, params_fn in _NATIVE_RULES:
        m = pattern.search(text)
        if not m:
            continue
        if command in ("__time__", "__date__", "__who__", "__how__", "__help__"):
            return _conversational(command, text)
        if command == "__look_at_phone__":
            return _look_at_phone(text)
        params = params_fn(m) if callable(params_fn) else (dict(params_fn) if params_fn else {})
        if command == "phone_secret_delete":
            params = {"action": "delete"}
            command = "phone_secret"
        if command == "android_control_open":
            # "open <app> on my phone" → android ADB open with the app name.
            params = {"action": "open", "app": (params or {}).get("app", "")}
            command = "android_control"
        return _run_command(command, params, text)
    return None


def _look_at_phone(text: str) -> str:
    """'look at my phone' → capture the screen and let the AI read it."""
    # Anything after the trigger phrase is the question ("…and tell me the time").
    rest = re.sub(
        r"^.*?\b(look at|what's on|what is on|read)\b.*?\b(phone|screen)\b",
        "", text, flags=re.IGNORECASE,
    ).strip(" .?!,")
    try:
        from .look_at_phone import look_at_phone

        return look_at_phone(rest or None)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("look_at_phone failed")
        return f"Sorry, I couldn't look at your phone: {type(exc).__name__}: {exc}"


def _conversational(kind: str, text: str) -> str:
    if kind == "__time__":
        return time.strftime("The time is %I:%M %p.")
    if kind == "__date__":
        return time.strftime("Today is %A, %B %d, %Y.")
    if kind == "__who__":
        return "I am A3THER — your local, voice-controlled assistant. I can open apps, flash your phone, take screenshots, send notifications, and answer questions."
    if kind == "__how__":
        return "Running smoothly. All systems operational. What can I do for you?"
    return "Say something like: flash my phone, open notepad, take a screenshot, or ask me a question."


# --------------------------------------------------------------------------- #
# Command execution — broadcast to the mesh (host hooks + phone nodes)
# --------------------------------------------------------------------------- #
def _run_command(command: str, params: dict, transcript: str) -> str:
    """Execute one allowlisted command and return a spoken confirmation."""
    if command == "mcp_tool":
        return _mcp_tool_action(params)
    try:
        from sync.broadcaster import get_broadcast_engine

        summary = get_broadcast_engine().broadcast(
            command=command,
            params=params,
            source="voice",
            ack_required=False,
        )
        return _summarize(summary, command, params)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Voice command '%s' failed", command)
        return f"Sorry, I couldn't {command.replace('_', ' ')}. {type(exc).__name__}: {exc}"


def _summarize(summary: dict, command: str, params: dict) -> str:
    """Turn a dispatch summary into a natural one-liner."""
    local_ok = any(r.get("ok") for r in summary.get("local_results", []))
    local_msg = ""
    for r in summary.get("local_results", []):
        if r.get("ok") and r.get("detail"):
            local_msg = str(r["detail"])
            break

    delivered = int(summary.get("delivered", 0))
    friendly = command.replace("_", " ")

    if local_ok:
        if delivered:
            return f"Done — {local_msg}. Also sent to {delivered} device{'s' if delivered != 1 else ''}."
        return f"Done — {local_msg}."
    if delivered:
        return f"Sent to {delivered} device{'s' if delivered != 1 else ''}."
    if summary.get("failed"):
        return f"I couldn't reach any device for {friendly} — nothing is connected. Open the phone page to join the mesh."
    return f"Command {friendly} went out, but no device reported back. Is your phone connected?"


# --------------------------------------------------------------------------- #
# LLM path — the Gemini-Live style brain
# --------------------------------------------------------------------------- #
_TOOL_SCHEMA = """\
You are A3THER, a sharp voice assistant controlling the user's devices (a laptop and their phones).
You may perform AT MOST ONE action by starting your reply with exactly one line:

ACTION: <command> key=value key=value

then, on the following line(s), give a short spoken confirmation (1 sentence).

Available commands:
- open_app app=<name>        open an app (phone or laptop)
- close_app                  close the foreground app
- flash_screen               flash the phone's screen
- push_notification title=<t> body=<message>
- type_text text=<words>     type text into the focused window
- screenshot                 capture the laptop screen
- unlock_interface           wake/unlock the phone
- system_sleep               sleep/lock the phone
- go_home | back | recent_apps    phone navigation
- open_url url=<https://...> open a link on the phone
- initialize_diagnostic      run device diagnostics
- mcp_tool tool=<server__tool> arg=value    call a connected MCP tool
  (e.g. tool=filesystem__read_file path=C:/notes.txt). A live list of
  connectable tools is appended below when any MCP server is connected.

Rules:
- If the user asks a question or makes small talk, reply conversationally with NO ACTION line.
- Never invent commands. If they ask for something you cannot do, say so in one sentence.
- Keep the spoken reply to 1-2 short sentences. No markdown.
- When a value contains spaces, write it with underscores: text=hello_world
"""


def _build_tool_schema() -> str:
    """The LLM schema, extended with whatever MCP tools are currently connected.

    Lets the voice brain drive any live MCP server (filesystem, mobile-mcp,
    commands, …) with ``mcp_tool tool=<server__tool> arg=value`` — the same
    flattened ``server__tool`` addressing the Extensions dashboard uses.
    """
    schema = _TOOL_SCHEMA
    try:
        from mcp.host import get_mcp_host

        tools = get_mcp_host().list_tools()
    except Exception:  # noqa: BLE001
        tools = []
    if not tools:
        return schema
    lines = [
        "",
        "Connected MCP tools — to use one, start your reply with:",
        "ACTION: mcp_tool tool=<server__tool> arg1=value arg2=value",
        "Available now:",
    ]
    seen: set[str] = set()
    for t in tools:
        name = f"{t.server}__{t.name}"
        if name in seen:
            continue
        seen.add(name)
        args = []
        props = (t.input_schema or {}).get("properties") or {}
        for key in list(props)[:4]:
            req = (t.input_schema or {}).get("required") or []
            args.append(f"{key}={key if key in req else '?'}")
        lines.append(f"- {name}  (" + " ".join(args) + ")")
    return schema + "\n".join(lines)


_ACTION_RE = re.compile(r"^\s*ACTION:\s*(\S+)\s*(.*)$", re.IGNORECASE | re.MULTILINE)
_KEYVAL_RE = re.compile(r"(\w+)=(\S+)")

_PHONE_COMMANDS = {
    "open_app", "close_app", "flash_screen", "push_notification", "type_text",
    "screenshot", "unlock_interface", "system_sleep", "go_home", "back",
    "recent_apps", "open_url", "initialize_diagnostic", "android_control",
    "unlock_phone", "phone_secret", "phone_secret_delete", "mcp_tool",
}


def _mcp_tool_action(params: dict) -> str:
    """Call a connected MCP tool by its flattened ``server__tool`` name.

    Example: ``mcp_tool tool=filesystem__read_file path=C:/notes.txt``
    Any extra ``key=value`` pairs become the tool's JSON arguments.
    Bare names are resolved too: ``tool=create_entities`` finds the single
    connected tool that matches, and a bare server name gets a precise hint
    listing that server's exact tool names.
    """
    tool_name = (params.get("tool") or "").strip()
    if not tool_name:
        return "MCP tool call missing a tool name — say: use the filesystem read file tool."
    args = {k: v for k, v in params.items() if k not in ("tool", "server")}
    try:
        from mcp.host import get_mcp_host

        host = get_mcp_host()
        if "__" not in tool_name:
            # Resolve bare names against the live tool list.
            tools = host.list_tools()
            if any(t.server == tool_name for t in tools):
                names = [f"{t.server}__{t.name}" for t in tools if t.server == tool_name]
                return (f"I need the full tool name for {tool_name} — try: "
                        + ", ".join(names[:4]))
            matches = [f"{t.server}__{t.name}" for t in tools
                       if t.name == tool_name or tool_name in t.name]
            if len(matches) == 1:
                tool_name = matches[0]
            elif len(matches) > 1:
                return "Several MCP tools match — which one? " + ", ".join(matches[:4])
            elif not tools:
                return ("No MCP servers are connected right now. Open Extensions → "
                        "MCP Servers and connect one, then try again.")
        result = host.call_llm_tool(tool_name, args)
        snippet = str(result)[:220]
        return f"Done — {tool_name} returned: {snippet}"
    except Exception as exc:  # noqa: BLE001
        # Honest, actionable error: name the live tools when resolution failed.
        try:
            from mcp.host import get_mcp_host

            names = [f"{t.server}__{t.name}" for t in get_mcp_host().list_tools()][:6]
            hint = f" Connected tools: {', '.join(names)}." if names else " No MCP servers connected."
        except Exception:  # noqa: BLE001
            hint = ""
        return f"MCP tool {tool_name} failed: {exc}.{hint}"


def _parse_action(text: str) -> tuple[str, dict] | None:
    m = _ACTION_RE.search(text)
    if not m:
        return None
    command = m.group(1).strip().lower()
    if command not in _PHONE_COMMANDS:
        return None
    params: dict = {}
    for key, value in _KEYVAL_RE.findall(m.group(2)):
        # tool/server names keep underscores (server__tool separator); every
        # other value keeps the spoken-word convention (text=hello_world →
        # "hello world").
        if key in ("tool", "server"):
            params.setdefault(key, value)
        else:
            params.setdefault(key, value.replace("_", " "))
    return command, params


def _llm_reply(text: str, system: str | None) -> str:
    from gateway.router import AllProvidersFailed, get_gateway

    gateway = get_gateway()
    if not gateway.any_available():
        return "I have no AI provider configured. Add a Gemini or OpenAI key in Settings and I can answer anything."

    provider = gateway.best_provider() or ""
    raw = gateway.complete_text(
        text,
        system=system or _build_tool_schema(),
        preference=provider,
        max_tokens=600,  # tool calls with JSON args need headroom over chat
        timeout=40,
    )
    raw = (raw or "").strip()
    if not raw:
        return "I didn't get a response from the AI. Try again?"
    action = _parse_action(raw)
    if action:
        command, params = action
        return _run_command(command, params, text)
    # Plain conversational reply — strip any stray ACTION-like junk.
    return re.sub(r"^\s*ACTION:.*$", "", raw, flags=re.MULTILINE).strip() or raw


# --------------------------------------------------------------------------- #
# Public entry point (wired into the voice pipeline)
# --------------------------------------------------------------------------- #
def GenerateResponse(text: str, system: str | None = None) -> str:
    """Process one spoken utterance → spoken reply (may execute device actions).

    Native matcher first (fast, no API key needed), then the LLM brain.
    Never raises — every failure degrades to an honest spoken message.
    """
    text = (text or "").strip()
    if not text:
        return "I didn't catch that — say it again?"
    try:
        native = _native(text)
        if native is not None:
            return native
        return _llm_reply(text, system)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Brain failed on: %r", text)
        return f"Sorry, I hit a problem: {type(exc).__name__}: {exc}"
