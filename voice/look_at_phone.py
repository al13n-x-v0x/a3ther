"""
voice/look_at_phone.py — A3THER's eyes on the phone.

When the phone's screen is cast (or just connected via ADB) the user can
say *"look at my phone"* and A3THER:

1. Captures the phone's screen over ADB (using the same adb resolution as
   the cast engine — system adb or the one bundled with auto-installed
   scrcpy).
2. Sends the screenshot to a vision-capable LLM (Gemini first) with the
   question the user asked, or a default "what's on my screen?".
3. Returns the answer as the spoken reply — so the AI literally reads
   what's on the phone and can act on it.

Degrades honestly: no phone connected → clear message; no vision provider
→ explains how to enable one; capture failure → surfaces the reason.
"""
from __future__ import annotations

import base64
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

LOGGER = logging.getLogger("a3ther.voice.look")

_VISION_SYSTEM = (
    "You are A3THER, the user's AI assistant, looking at the screen of their "
    "Android phone. Describe what is on screen in 1-2 short spoken sentences. "
    "If there is a clear actionable item (a notification, a message, a timer, "
    "an error), say what it is and what the user probably wants done with it. "
    "No markdown, no emoji spam."
)


def _adb_binary() -> str | None:
    """System adb, or the one bundled with auto-installed scrcpy."""
    import shutil

    on_path = shutil.which("adb")
    if on_path:
        return on_path
    try:
        from sync.cast import _find_scrcpy_exe

        exe = _find_scrcpy_exe()
        if exe:
            bundled = Path(exe).parent / "adb.exe"
            if bundled.exists():
                return str(bundled)
    except Exception:  # noqa: BLE001
        pass
    return None


def _capture(serial: str | None = None) -> tuple[bytes | None, str]:
    """Screencap the phone → (png_bytes, error). Never raises."""
    binary = _adb_binary()
    if not binary:
        return None, "adb not found — install scrcpy (bundles adb) or Android platform-tools"
    try:
        from sync.android import _pick_serial, adb_devices

        info = adb_devices()
        if not info.get("devices"):
            return None, "no Android device connected — plug it in via USB or wireless debugging"
        serial = _pick_serial(serial) or info["devices"][0]["serial"]
    except Exception as exc:  # noqa: BLE001
        return None, f"could not find the phone: {exc}"
    try:
        proc = subprocess.run(
            [binary, "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=20,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None, "screen capture failed — is the phone unlocked and USB debugging on?"
        if len(proc.stdout) < 100:
            return None, "screen capture came back empty"
        return proc.stdout, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"screen capture error: {type(exc).__name__}: {exc}"


def _vision_reply(png: bytes, question: str) -> str | None:
    """Ask a vision LLM about the screenshot. Gemini first, then any gateway
    provider that accepts images; None when no vision model is available."""
    # 1) Gemini native (google-genai) — the primary path.
    try:
        from config import get_config, get_env

        config = get_config()
        providers = config.get("llm_providers") or {}
        api_key = (
            get_env("A3THER_GEMINI_API_KEY")
            or providers.get("gemini_api_key")
            or config.get("gemini_api_key")
        )
        model = providers.get("gemini_model") or config.get("gemini_model") or "gemini-3-flash-preview"
        if api_key:
            from google import genai
            from google.genai import types as genai_types

            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=model,
                contents=genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(text=question or "What's on my screen?"),
                        genai_types.Part.from_bytes(data=png, mime_type="image/png"),
                    ],
                ),
                config=genai_types.GenerateContentConfig(
                    system_instruction=_VISION_SYSTEM,
                    max_output_tokens=200,
                ),
            )
            text = (resp.text or "").strip()
            if text:
                return re.sub(r"\s+", " ", text)[:400]
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Gemini vision failed: %s", exc)

    # 2) Generic OpenAI-compatible providers (OpenAI, Groq, DeepSeek vision).
    try:
        from gateway.router import get_gateway

        gateway = get_gateway()
        for name in ("openai", "groq", "deepseek", "gemini"):
            provider = gateway.providers.get(name)
            if not provider or not provider.available():
                continue
            b64 = base64.b64encode(png).decode("ascii")
            messages = [
                {"role": "system", "content": _VISION_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question or "What's on my screen?"},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                },
            ]
            result = provider.complete(messages, max_tokens=200, timeout=40)
            text = (result.text() or "").strip()
            if text:
                return re.sub(r"\s+", " ", text)[:400]
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Provider vision fallback failed: %s", exc)
    return None


def look_at_phone(question: str | None = None) -> str:
    """Capture the phone screen and answer with what the AI sees."""
    png, error = _capture()
    if png is None:
        return error
    try:
        reply = _vision_reply(png, (question or "").strip() or None)
    except Exception as exc:  # noqa: BLE001
        reply = None
        LOGGER.exception("Vision reply failed")
    if reply:
        return reply
    return (
        "I can see the phone is connected, but I need a vision-capable AI to read the "
        "screen — add a Gemini (or OpenAI) API key in Settings, then ask me again."
    )


def describe_capability() -> dict:
    """Status block used by the voice/help command."""
    return {
        "name": "look_at_phone",
        "description": "Capture the phone screen and let the AI read/act on what's on it",
        "needs": ["adb", "vision LLM (Gemini/OpenAI key)"],
    }
