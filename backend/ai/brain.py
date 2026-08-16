# =========================================================================================
# A.3.T.H.E.R ENGINE — BACKEND
# PHASE 2.3 — AI BRAIN CORE
# INTELLIGENCE ENGINE / PROMPT SYSTEM / MODEL CONNECTOR
# Adaptive 3rd-generation Technology for Heuristic Execution & Research
# AL13N INDUSTRIES
# =========================================================================================

import os
from datetime import datetime
import threading
from concurrent.futures import ThreadPoolExecutor
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass
from backend.ai.reasoning import Analyze

# Modified import: use module wrapper instead of non-existing `LLMClient` class.
import core.llm_client as llm_client

import memory.memory_manager as memory_manager

# Modified import: import memory helper functions used to load/format memory for prompts.
from memory.memory_manager import (
    load_memory,
    update_memory,
    format_memory_for_prompt,
    remember,
    forget,
    save_session_summary,
    pop_last_session,
)
from memory.memory_manager import save_memory as memory_save

# Modified import: use factory to create a `TTSPlayer` instance from config.
from core.tts import create_tts_player
from core.stt import create_stt_engine
from core.modes import ModeManager, ModeError

# Modified import: import STT module namespace (no STTManager class present).
import core.stt as stt_module

# Fixed actions import: import action modules/functions directly from package.
from actions import (
    open_app,
    web_search,
    browser_control,
    weather_report,
    system_monitor,
    file_controller,
    computer_control,
    send_message,
    youtube_video,
)

# Added: configuration and logging helpers used when initialising TTS/LLM.
from config import get_config
import logging

# Optional Ollama integration helper (kept separate)
try:
    import core.ollama_integration as ollama_integration
except Exception:
    ollama_integration = None

# Executor for background tasks (TTS, warmup, actions)
_EXECUTOR: ThreadPoolExecutor | None = None
# Voice enabled flag (read from config at init)
VOICE_ENABLED = True
# Mode manager for personality/voice modes
_MODE_MANAGER: ModeManager | None = None
# STT engine instance
_STT_ENGINE: object | None = None
# Module-level handles (safely default to None so API can import before init)
client = None
memory = None
tts = None
stt = None

# ===============================
# AI CONFIGURATION
# ===============================


AETHER_BRAIN = {

    "name": "A.3.T.H.E.R",

    "version": "Phase 2.3",

    "status": "OFFLINE",

    "personality":
    "Adaptive intelligent assistant",

    "requests": 0

}





# ===============================
# SYSTEM PROMPT
# ===============================


AETHER_SYSTEM_PROMPT = """

You are A.3.T.H.E.R.

Adaptive 3rd-generation Technology
for Heuristic Execution & Research.

You are an advanced assistant.

Personality:
- Intelligent
- Helpful
- Technical
- Adaptive

Abilities:
- Coding
- Research
- Planning
- Problem solving
- Memory management

Always provide useful answers.

"""







# ===============================
# INITIALIZE AI BRAIN
# ===============================



