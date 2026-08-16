# =========================================================================================
# A.3.T.H.E.R ENGINE — BACKEND
# PHASE 2.2 — API CONNECTION CORE
# FRONTEND ↔ AI BRAIN COMMUNICATION LAYER
# Adaptive 3rd-generation Technology for Heuristic Execution & Research
# AL13N INDUSTRIES
# =========================================================================================


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import importlib
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "Frontend"

# Ensure logging is configured when the server module is imported
try:
    import core.logging_config as logging_config
    importlib.reload(logging_config)
except Exception:
    pass

# A3THER extensions: LLM gateway, plugins, MCP host, remote dev, autopilot
from backend.api.extensions import init_extensions, router as extensions_router
from backend.api.extensions import ui_router as extensions_ui_router

# A3THER features: voice, security, memory, codebase, swarm, website maker
from backend.api.features import features_router, features_ui_router

# A3THER live HUD data: telemetry, devices, weather, location
from backend.api.live import live_router

# A3THER multi-device mesh: broadcast engine, failsafe, /ws/mesh
from backend.api.sync import router as sync_router, ws_router as sync_ws_router



# ===============================
# SERVER CONFIGURATION
# ===============================


app = FastAPI(

    title="A.3.T.H.E.R API",

    version="Phase 2.2"

)




# ===============================
# CORS CONNECTION
# ===============================


app.add_middleware(

    CORSMiddleware,

    allow_origins=["*"],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"]

)

# A3THER extensions (gateway / plugins / MCP / remote dev / autopilot)
app.include_router(extensions_router)
app.include_router(extensions_ui_router)

# A3THER features (voice / security / memory / codebase / swarm / website)
app.include_router(features_router)
app.include_router(features_ui_router)

# A3THER live HUD data (telemetry / devices / weather / location)
app.include_router(live_router)

# A3THER multi-device mesh (broadcast / terminate / /ws/mesh)
app.include_router(sync_router)
app.include_router(sync_ws_router)

# Generated websites (website maker) — preview at /websites/<name>/index.html
from fastapi.staticfiles import StaticFiles  # noqa: E402

# Generated websites live under config.base_dir()/Output/websites — the
# SAME root the website_maker generator writes to. In dev that's the repo
# root; in the frozen exe it's the exe's own folder. (A __file__-relative
# path would resolve to _internal/ in the bundle and serve an empty dir
# while the generator wrote elsewhere — a silent 404.)
try:
    from config import base_dir as _repo_base

    _OUTPUT_DIR = _repo_base() / "Output" / "websites"
except Exception:  # noqa: BLE001
    _OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "Output" / "websites"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/websites", StaticFiles(directory=str(_OUTPUT_DIR)), name="websites")






# ===============================
# REQUEST FORMAT
# ===============================


class ChatRequest(BaseModel):
    message: str
    mode: str | None = None


class ModeRequest(BaseModel):
    mode: str


class SetupKeyRequest(BaseModel):
    provider: str
    key: str


class VoiceSettingsRequest(BaseModel):
    engine: str | None = None
    voice: str | None = None


# ===============================
# RESPONSE FORMAT
# ===============================


class ChatResponse(BaseModel):

    reply:str






# ===============================
# SYSTEM STATUS
# ===============================


@app.get("/")
def Home():
    """Serve the A.3.T.H.E.R. HUD dashboard (Frontend/index.html)."""
    page = FRONTEND_DIR / "index.html"
    if page.exists():
        return FileResponse(str(page))
    return {
        "system": "A.3.T.H.E.R",
        "status": "ONLINE",
        "phase": "2.2 API CORE",
        "hud": "missing — Frontend/index.html not found",
    }


@app.get("/hub")
def HubPage():
    """Flowbite-styled Hub — boot engine, MCP servers/tools, catalog, autopilot."""
    page = FRONTEND_DIR / "hub.html"
    return FileResponse(str(page), media_type="text/html") if page.exists() else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/hub/hub.js")
def HubJs():
    asset = FRONTEND_DIR / "hub.js"
    return FileResponse(str(asset), media_type="application/javascript") if asset.exists() else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/engine/status")
def EngineStatus():
    """Boot-engine runtime state: preflight, USB watcher, live boot log."""
    try:
        from core.engine_state import snapshot

        return snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "preflight": {}, "usb_running": False, "events": []}


@app.get("/phone")
def PhonePage():
    """Zero-install mobile control page — open on a phone (same Wi-Fi)."""
    page = FRONTEND_DIR / "phone.html"
    return FileResponse(str(page), media_type="text/html") if page.exists() else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/style.css")
def HUDCss():
    asset = FRONTEND_DIR / "style.css"
    return FileResponse(str(asset), media_type="text/css") if asset.exists() else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/script.js")
def HUDJs():
    asset = FRONTEND_DIR / "script.js"
    return FileResponse(str(asset), media_type="application/javascript") if asset.exists() else JSONResponse({"error": "not found"}, status_code=404)



@app.get("/api/time")
def TimeNow():
    try:
        import core.timekeeper as timekeeper_module
        tk = timekeeper_module.get_timekeeper()
        return {
            "now": tk.now_iso(),
            "uptime_seconds": tk.uptime_seconds(),
        }
    except Exception as e:
        return {"error": str(e)}







# ===============================
# AI CHAT ROUTE
# ===============================


@app.post(
"/api/chat",
response_model=ChatResponse
)

def Chat(data:ChatRequest):
    try:
        from backend.ai.brain import GenerateResponse

        response = GenerateResponse(data.message, mode=data.mode)
        return {"reply": response}
    except Exception as error:
        return {"reply": f"A.3.T.H.E.R Error: {error}"}


