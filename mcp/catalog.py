"""
catalog.py — one-click MCP server installer + community index browser.

Two halves:

1. **Curated catalog** — the servers A3THER ships/recommends (including the
   repos requested for this build), each with the *exact* run command:
   ``npx -y <pkg>`` (npm), ``uv run <pkg>`` (Python), ``go install``,
   ``git clone + build``, or a desktop builder app. Installing one writes a
   ready-to-connect entry into ``config/mcp-servers.json`` through the same
   registry the dashboard uses.

2. **Community index browser** — the three indexes the user pointed at:
   - punkpeye/awesome-mcp-servers   (16k+ servers, markdown list)
   - wong2/awesome-mcp-servers      (the original awesome list)
   - modelcontextprotocol/registry  (the official registry, structured JSON)

   Each is fetched live, normalized into the same shape, cached for a short
   TTL, and browsable from the Extensions dashboard. Entries that carry a
   one-command install hint (``npx -y ...`` / ``uvx ...``) can be installed
   with one click; the rest show their repo link.

The install path reuses :class:`mcp.registry.MCPRegistry.add_server`, so
catalog-installed servers behave exactly like hand-written config entries
(hot-reload, enabled toggles, connect/disconnect, tool calls).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

try:
    from config.paths import data_path as _data_path
except Exception:  # noqa: BLE001
    _data_path = None

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
_CACHE_TTL = 15 * 60          # seconds — community lists are big, cache them
_INSTALL_SUBDIR = "mcp/installed"   # git-cloned servers live under the data dir
_CACHE_FILE = "config/mcp_catalog_cache.json"

#: The three index sources (the awesome lists + the official registry).
INDEX_SOURCES: list[dict[str, str]] = [
    {
        "name": "punkpeye",
        "display": "Awesome MCP Servers (punkpeye)",
        "source": "https://github.com/punkpeye/awesome-mcp-servers",
        "url": "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
        "format": "markdown",
        "note": "The big curated list — 16,000+ community MCP servers. Install hints are parsed when present.",
    },
    {
        "name": "wong2",
        "display": "Awesome MCP Servers (wong2)",
        "source": "https://github.com/wong2/awesome-mcp-servers",
        "url": "https://raw.githubusercontent.com/wong2/awesome-mcp-servers/master/README.md",
        "format": "markdown",
        "note": "The original awesome list — reference servers and starter examples.",
    },
    {
        "name": "official",
        "display": "MCP Official Registry",
        "source": "https://github.com/modelcontextprotocol/registry",
        "url": "https://raw.githubusercontent.com/modelcontextprotocol/registry/main/data/seed.json",
        "format": "json",
        "note": "The official modelcontextprotocol registry seed — canonical server list.",
    },
]

#: Curated entries for the servers requested for this build.
CURATED: list[dict[str, Any]] = [
    # ------------------------------------------------------------- npx ----- #
    {
        "name": "native-devtools",
        "display": "Native DevTools",
        "description": "Computer use for native desktop apps, Chrome/Electron (CDP) and Android — screenshots, OCR, click, type, find text.",
        "source": "https://github.com/sh3ll3x3c/native-devtools-mcp",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "native-devtools-mcp"],
        "env": {},
        "requirements": "Node.js 18+",
        "category": "Automation",
    },
    {
        "name": "mcp-server-commands",
        "display": "Commands",
        "description": "Run shell commands and processes on the host machine through MCP.",
        "source": "https://github.com/g0t4/mcp-server-commands",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "mcp-server-commands"],
        "env": {},
        "requirements": "Node.js 18+",
        "category": "DevOps",
    },
    {
        "name": "gsap-master",
        "display": "GSAP Master",
        "description": "GSAP animation expert — create, debug and optimize scroll / text / SVG animations with 100% plugin coverage.",
        "source": "https://github.com/bruzethegreat/gsap-master-mcp-server",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "bruzethegreat-gsap-master-mcp-server@latest"],
        "env": {},
        "requirements": "Node.js 18+",
        "category": "Web",
    },
    {
        "name": "mcp-video-analyzer",
        "display": "Video Analyzer",
        "description": "Turn any video (YouTube / Instagram / TikTok / local files) into transcripts, key frames, OCR and metadata for AI agents.",
        "source": "https://github.com/guimatheus92/mcp-video-analyzer",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "mcp-video-analyzer@latest"],
        "env": {},
        "requirements": "Node.js 18+; yt-dlp for platform URLs (already bundled in A3THER)",
        "category": "Video",
    },
    {
        "name": "vibeframe",
        "display": "VibeFrame",
        "description": "Frontier AI video generation (Seedance, Runway, Veo, Kling) on your own keys behind a hard cost cap. CLI + MCP.",
        "source": "https://github.com/vericontext/vibeframe",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "@vibeframe/mcp-server"],
        "env": {},
        "requirements": "Node.js 20+, FFmpeg, Chrome/Chromium; provider keys (OPENAI_API_KEY / FAL_API_KEY / GOOGLE_API_KEY / RUNWAY_API_SECRET)",
        "category": "Video",
    },
    # -------------------------------------------------------------- uv ------ #
    {
        "name": "video-editor",
        "display": "Video Editor (FFmpeg)",
        "description": "Natural-language FFmpeg editing — trim, merge, convert, speed, audio, subtitles — with progress tracking.",
        "source": "https://github.com/kush36agrawal/video_editor_mcp",
        "kind": "uv",
        "command": "uv",
        "args": ["run", "video-editor"],
        "env": {},
        "requirements": "uv + Python 3.9+; FFmpeg on PATH (A3THER bundles one — add it to PATH if needed)",
        "category": "Video",
    },
    # -------------------------------------------------------------- go ------ #
    {
        "name": "aio-mcp",
        "display": "AIO-MCP (All-in-one)",
        "description": "AI search, RAG and GitLab / Jira / Confluence / YouTube / Google Calendar integrations in one Go server.",
        "source": "https://github.com/athapong/aio-mcp",
        "kind": "go",
        "command": "aio-mcp",
        "args": [],
        "env": {},
        "preinstall": "go install github.com/athapong/aio-mcp@latest",
        "requirements": "Go 1.23+; service keys via env (GITLAB_TOKEN, ATLASSIAN_*, BRAVE_API_KEY, GOOGLE_AI_API_KEY, …)",
        "category": "DevOps",
    },
    # -------------------------------------------------------------- git ----- #
    {
        "name": "scroll-analyzer",
        "display": "Scroll Animation Analyzer",
        "description": "Analyze any webpage's scroll animations (GSAP, ScrollTrigger, Lenis, CSS) with Playwright — AST extraction, video recording, replicable code.",
        "source": "https://github.com/rob-kingsbury/mcp-scroll-analyzer",
        "kind": "git",
        "repo": "https://github.com/rob-kingsbury/mcp-scroll-analyzer",
        "build_steps": [
            "npm install",
            "npx playwright install chromium",
            "npm run build",
        ],
        "run": ["node", "dist/index.js"],
        "requirements": "Node.js 18+; first install downloads Playwright Chromium (~130 MB)",
        "category": "Web",
    },
    # ------------------------------------------------------------- app ------ #
    {
        "name": "gui-mcp",
        "display": "GUI-MCP Builder",
        "description": "Blueprint-style visual node editor for building FastMCP servers without code — design tools, export Python, run them.",
        "source": "https://github.com/PhialsBasement/GUI-MCP",
        "kind": "app",
        "requirements": "Desktop app (PySide6) — clone the repo, pip install -r requirements.txt, run python main.py",
        "category": "Builder",
    },
    # ------------------------------------------------------- official ----- #
    {
        "name": "filesystem",
        "display": "Filesystem (official)",
        "description": "The official MCP filesystem server — secure file operations (read/write/search) restricted to the root folders you allow.",
        "source": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
        "env": {},
        "requirements": "Node.js 18+; edit the trailing '.' arg in mcp-servers.json to the folders you want it to access",
        "category": "Files",
    },
    {
        "name": "official-memory",
        "display": "Memory (official)",
        "description": "The official knowledge-graph memory server — persistent entities/relations that survive sessions.",
        "source": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "env": {},
        "requirements": "Node.js 18+",
        "category": "Memory",
    },
    {
        "name": "official-everything",
        "display": "Everything (official)",
        "description": "The official reference/test server — every tool shape, resource and prompt for validating MCP clients.",
        "source": "https://github.com/modelcontextprotocol/servers/tree/main/src/everything",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
        "env": {},
        "requirements": "Node.js 18+",
        "category": "Testing",
    },
    {
        "name": "official-sequential-thinking",
        "display": "Sequential Thinking (official)",
        "description": "Step-by-step structured reasoning — breaks complex problems into verifiable stages.",
        "source": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "env": {},
        "requirements": "Node.js 18+",
        "category": "Reasoning",
    },
    {
        "name": "official-servers",
        "display": "Official MCP Servers (collection)",
        "description": "The official modelcontextprotocol/servers repo — the reference implementations. Filesystem, memory, everything and sequential-thinking are listed above; git & fetch are archived.",
        "source": "https://github.com/modelcontextprotocol/servers",
        "kind": "collection",
        "requirements": "Collection — pick the individual servers listed above to install.",
        "category": "Reference",
    },
    # ------------------------------------------------------------ phone ---- #
    {
        "name": "mobile-mcp",
        "display": "Mobile Next (mobile-mcp)",
        "description": "Cross-platform mobile automation — drive iOS/Android real devices, emulators & simulators with taps, swipes, app control, screenshots and UI-tree access.",
        "source": "https://github.com/mobile-next/mobile-mcp",
        "kind": "npx",
        "command": "npx",
        "args": ["-y", "@mobilenext/mobile-mcp@latest"],
        "env": {},
        "requirements": "Node.js 20+; Android platform-tools (adb) for Android — A3THER bundles adb with scrcpy",
        "category": "Phone",
    },
    {
        "name": "scrcpy-vision",
        "display": "scrcpy Vision",
        "description": "Real-time Android vision + control — scrcpy H.264 streaming (5-10ms input), uiautomator element detection, WiFi ADB, clipboard/notifications.",
        "source": "https://github.com/invidtiv/mcp-scrcpy-vision",
        "kind": "git",
        "repo": "https://github.com/invidtiv/mcp-scrcpy-vision",
        "build_steps": ["npm install", "npm run build"],
        "run": ["node", "<repo>/dist/index.js"],
        "requirements": "Node.js 18+; set SCRCPY_SERVER_PATH to your scrcpy-server (A3THER auto-installs scrcpy to ~/Videos/A3THER/scrcpy)",
        "category": "Phone",
    },
    {
        "name": "scrcpy",
        "display": "scrcpy (already integrated)",
        "description": "Genymobile's scrcpy — screen mirroring + control for Android. A3THER already auto-installs it for Phone Link casting (Settings → PHONE LINK → Start Cast).",
        "source": "https://github.com/Genymobile/scrcpy",
        "kind": "note",
        "requirements": "Already bundled — no install needed. It ships its own adb.exe that the cast engine resolves.",
        "category": "Phone",
    },
    # ------------------------------------------------------------- voice ---- #
    {
        "name": "speech-mcp",
        "display": "Speech MCP (STT+TTS)",
        "description": "Voice interface — faster-whisper speech-to-text, 54+ Kokoro TTS voices, multi-speaker narration and PyQt audio visualization.",
        "source": "https://github.com/kvadratni/speech-mcp",
        "kind": "uv",
        "command": "uvx",
        "args": ["-p", "3.10.14", "speech-mcp@latest"],
        "env": {},
        "requirements": "uv + Python 3.10+; Kokoro voice models (~500KB each) download on first use; PyAudio wheel bundles PortAudio on Windows",
        "category": "Voice",
    },
    # ---------------------------------------------------------- hardware ---- #
    {
        "name": "ble-mcp-server",
        "display": "BLE MCP Server",
        "description": "Talk to real Bluetooth Low Energy hardware — scan, connect, read/write characteristics, subscribe to notifications (bleak).",
        "source": "https://github.com/es617/ble-mcp-server",
        "kind": "uv",
        "command": "uvx",
        "args": ["ble-mcp-server"],
        "env": {},
        "requirements": "uv + Python 3.10+; Windows BLE via bleak (already used by A3THER's device discovery)",
        "category": "Hardware",
    },
    # ------------------------------------------------------------- video ---- #
    {
        "name": "ffmpeg-mcp",
        "display": "FFmpeg-MCP (macOS only)",
        "description": "Conversational FFmpeg — local video search, trim, stitch, overlay, scale, frame extraction.",
        "source": "https://github.com/video-creator/ffmpeg-mcp",
        "kind": "git",
        "repo": "https://github.com/video-creator/ffmpeg-mcp",
        "build_steps": ["uv sync"],
        "run": ["uv", "--directory", "<repo>", "run", "ffmpeg-mcp"],
        "requirements": "⚠ macOS only (per the author); uv + FFmpeg on PATH — A3THER's bundled ffmpeg covers Windows edits instead",
        "category": "Video",
    },
    # ------------------------------------------------------------ cli ------ #
    {
        "name": "opentabs",
        "display": "OpenTabs",
        "description": "Your AI calls real web APIs through your browser session (Slack, Discord, GitHub, Jira, Notion, Figma…) — 100+ plugins, ~2,000 tools, no API keys.",
        "source": "https://github.com/opentabs-dev/opentabs",
        "kind": "cli",
        "requirements": "npm install -g @opentabs-dev/cli && opentabs start — then load the Chrome extension from ~/.opentabs/extension (Node 22+ + Chrome)",
        "category": "Web",
    },
    {
        "name": "omniroute",
        "display": "OmniRoute (AI gateway)",
        "description": "Free MIT AI gateway — 290+ providers / 500+ models behind one endpoint with quota-aware fallback; ships its own MCP server (~95 tools).",
        "source": "https://github.com/diegosouzapw/OmniRoute",
        "kind": "cli",
        "requirements": "npm install -g omniroute && omniroute — a daemon + dashboard on localhost:20128; its MCP server runs from the CLI",
        "category": "Gateway",
    },
]

# --------------------------------------------------------------------------- #
# Community index fetching + parsing
# --------------------------------------------------------------------------- #
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cache_path() -> Path:
    if _data_path is not None:
        try:
            return _data_path(_CACHE_FILE)
        except Exception:  # noqa: BLE001
            pass
    return Path(__file__).resolve().parent.parent / "config" / "mcp_catalog_cache.json"


def _cache_get(key: str) -> Any | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]
    # Disk cache fallback (survives restarts within the TTL window).
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        item = data.get(key)
        if item and time.time() - item.get("ts", 0) < _CACHE_TTL:
            with _cache_lock:
                _cache[key] = (item["ts"], item["value"])
            return item["value"]
    except Exception:  # noqa: BLE001
        pass
    return None


def _cache_set(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), value)
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = {}
        data[key] = {"ts": time.time(), "value": value}
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _http_get(url: str, timeout: float = 25.0) -> str:
    """Fetch a URL as text with a sane UA and timeout."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "A3THER/1.0 (MCP catalog browser)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


