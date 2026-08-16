"""
backend/api/features.py — A3THER feature APIs.

Voice · Security · Memory · Codebase · Swarm · Website Maker.

Endpoints
---------
Voice:      GET /api/voice/status · POST /api/voice/start · /stop · /say
Security:   GET /api/security/policy · POST /api/security/check · /run
            GET  /api/security/approvals · POST /api/security/approvals/{id}/decide
Memory:     GET /api/memory/status · POST /api/memory/observe · /query · /search
Codebase:   POST /api/codebase/index · GET /api/codebase/status
            POST /api/codebase/search · POST /api/codebase/tool
Swarm:      POST /api/agents/run · GET /api/agents/events · GET /api/agents/status
Website:    POST /api/website/generate · GET /api/website/list
UI:         GET /agents (+ .js/.css) — the swarm terminal console
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

features_router = APIRouter(prefix="/api", tags=["features"])
features_ui_router = APIRouter(tags=["agents-ui"])

FRONTEND = Path(__file__).resolve().parent.parent.parent / "Frontend"


# ------------------------------------------------------------------------- #
# Request models
# ------------------------------------------------------------------------- #
class CommandRequest(BaseModel):
    command: str
    cwd: str | None = None
    timeout: int | None = 30


class ApprovalDecisionRequest(BaseModel):
    approved: bool


class MemoryTextRequest(BaseModel):
    text: str
    k: int | None = 5


class CodeToolRequest(BaseModel):
    tool: str
    args: dict = {}


class AgentsRunRequest(BaseModel):
    task: str


class WebsiteRequest(BaseModel):
    description: str
    name: str = ""
    theme: str = ""


class VoiceChatRequest(BaseModel):
    text: str
    system: str | None = None


class InternetRequest(BaseModel):
    query: str
    max_results: int | None = 6


class LearnRequest(BaseModel):
    topic: str


class VideoRenderRequest(BaseModel):
    source_dir: str
    style: str | None = None
    title: str = ""


class VideoClipsSearchRequest(BaseModel):
    query: str
    max_results: int | None = 10


class VideoClipsRenderRequest(BaseModel):
    query: str
    style: str | None = None
    count: int | None = 6
    title: str = ""


class YoutubeAuthCodeRequest(BaseModel):
    code: str


class YoutubeProposeRequest(BaseModel):
    video_path: str
    title: str | None = None


class YoutubeApproveRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None


# ------------------------------------------------------------------------- #
# YouTube upload pipeline (connect → propose → approve → publish)
# ------------------------------------------------------------------------- #
@features_router.get("/youtube/status")
def youtube_status():
    try:
        from youtube_upload import connect_status, bot_status

        return {**connect_status(), **bot_status()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/youtube/auth-url")
def youtube_auth_url():
    try:
        from youtube_upload import get_auth_url

        return get_auth_url()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/youtube/auth-code")
def youtube_auth_code(body: YoutubeAuthCodeRequest):
    try:
        from youtube_upload import exchange_code

        return exchange_code(body.code)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/youtube/disconnect")
def youtube_disconnect():
    try:
        from youtube_upload import disconnect

        return disconnect()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/youtube/propose")
def youtube_propose(body: YoutubeProposeRequest):
    """Stage a rendered video for approval with AI-generated title/tags/desc."""
    try:
        from youtube_upload import propose_upload

        return propose_upload(body.video_path, body.title)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/youtube/approvals")
def youtube_approvals():
    try:
        from youtube_upload import approvals

        return {"approvals": approvals()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/youtube/approve/{approval_id}")
def youtube_approve(approval_id: str, body: YoutubeApproveRequest):
    """Approve a pending upload (optionally editing the AI metadata)."""
    try:
        from youtube_upload import approve

        return approve(approval_id, body.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/youtube/reject/{approval_id}")
def youtube_reject(approval_id: str):
    try:
        from youtube_upload import reject

        return reject(approval_id)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/youtube/bot/start")
def youtube_bot_start():
    """Start the auto-reply bot (replies to comments = growth loop)."""
    try:
        from youtube_upload import bot_start

        return bot_start()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/youtube/bot/stop")
def youtube_bot_stop():
    try:
        from youtube_upload import bot_stop

        return bot_stop()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Voice
# ------------------------------------------------------------------------- #
@features_router.get("/voice/status")
def voice_status():
    try:
        from voice.pipeline import get_voice_pipeline
        from voice.audio_io import list_input_devices

        pipeline = get_voice_pipeline()
        return {
            "state": pipeline.state,
            "speaking": pipeline.speaker.is_speaking,
            "wake_engine": pipeline.wake.name,
            "stt_engine": pipeline.transcriber.engine,
            "live": pipeline.live_mode,
            "devices": list_input_devices()[:6],
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/voice/start")
def voice_start():
    try:
        from voice.pipeline import get_voice_pipeline

        get_voice_pipeline().start()
        return {"ok": True, "state": "wake_listening"}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/voice/stop")
def voice_stop():
    try:
        from voice.pipeline import get_voice_pipeline

        get_voice_pipeline().stop()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


class VoiceLiveRequest(BaseModel):
    live: bool = True


@features_router.get("/voice/live")
def voice_live_status():
    """Whether Gemini-Live continuous conversation mode is enabled."""
    try:
        from voice.pipeline import get_voice_pipeline

        return {"live": get_voice_pipeline().live_mode}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/voice/live")
def voice_live_toggle(body: VoiceLiveRequest):
    """Toggle Gemini-Live mode: continuous listening, no re-wake needed.

    Enabling also starts the pipeline so the conversation begins immediately.
    """
    try:
        from voice.pipeline import get_voice_pipeline

        pipeline = get_voice_pipeline()
        pipeline.set_live_mode(body.live)
        if body.live:
            pipeline.start()
        return {"ok": True, "live": pipeline.live_mode, "state": pipeline.state}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/voice/say")
def voice_say(body: MemoryTextRequest):
    """Speak text aloud. Reports honestly when the TTS engine is missing."""
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return JSONResponse(
            {"ok": False, "error": "TTS engine missing — run: pip install edge-tts"},
            status_code=500,
        )
    try:
        from voice.pipeline import get_voice_pipeline

        get_voice_pipeline().speaker.say_now(body.text)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/voice/chat")
def voice_chat(body: VoiceChatRequest):
    """Gemini-Live style loop: text in → reply (spoken aloud).

    Routed through :func:`voice.brain.GenerateResponse` — the same brain the
    wake-word pipeline uses — so native device commands AND every connected
    MCP tool (dynamic schema + ``mcp_tool`` action) work here too, not just
    plain chat. Returns the reply + provider + spoken state for the HUD.
    """
    try:
        from gateway.router import get_gateway
        from voice.brain import GenerateResponse
        from voice.pipeline import get_voice_pipeline

        gateway = get_gateway()
        provider = (gateway.best_provider() or "") if gateway.any_available() else "native"
        # The brain never raises — it degrades to an honest spoken message.
        reply = GenerateResponse(body.text, system=body.system or None)
        spoken = False
        speak_error = None
        try:
            get_voice_pipeline().speaker.say_now(reply)
            spoken = True
        except Exception as exc:  # noqa: BLE001
            speak_error = str(exc)
        return {"ok": True, "reply": reply, "provider": provider, "spoken": spoken, "speak_error": speak_error}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Internet skills
# ------------------------------------------------------------------------- #
@features_router.post("/internet/search")
def internet_search(body: InternetRequest):
    """Web search (no API key needed) — lets A3THER browse the internet."""
    try:
        from internet.skills import search_web

        results = search_web(body.query, max_results=body.max_results or 6)
        return {"ok": True, "query": body.query, "results": results}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=502)


@features_router.post("/internet/learn")
def internet_learn(body: LearnRequest):
    """Research a topic: search the web + LLM summary = a skill brief."""
    try:
        from internet.skills import learn

        return learn(body.topic)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Video studio (AI editor)
# ------------------------------------------------------------------------- #
@features_router.post("/video/render")
def video_render(body: VideoRenderRequest):
    """Start a background render: folder of clips/images → stylised edit.

    Styles: tiktok_intense · anime · movie_trailer · aesthetic.
    """
    try:
        from video_editor.engine import start_render
        from video_editor.styles import style_names

        if body.style and body.style not in style_names():
            return JSONResponse(
                {"error": f"unknown style '{body.style}'; use {style_names()}"},
                status_code=400,
            )
        job = start_render(body.source_dir, body.style, body.title)
        return {"ok": True, "job": job.to_dict()}
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/video/status")
def video_status():
    try:
        from video_editor.engine import job_status

        return {"jobs": job_status()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/video/list")
def video_list():
    try:
        from video_editor.engine import list_videos

        return {"videos": list_videos()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/video/file/{name}")
def video_file(name: str):
    """Download a rendered edit (path-traversal safe)."""
    try:
        from video_editor.engine import get_video_path

        path = get_video_path(name)
        if path is None:
            return JSONResponse({"error": "video not found"}, status_code=404)
        return FileResponse(str(path), media_type="video/mp4", filename=path.name)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/video/clips/search")
def video_clips_search(body: VideoClipsSearchRequest):
    """Search the internet for the best short clips for a vibe (TikTok genre)."""
    try:
        from video_editor.clips import search_clips

        return search_clips(body.query, body.max_results or 10)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/video/clips/render")
def video_clips_render(body: VideoClipsRenderRequest):
    """Fetch the best internet clips for a vibe and render a TikTok-style edit."""
    try:
        from video_editor.clips import fetch_and_render
        from video_editor.styles import style_names

        if body.style and body.style not in style_names():
            return JSONResponse(
                {"error": f"unknown style '{body.style}'; use {style_names()}"},
                status_code=400,
            )
        return fetch_and_render(body.query, body.style, body.count, body.title)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Security
# ------------------------------------------------------------------------- #
@features_router.get("/security/policy")
def security_policy():
    try:
        from security.policy import load_policy

        policy = load_policy()
        return {
            "approval_timeout_seconds": policy.approval_timeout_seconds,
            "auto_approve_safe": policy.auto_approve_safe,
            "blocked_rules": len(policy.blocked),
            "dangerous_rules": len(policy.dangerous),
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/security/check")
def security_check(body: CommandRequest):
    try:
        from security.guard import CommandGuard

        return {"decision": CommandGuard().validate(body.command).to_dict()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/security/run")
def security_run(body: CommandRequest):
    """Run a command through the sandbox (dangerous → waits for approval)."""
    try:
        from security.sandbox import SandboxedExecutor

        result = SandboxedExecutor().run(
            body.command, cwd=body.cwd, timeout=body.timeout or 30, interactive=False
        )
        return result
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/security/approvals")
def security_approvals():
    try:
        from security.guard import get_approval_gate

        return {"pending": get_approval_gate().pending()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/security/approvals/{approval_id}/decide")
def security_decide(approval_id: str, body: ApprovalDecisionRequest):
    try:
        from security.guard import get_approval_gate

        ok = get_approval_gate().decide(approval_id, body.approved)
        return {"ok": ok}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Memory
# ------------------------------------------------------------------------- #
@features_router.get("/memory/status")
def memory_status():
    try:
        from memory.orchestrator import get_memory_orchestrator

        return get_memory_orchestrator().status()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/memory/observe")
def memory_observe(body: MemoryTextRequest):
    try:
        from memory.orchestrator import get_memory_orchestrator

        stored = get_memory_orchestrator().observe(body.text)
        return {"ok": True, "queued": stored}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/memory/query")
def memory_query(body: MemoryTextRequest):
    try:
        from memory.orchestrator import get_memory_orchestrator

        orchestrator = get_memory_orchestrator()
        context = orchestrator.build_context(body.text, k=body.k or 5)
        hits = [
            {"text": unit.text, "importance": unit.importance, "category": unit.category}
            for unit, score in orchestrator.vector.search(body.text, k=body.k or 5)
        ]
        return {"context": context, "hits": hits}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Codebase
# ------------------------------------------------------------------------- #
@features_router.post("/codebase/index")
def codebase_index(body: MemoryTextRequest):
    """body.text is the directory to index (default: repo root)."""
    try:
        from codebase.indexer import CodeIndexer

        indexer = CodeIndexer()
        updated = indexer.index_directory(body.text or ".")
        return {"ok": True, "updated": updated, "stats": indexer.stats()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/codebase/status")
def codebase_status():
    try:
        from codebase.indexer import CodeIndexer

        return CodeIndexer().stats()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/codebase/search")
def codebase_search(body: MemoryTextRequest):
    try:
        from codebase.indexer import CodeIndexer

        indexer = CodeIndexer()
        if not indexer.files():
            indexer.index_directory(".")
        return {"symbols": [s.__dict__ for s in indexer.search_symbols(body.text, limit=20)]}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.post("/codebase/tool")
def codebase_tool(body: CodeToolRequest):
    try:
        from codebase.tools import execute_tool

        return {"result": execute_tool(body.tool, body.args)}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Swarm
# ------------------------------------------------------------------------- #
@features_router.post("/agents/run")
def agents_run(body: AgentsRunRequest):
    try:
        from swarm.supervisor import run_task

        result = run_task(body.task)
        return result
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/agents/events")
def agents_events(limit: int = 100):
    try:
        from swarm.events import get_event_log

        return {"events": get_event_log().last(limit)}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/agents/status")
def agents_status():
    try:
        from swarm.agents import AGENT_CLASSES
        from swarm.events import get_event_log

        return {"agents": list(AGENT_CLASSES.keys()), "events": len(get_event_log().last(1000))}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Website maker
# ------------------------------------------------------------------------- #
@features_router.post("/website/generate")
def website_generate(body: WebsiteRequest):
    try:
        from website_maker.generator import generate_website

        return generate_website(body.description, name=body.name, theme=body.theme)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@features_router.get("/website/list")
def website_list():
    try:
        from website_maker.generator import list_websites

        return {"sites": list_websites()}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------------- #
# Agents console UI
# ------------------------------------------------------------------------- #
@features_ui_router.get("/agents")
def agents_page():
    page = FRONTEND / "agents.html"
    return FileResponse(str(page)) if page.exists() else JSONResponse({"error": "Frontend/agents.html not found"}, status_code=404)


@features_ui_router.get("/agents/agents.js")
def agents_js():
    asset = FRONTEND / "agents.js"
    return FileResponse(str(asset), media_type="application/javascript") if asset.exists() else JSONResponse({"error": "not found"}, status_code=404)


@features_ui_router.get("/agents/agents.css")
def agents_css():
    asset = FRONTEND / "agents.css"
    return FileResponse(str(asset), media_type="text/css") if asset.exists() else JSONResponse({"error": "not found"}, status_code=404)