@app.get("/api/modes")
def GetModes():
    try:
        from backend.ai.brain import GetAvailableModes, GetModeMetadata

        return {
            "available": GetAvailableModes(),
            "current": GetModeMetadata(),
        }
    except Exception as error:
        return {"error": str(error)}


@app.post("/api/mode")
def SetMode(data:ModeRequest):
    try:
        from backend.ai.brain import SetMode, GetModeMetadata

        mode = SetMode(data.mode)
        return {
            "mode": mode,
            "metadata": GetModeMetadata(mode),
        }
    except Exception as error:
        return {"error": str(error)}








# ===============================
# MEMORY TEST ROUTE
# ===============================


@app.get("/api/memory")

def MemoryStatus():


    try:

        from backend.ai.memory import GetMemoryStatus

        return GetMemoryStatus()



    except Exception as error:


        return {


            "status":
            "offline",


            "error":
            str(error)


        }



@app.get("/api/ollama_status")
def OllamaStatus():
    try:
        # Optional helper; if not present, report unavailable
        try:
            import core.ollama_integration as ollama_integration
        except Exception:
            return {"available": False, "reason": "ollama helper not installed"}

        ok = ollama_integration.ensure_running()
        return {"available": bool(ok)}

    except Exception as error:
        return {"available": False, "error": str(error)}


@app.get("/api/setup/status")
def SetupStatus():
    """Whether an LLM key is configured (never reveals the key itself)."""
    try:
        from core.first_run import needs_setup, configured_providers, invalid_providers, PROVIDERS
        from config.paths import get_data_dir

        return {
            "needs_setup": needs_setup(),
            "providers": list(PROVIDERS),
            "configured": configured_providers(),
            "invalid": invalid_providers(),
            "data_dir": str(get_data_dir()),
        }
    except Exception as error:
        return {"error": str(error)}


@app.post("/api/setup/key")
def SetupKey(data: SetupKeyRequest):
    """Set (or replace) an LLM provider API key from the HUD Settings panel."""
    try:
        from core.first_run import save_key

        result = save_key(data.provider, data.key)
        if result.get("ok"):
            # Rebuild the gateway so the new key is live immediately — no restart.
            try:
                from gateway.router import reset_gateway

                reset_gateway()
            except Exception:  # noqa: BLE001
                pass
        return result
    except Exception as error:
        return {"ok": False, "error": str(error)}


@app.get("/api/settings/voice")
def VoiceSettings():
    """Current TTS engine + voice (used by the multi-language selector)."""
    try:
        from config import get_config

        cfg = get_config()
        return {
            "engine": cfg.get("tts_engine", "edgetts"),
            "voice": cfg.get("tts_voice", "en-US-GuyNeural"),
        }
    except Exception as error:  # noqa: BLE001
        return {"error": str(error)}


@app.post("/api/settings/voice")
def VoiceSettingsSave(data: VoiceSettingsRequest):
    """Persist the TTS engine + voice so speech matches the chosen language."""
    try:
        from config import save_config

        update: dict = {}
        if data.engine:
            update["tts_engine"] = data.engine
        if data.voice:
            update["tts_voice"] = data.voice
        save_config(update)
        return {"ok": True, **update}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)}


@app.get("/api/status")
def Status():
    try:
        from backend.ai.brain import BrainStatus
        import core.timekeeper as timekeeper_module

        status = BrainStatus()
        tk = timekeeper_module.get_timekeeper() if timekeeper_module else None
        status.update({
            "time_now": tk.now_iso() if tk else None,
            "uptime_seconds": tk.uptime_seconds() if tk else None,
        })
        return status
    except Exception as e:
        return {"error": str(e)}



# Ensure AI brain and memory are initialized when the API server starts
@app.on_event("startup")
async def startup_event():
    try:
        # Migrate repo state into the OS app-data folder (keys, memory, MCP…)
        try:
            from config.paths import migrate_all

            migrated = migrate_all()
            if any(migrated.values()):
                print("[SETUP] Migrated state into the A3THER app-data folder:",
                      [k for k, v in migrated.items() if v])
        except Exception:
            pass

        # First-run API-key prompt (only when stdin is a terminal).
        try:
            from core.first_run import maybe_run_setup

            maybe_run_setup()
        except Exception:
            pass

        # Initialize memory DB if available
        try:
            from backend.ai.memory import InitializeMemory
            InitializeMemory()
        except Exception:
            pass

        # Initialize AI brain
        try:
            from backend.ai.brain import InitializeBrain
            InitializeBrain()
        except Exception as e:
            print('[STARTUP] InitializeBrain failed:', e)

        # Initialize A3THER extensions (gateway, plugins, MCP host)
        try:
            init_extensions()
        except Exception as e:
            print('[STARTUP] init_extensions failed:', e)

        # Auto-start the voice pipeline (wake-word listening) so "hey aether"
        # works the moment the app opens — no manual Voice-button click needed.
        try:
            from voice.pipeline import get_voice_pipeline

            pipeline = get_voice_pipeline()
            pipeline.set_process_command(None)  # ensure the brain is wired
            from voice import brain as _voice_brain

            pipeline.set_process_command(_voice_brain.GenerateResponse)
            pipeline.start()
            print('[VOICE] Pipeline auto-started — say "hey aether"')
        except Exception as e:
            print('[STARTUP] Voice autostart failed (no mic?):', e)
    except Exception as e:
        print('[STARTUP] Unexpected startup error:', e)







# ===============================
# API START MESSAGE
# ===============================


print(
"""

╔══════════════════════════════╗
║  A.3.T.H.E.R API CORE ONLINE ║
║          PORT: 8000           ║
╚══════════════════════════════╝

"""
)