_RE_LINK = re.compile(r"-\s*\*?\*?\[([^\]]+)\]\((https?://[^\s)]+)\)")
_RE_INSTALL = re.compile(
    r"`([^`]*(?:npx|uvx|uv |pipx|python|go run)[^`]*)`", re.IGNORECASE
)


def _entry_from_line(line: str, source: str) -> dict | None:
    """Normalize one markdown bullet/table row into a catalog entry."""
    match = _RE_LINK.search(line)
    if not match:
        return None
    label, url = match.group(1), match.group(2)

    name = label.strip()
    # Prefer the GitHub owner/repo as the stable id.
    repo_match = re.search(r"github\.com/([^/\s]+/[^/\s#)]+)", url)
    if repo_match:
        name = repo_match.group(1).rstrip("/")

    # Description: text after the " - " separator (bullet lists carry it).
    description = ""
    if " - " in line:
        tail = line.split(" - ", 1)[1]
        description = tail.strip()

    hint = ""
    install = _RE_INSTALL.search(line)
    if install:
        hint = install.group(1).strip()
        # The hint is surfaced separately — don't repeat it in the text.
        description = description.replace(f"Install: `{hint}`", "").strip()
        description = description.replace(f"`{hint}`", "").strip()

    return {
        "name": name,
        "display": label,
        "description": description[:220],
        "source": url,
        "kind": "community",
        "install_hint": hint or None,
        "category": "Community",
        "index": source,
    }


