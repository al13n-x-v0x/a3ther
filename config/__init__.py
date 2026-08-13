"""
config — A3THER configuration package.

Central access to A3THER's settings. The authoritative store is the OS
app-data folder (see :mod:`config.paths`) so keys survive both dev runs and
the frozen exe; a repo mirror (``config/api_keys.json``) is maintained so
legacy readers that hit the file directly never crash.

Public API
----------
``get_config()``      – merged settings dict (defaults + stored JSON + env).
``save_config(upd)``  – merge ``upd`` into the store, persist, return merged.
``get_env(name, dflt)`` – read an environment variable (with .env support).
``load_env_file()``   – load ``.env`` from the project root into ``os.environ``.
``base_dir()``        – project root (frozen-exe aware).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = [
    "base_dir",
    "get_config",
    "save_config",
    "get_env",
    "load_env_file",
    "get_config_path",
]


def base_dir() -> Path:
    """Project root — where ``plugins/``, ``Output/``, ``.env`` live.

    Resolves to the executable's folder when frozen (PyInstaller), otherwise
    the parent of this package.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_config_path() -> Path:
    """Path of the authoritative settings JSON (app-data, with repo mirror)."""
    try:
        from .paths import data_path

        return data_path("config/api_keys.json")
    except Exception:  # noqa: BLE001
        return base_dir() / "config" / "api_keys.json"


# --------------------------------------------------------------------------- #
# Defaults — everything the brain / voice / gateway reads with .get(key, dflt)
# --------------------------------------------------------------------------- #
_DEFAULTS: Dict[str, Any] = {
    # Voice
    "tts_enabled": True,
    "tts_engine": "edgetts",
    "tts_voice": "en-US-GuyNeural",
    "tts_speed": 1.0,
    "stt_engine": "vosk",
    "vosk_language": "en-us",
    "whisper_model": "base",
    "wake_word_engine": "vosk",
    "wake_language": "en-us",
    # Modes
    "default_mode": "ai",
    # LLM provider keys (empty until the user saves them via Settings)
    "openai_api_key": "",
    "deepseek_api_key": "",
    "gemini_api_key": "",
    "groq_api_key": "",
    "anthropic_api_key": "",
    # Gateway structure
    "llm_providers": {
        "openai_api_key": "",
        "openai_base_url": "",
        "openai_model": "gpt-4o-mini",
        "deepseek_api_key": "",
        "deepseek_model": "deepseek-chat",
        "gemini_api_key": "",
        "gemini_model": "gemini-3-flash-preview",
        "groq_api_key": "",
        "groq_model": "llama-3.3-70b-versatile",
        "anthropic_api_key": "",
        "anthropic_model": "claude-3-5-haiku-latest",
    },
    "llm_routing": {
        "priority": ["openai", "deepseek", "gemini", "groq", "anthropic"],
        "fallback_enabled": True,
        "breaker_threshold": 3,
        "breaker_cooldown_seconds": 30,
        "request_timeout_seconds": 90,
    },
    # Legacy local-LLM client
    "llm_url": "http://localhost:11434",
    "llm_model": "llama3.2",
    "llm_provider": "ollama",
}


def _deep_merge(base: dict, updates: dict) -> dict:
    """Recursively merge ``updates`` into ``base`` (dicts merge, rest replace)."""
    out = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _stored() -> dict:
    try:
        raw = get_config_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — missing/corrupt file → defaults
        return {}


def get_config() -> Dict[str, Any]:
    """Merged settings: defaults ← stored JSON ← environment (``A3THER_*``)."""
    cfg = _deep_merge(_DEFAULTS, _stored())
    # Environment wins for provider keys (A3THER_OPENAI_API_KEY, …).
    for provider in ("openai", "deepseek", "gemini", "groq", "anthropic"):
        env_val = get_env(f"A3THER_{provider.upper()}_API_KEY")
        if env_val:
            cfg[f"{provider}_api_key"] = env_val
            providers = cfg.setdefault("llm_providers", {})
            providers[f"{provider}_api_key"] = env_val
    return cfg


def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``updates`` into the stored JSON (both app-data + repo mirror).

    Returns the fully merged config so callers can inspect the result
    (e.g. ``first_run.save_key`` lists which providers are configured).
    """
    path = get_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = _deep_merge(_stored(), updates)
        path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        # Repo mirror — keeps legacy readers (core/llm_client etc.) working.
        try:
            mirror = base_dir() / "config" / "api_keys.json"
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001 — frozen exe: repo may be read-only
            pass
    except Exception as exc:  # noqa: BLE001
        print(f"[CONFIG] save failed: {exc}")
    return _deep_merge(get_config(), updates)


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read an environment variable (seeded from .env by :func:`load_env_file`)."""
    load_env_file()
    return os.environ.get(name, default)


def load_env_file() -> None:
    """Load ``.env`` from the project root into ``os.environ`` (idempotent).

    Simple parser: ``KEY=VALUE`` lines, ``#`` comments, quotes stripped.
    Does not override variables that are already set in the environment.
    """
    if getattr(load_env_file, "_loaded", False):
        return
    load_env_file._loaded = True  # type: ignore[attr-defined]
    try:
        env_path = base_dir() / ".env"
        if not env_path.exists():
            return
        for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:  # noqa: BLE001 — .env is best-effort
        pass