def InitializeBrain():

    global client
    global memory
    global tts
    global stt

    # Modified: use module-level llm client reference (no LLMClient class present)
    client = llm_client

    # Start a background executor for non-blocking tasks
    global _EXECUTOR, VOICE_ENABLED
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=4)

    # Keep the memory manager module available for other parts of the system
    # (this project uses functional memory helpers rather than a class)
    memory = memory_manager

    # Create a TTS player from config; fall back to None if creation fails
    try:
        cfg = get_config() or {}
        VOICE_ENABLED = bool(cfg.get("tts_enabled", True))
        tts = create_tts_player(cfg) if VOICE_ENABLED else None
    except Exception as e:
        logging.warning(f"[TTS] Could not create TTS player: {e}")
        tts = None

    # Create STT engine from config, if configured.
    global _STT_ENGINE, _MODE_MANAGER
    try:
        _STT_ENGINE = create_stt_engine(cfg)
    except Exception as e:
        logging.warning(f"[STT] Could not create STT engine: {e}")
        _STT_ENGINE = None

    # Initialize mode manager for persona and voice mode control.
    try:
        _MODE_MANAGER = ModeManager(default_mode=str(cfg.get("default_mode", "ai")))
    except Exception as e:
        logging.warning(f"[MODE] Could not initialize ModeManager: {e}")
        _MODE_MANAGER = ModeManager()

    # STT: the project exposes STT engine classes, not a manager wrapper.
    # Expose the module so callers may instantiate engines if needed.
    stt = stt_module

    AETHER_BRAIN["status"] = "ONLINE"

    logging.basicConfig(level=logging.INFO)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.info("[AETHER AI] Brain initialized")

    # Warm up LLM in background if available
    try:
        if ConnectModel() and hasattr(llm_client, "warmup_model"):
            def _warmup():
                try:
                    llm_client.warmup_model(system_prompt=AETHER_SYSTEM_PROMPT)
                except Exception as e:
                    logging.warning(f"[LLM] Warmup failed: {e}")
            _EXECUTOR.submit(_warmup)
    except Exception:
        logging.exception("[LLM] Warmup scheduling failed")

    # Optionally ensure Ollama is running in background via the separate helper
    try:
        if ollama_integration is not None and _EXECUTOR is not None:
            def _ensure_ollama():
                try:
                    ollama_integration.ensure_running()
                except Exception as e:
                    logging.debug(f"[Ollama] ensure_running error: {e}")
            _EXECUTOR.submit(_ensure_ollama)
    except Exception:
        logging.exception("[LLM] Warmup scheduling failed")









# ===============================
# MODEL CONNECTION
# ===============================


def ConnectModel():

    # Modified: detect external LLM availability. Prefer explicit API key,
    # otherwise check if the local LLM server is reachable.
    try:
        api_key = os.getenv("AI_API_KEY")
        if api_key:
            logging.info("[AI] API key found - external model available")
            return True

        # Check LLM server availability (Ollama / LM Studio / LocalAI)
        try:
            if hasattr(llm_client, "ensure_ollama_running"):
                ok = llm_client.ensure_ollama_running()
                if ok:
                    # If model listing check exists, verify model is pulled
                    if hasattr(llm_client, "check_model_available"):
                        llm_client.check_model_available(log=logging.info)
                    logging.info("[AI] Local LLM server reachable")
                    return True
        except Exception as e:
            logging.warning(f"[LLM] ensure_ollama_running failed: {e}")
    except Exception as e:
        logging.warning(f"[AI] ConnectModel check failed: {e}")

    logging.info("[AI] No API key found - Local mode")
    return False







# ===============================
# PROMPT BUILDER
# ===============================


def BuildPrompt(message: str, mode: str | None = None) -> str:
    """Build a prompt including the system persona, current mode, and user text."""
    mode_prompt = ""
    try:
        if _MODE_MANAGER is not None:
            mode_prompt = _MODE_MANAGER.get_mode_prompt(mode)
    except Exception as e:
        logging.warning(f"[MODE] Could not build mode prompt: {e}")

    return (
        AETHER_SYSTEM_PROMPT
        + "\n\n"
        + mode_prompt
        + "\n\nUSER:\n"
        + message
    )







# ===============================
# MAIN AI RESPONSE
# ===============================