def _parse_markdown(text: str, source: str) -> list[dict]:
    entries: list[dict] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("-") and not line.startswith("|"):
            continue
        entry = _entry_from_line(line, source)
        if not entry or not entry["name"]:
            continue
        if entry["name"] in seen:
            continue
        seen.add(entry["name"])
        entries.append(entry)
    return entries


def _parse_official(text: str) -> list[dict]:
    entries: list[dict] = []
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return entries
    for item in data if isinstance(data, list) else []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        # io.github.owner/repo → owner/repo
        if "/" in name and "." in name.split("/", 1)[0]:
            name = name.split("/", 1)[1]
        repo_url = ""
        repo = item.get("repository") or {}
        repo_url = str(repo.get("url") or item.get("url") or "")
        entries.append(
            {
                "name": name,
                "display": name,
                "description": str(item.get("description") or "")[:220],
                "source": repo_url or f"https://github.com/{name}",
                "kind": "community",
                "install_hint": None,
                "category": "Community",
                "index": "official",
            }
        )
    return entries


_FETCHERS = {
    "punkpeye": lambda text: _parse_markdown(text, "punkpeye"),
    "wong2": lambda text: _parse_markdown(text, "wong2"),
    "official": _parse_official,
}


def fetch_index(source: str) -> list[dict]:
    """Fetch + parse one community index, with memory/disk caching."""
    meta = next((s for s in INDEX_SOURCES if s["name"] == source), None)
    if not meta:
        raise ValueError(f"unknown index source: {source}")
    cached = _cache_get(f"index:{source}")
    if cached is not None:
        return cached
    text = _http_get(meta["url"])
    parser = _FETCHERS.get(source)
    entries = parser(text) if parser else []
    _cache_set(f"index:{source}", entries)
    return entries


# --------------------------------------------------------------------------- #
# Install helpers
# --------------------------------------------------------------------------- #
def _installed_root() -> Path:
    if _data_path is not None:
        try:
            return _data_path(_INSTALL_SUBDIR)
        except Exception:  # noqa: BLE001
            pass
    return Path(__file__).resolve().parent.parent / _INSTALL_SUBDIR


def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 900.0) -> str:
    """Run a subprocess; returns combined output (best-effort on failure)."""
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "errors": "replace",
        "cwd": str(cwd) if cwd else None,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(cmd, timeout=timeout, **kwargs)  # noqa: S603
    return (proc.stdout or "")[-4000:]


def _command_from_hint(hint: str) -> tuple[str, list[str]] | None:
    """Turn an install hint like ``npx -y foo`` into (command, args)."""
    parts = hint.strip().split()
    if not parts:
        return None
    first = parts[0].lower().replace(".cmd", "").replace(".exe", "")
    rest = parts[1:]
    if first in ("npx", "npx.cmd"):
        if "-y" not in rest and not any(a.startswith("-") for a in rest):
            rest = ["-y"] + rest
        return "npx", rest
    if first in ("uvx", "uvx.cmd"):
        return "uvx", rest
    if first == "uv":
        return "uv", rest
    if first in ("pipx", "pipx.exe"):
        return "pipx", rest
    if first in ("python", "python3"):
        return first, rest
    return None