def GenerateResponse(message, mode: str | None = None):


    AETHER_BRAIN["requests"] += 1

    # Modified: load memory and merge into prompt before reasoning/LLM
    logging.info("[AETHER INPUT] %s", message)

    try:
        memory_data = load_memory()
        memory_prompt = format_memory_for_prompt(memory_data) or ""

        analysis = Analyze(message)
        intent = analysis.get("intent", "LLM")
        analysis_mode = analysis.get("mode") if isinstance(analysis.get("mode"), str) else None
        requested_mode = mode or analysis_mode

        if requested_mode and _MODE_MANAGER is not None:
            try:
                _MODE_MANAGER.set_mode(requested_mode)
            except ModeError:
                logging.warning(f"[MODE] Unsupported mode requested: {requested_mode}")

        combined_prompt = (
            memory_prompt
            + "\n\n"
            + BuildPrompt(message, requested_mode)
        ).strip()

        response = None

        # Route to action modules for specific intents using a registry
        def _safe_call(func, params=None, default=None):
            try:
                if params is None:
                    params = {}
                return func(params)
            except TypeError:
                # Try calling without params
                try:
                    return func()
                except Exception as e:
                    logging.exception("[Action] call failed")
                    return default or f"Action failed: {e}"
            except Exception as e:
                logging.exception("[Action] call failed")
                return default or f"Action failed: {e}"

        if intent == "OPEN_APP":
            response = _safe_call(open_app.open_app, {"app_name": analysis.get("application") or message})
        elif intent == "WEB_SEARCH":
            response = _safe_call(web_search.web_search, {"query": message})
        elif intent == "WEATHER":
            response = _safe_call(weather_report.weather_action, {"city": message})
        elif intent == "SYSTEM":
            try:
                status = system_monitor.get_system_status()
                response = f"System status: {status}"
            except Exception as e:
                logging.exception("[Action:SYSTEM] failed")
                response = f"System monitor failed: {e}"
        elif intent == "FILE":
            response = _safe_call(file_controller.list_files)
        elif intent == "YOUTUBE":
            response = _safe_call(youtube_video.youtube_video, {"action": "play", "query": message})

        elif intent == "REMOTE_DEV":
            # "a3ther, act as a dev on <server>" and session commands
            try:
                from remote_dev.dev_mode import get_dev_mode_manager
                response = get_dev_mode_manager().handle(message)
            except Exception as e:
                logging.exception("[DevMode] failed")
                response = f"Remote dev mode error: {e}"

        elif intent == "WEBSITE":
            # 3D website maker
            try:
                from website_maker.generator import generate_website
                description = message
                result = generate_website(description)
                response = (
                    f"Website generated: {result['name']} ({result['source']})\n"
                    f"Saved to: {result['path']}\n"
                    f"Preview: {result['preview']}"
                )
            except Exception as e:
                logging.exception("[Website] failed")
                response = f"Website generation error: {e}"

        elif intent == "AGENT":
            # Multi-agent swarm delegation
            try:
                from swarm.supervisor import run_task
                result = run_task(message)
                if result.get("ok"):
                    steps = " → ".join(result.get("steps", []))
                    response = f"Swarm plan: {steps}\n\n" + (result.get("summary") or "")
                else:
                    response = f"Swarm failed: {result.get('error')}"
            except Exception as e:
                logging.exception("[Agents] failed")
                response = f"Swarm error: {e}"

        elif intent == "PLUGIN":
            # Plugin / extension / MCP management
            try:
                from plugins.manager import get_plugin_manager
                plugins = get_plugin_manager().list_plugins()
                if not plugins:
                    response = (
                        "No plugins are installed. Drop a folder containing an "
                        "a3ther-plugin.json into plugins/ to add one."
                    )
                else:
                    lines = ["Installed plugins:"]
                    for p in plugins:
                        state = "enabled" if p.enabled else "disabled"
                        loaded = "loaded" if p.loaded else "not loaded"
                        caps = ", ".join(c.get("name", "") for c in p.capabilities) or "none"
                        lines.append(
                            f"- {p.name} v{p.version} [{p.plugin_type}] "
                            f"({state}, {loaded}) capabilities: {caps}"
                        )
                    response = "\n".join(lines)
            except Exception as e:
                logging.exception("[Plugins] failed")
                response = f"Plugin manager error: {e}"

        else:
            # LLM path: prefer the multi-model gateway, fall back to the
            # legacy local client (Ollama / OpenAI-compatible), then LocalAI.
            try:
                from gateway.router import get_gateway
                gateway = get_gateway()
                if gateway.any_available():
                    try:
                        response = gateway.complete_text(
                            prompt=combined_prompt,
                            system=AETHER_SYSTEM_PROMPT,
                        )
                    except Exception as e:
                        logging.exception("[LLM] Gateway failed, falling back to legacy client")
                        response = _legacy_llm(combined_prompt, message)
                else:
                    response = _legacy_llm(combined_prompt, message)
            except Exception as e:
                logging.exception("[GenerateResponse] LLM decision failed")
                response = _legacy_llm(combined_prompt, message)

        # Save conversation and speak response via TTS (if available)
        try:
            SaveConversation(message, response)
        except Exception:
            logging.exception("[Memory] SaveConversation failed")

        try:
            if tts and response and isinstance(response, str) and VOICE_ENABLED:
                # speak in background to avoid blocking API threads
                try:
                    if _EXECUTOR:
                        _EXECUTOR.submit(tts.speak, response)
                    else:
                        # fallback synchronous speak
                        tts.speak(response)
                except Exception:
                    logging.exception("[TTS] speak failed")
        except Exception:
            logging.exception("[TTS] Unexpected error")

        return response
    except Exception as e:
        logging.exception("[GenerateResponse] Unexpected error: %s", e)
        return LocalAI(message)