def _prepare_git(entry: dict) -> tuple[str, list[str]]:
    """Clone + build a git-kind server. Returns the resolved (command, args).

    ``run`` tokens support two substitutions:
    - ``node``        → resolved Node executable (PATH, PATHEXT-aware)
    - ``<repo>``      → the cloned repo directory (e.g. ``<repo>/dist/index.js``)
    Other tokens pass through unchanged (flags, package names, ``uv`` …).
    """
    root = _installed_root()
    root.mkdir(parents=True, exist_ok=True)
    repo_dir = root / entry["name"]
    if not (repo_dir / ".git").exists():
        _run(["git", "clone", "--depth", "1", entry["repo"], str(repo_dir)])
    for step in entry.get("build_steps") or []:
        # npm/npx/playwright/uv steps are plain shell commands.
        subprocess.run(step, shell=True, cwd=str(repo_dir), timeout=1200)  # noqa: S602
    run = entry.get("run") or ["node", "<repo>/dist/index.js"]
    resolved = []
    for part in run:
        if part == "node":
            resolved.append(shutil.which("node") or "node")
        else:
            resolved.append(part.replace("<repo>", str(repo_dir)))
    return resolved[0], resolved[1:]


def _prepare_go(entry: dict) -> None:
    """Ensure the go binary is installed (best-effort when `go` is present)."""
    if shutil.which(entry["command"]):
        return
    if shutil.which("go") and entry.get("preinstall"):
        try:
            _run(["go", "install", "github.com/athapong/aio-mcp@latest"], timeout=1200)
        except Exception:  # noqa: BLE001
            pass


def installed_names() -> list[str]:
    """Names already present in the live registry (for UI badges)."""
    try:
        from mcp.registry import MCPRegistry

        return [s.name for s in MCPRegistry().discover(force=False)]
    except Exception:  # noqa: BLE001
        return []


def install_entry(name: str, source: str | None = None) -> dict:
    """Install a catalog entry into config/mcp-servers.json and hot-load it.

    Returns ``{"ok": True, "server": {...}}`` or ``{"ok": False, "error": ...}``.
    """
    from mcp.registry import MCPRegistry

    entry = next((e for e in CURATED if e["name"] == name), None)

    if entry is None and source:
        for item in fetch_index(source):
            if item["name"] == name:
                entry = item
                break

    if entry is None:
        return {"ok": False, "error": f"'{name}' is not in the catalog"}

    kind = entry.get("kind", "community")
    command = entry.get("command")
    args = list(entry.get("args") or [])
    env = dict(entry.get("env") or {})
    description = entry.get("description", "")
    note = entry.get("requirements", "")

    if kind == "git":
        try:
            command, args = _prepare_git(entry)
            description = f"{description} (installed from {entry['source']})"
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"failed to clone/build {entry['name']}: {exc}",
                "source": entry.get("source", ""),
            }
    elif kind == "go":
        try:
            _prepare_go(entry)
        except Exception:  # noqa: BLE001
            pass
    elif kind in ("app", "cli", "collection", "note", "index"):
        hint = {
            "app": "a desktop app, not a connectable server",
            "cli": "a CLI/daemon app — it exposes its MCP server from its own process",
            "collection": "a collection of servers — pick the individual servers listed above",
            "note": "already integrated — nothing to install",
            "index": "an index (a list of servers) — browse its Community tab instead",
        }[kind]
        return {
            "ok": False,
            "error": f"'{entry['name']}' is {hint}. {note}",
            "source": entry.get("source", ""),
        }
    elif kind == "community":
        if not entry.get("install_hint"):
            return {
                "ok": False,
                "error": "No one-command install found for this server — check the repo for setup steps.",
                "source": entry.get("source", ""),
            }
        parsed = _command_from_hint(entry["install_hint"])
        if not parsed:
            return {
                "ok": False,
                "error": f"Could not parse the install hint: {entry['install_hint']!r}",
                "source": entry.get("source", ""),
            }
        command, args = parsed
        description = f"{description} (from {entry.get('index', 'community')} catalog)"

    if not command:
        return {"ok": False, "error": f"'{name}' has no run command configured"}

    registry = MCPRegistry()
    try:
        server = registry.add_server(
            name=name,
            transport="stdio",
            command=command,
            args=args,
            env=env,
            description=description.strip(),
            enabled=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    result = {"ok": True, "server": {"name": server.name, "transport": server.transport}}
    if note:
        result["note"] = note
    return result


# --------------------------------------------------------------------------- #
# Public API for the dashboard
# --------------------------------------------------------------------------- #
def get_catalog(source: str | None = None, q: str | None = None) -> dict:
    """Assemble the catalog response for the Extensions dashboard."""
    curated = []
    for entry in CURATED:
        curated.append(
            {
                "name": entry["name"],
                "display": entry.get("display", entry["name"]),
                "description": entry.get("description", ""),
                "source": entry.get("source", ""),
                "kind": entry.get("kind", ""),
                "category": entry.get("category", ""),
                "requirements": entry.get("requirements", ""),
            }
        )

    community: list[dict] = []
    index_error: str | None = None
    if source:
        try:
            community = fetch_index(source)
        except Exception as exc:  # noqa: BLE001
            index_error = str(exc)

    if q:
        needle = q.strip().lower()
        curated = [e for e in curated if needle in e["name"].lower() or needle in e["description"].lower()]
        community = [e for e in community if needle in e["name"].lower() or needle in (e.get("description") or "").lower()]

    return {
        "curated": curated,
        "index_sources": INDEX_SOURCES,
        "community": community,
        "index_error": index_error,
        "installed": installed_names(),
    }