# ===============================
# LOCAL AI FALLBACK
# ===============================


def LocalAI(message):


    text = message.lower()



    if "hello" in text:


        return (
            "Hello. "
            "A.3.T.H.E.R is online."
        )



    if "status" in text:


        return (

            "A.3.T.H.E.R STATUS\n"

            "-----------------\n"

            "AI Core: ONLINE\n"

            "Memory: READY\n"

            "Backend: ACTIVE"

        )




    if "who are you" in text:


        return (

            "I am A.3.T.H.E.R.\n"

            "Adaptive 3rd-generation "
            "Technology for Heuristic "
            "Execution & Research."

        )




    return (

        "Processing request...\n\n"

        "Input: "

        +

        message

        +

        "\n\n"

        "Local intelligence mode active."

    )





def _legacy_llm(combined_prompt: str, message: str) -> str:
    """Legacy LLM path (Ollama / OpenAI-compatible client) with LocalAI fallback."""
    try:
        if ConnectModel():
            try:
                return llm_client.call_llm_text(
                    prompt=combined_prompt,
                    system=AETHER_SYSTEM_PROMPT,
                )
            except Exception as e:
                logging.exception("[LLM] call failed, falling back to LocalAI")
                return LocalAI(message)
        return LocalAI(message)
    except Exception as e:
        logging.exception("[GenerateResponse] LLM decision failed")
        return LocalAI(message)




# ===============================
# MEMORY CONNECTION
# ===============================


def SaveConversation(user, assistant):
    # Modified: unified memory handling.
    # 1) Save a brief session summary into memory.long_term via save_session_summary
    # 2) Update structured long-term JSON memory with a note entry via update_memory
    # 3) Call legacy backend.ai.memory.SaveMemory (SQLite) for compatibility (best-effort)
    try:
        try:
            # save a short session summary (non-blocking)
            summary = (str(assistant) or "").strip()
            if summary:
                save_session_summary(summary[:280])
        except Exception:
            logging.exception("[Memory] save_session_summary failed")

        try:
            # Add a note entry to JSON memory for traceability
            key = f"conv_{int(time.time())}"
            update_memory({"notes": {key: {"value": f"User: {user} -- Assistant: {assistant}"}}})
        except Exception:
            logging.exception("[Memory] update_memory failed")

        try:
            # Best-effort legacy SQLite persistence for backwards compatibility
            from backend.ai.memory import SaveMemory as _LegacySave
            _LegacySave({
                "user": user,
                "assistant": assistant,
                "category": "conversation",
                "time": datetime.now().isoformat(),
            })
        except Exception:
            logging.debug("[Memory] Legacy SaveMemory not available or failed")

    except Exception as error:
        logging.exception("[MEMORY WARNING] %s", error)








# ===============================
# BRAIN STATUS
# ===============================


def BrainStatus():


    return {


        "name":
        AETHER_BRAIN["name"],


        "version":
        AETHER_BRAIN["version"],


        "status":
        AETHER_BRAIN["status"],


        "mode":
        _MODE_MANAGER.get_mode() if _MODE_MANAGER is not None else None,


        "requests":
        AETHER_BRAIN["requests"]


    }


def GetAvailableModes():
    if _MODE_MANAGER is None:
        raise ModeError("Mode manager is not initialized")
    return _MODE_MANAGER.available_modes()


def GetModeMetadata(mode: str | None = None):
    if _MODE_MANAGER is None:
        raise ModeError("Mode manager is not initialized")
    return _MODE_MANAGER.get_mode_metadata(mode)


def SetMode(mode: str) -> str:
    if _MODE_MANAGER is None:
        raise ModeError("Mode manager is not initialized")
    return _MODE_MANAGER.set_mode(mode)


# ===============================
# TEST MODE
# ===============================


if __name__ == "__main__":


    InitializeBrain()


    print(
        BrainStatus()
    )


    print(
        GenerateResponse(
            "Hello A.3.T.H.E.R"
        )
    )