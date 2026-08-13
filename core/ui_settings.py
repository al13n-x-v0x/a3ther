"""
core/ui_settings.py — A3THER UI settings store.

Small, dependency-free JSON settings file for user-facing UI preferences:
theme accent colors, active mode, hotkey bindings, poll interval, and
background behaviour. Lives in the OS app-data folder so it survives both
dev runs and the frozen exe, and is shared with the backend so the HUD and
the hotkey engine stay in sync.

Everything here degrades gracefully: missing/corrupt file → defaults.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

_lock = threading.RLock()  # re-entrant: save_ui_settings → _save() re-locks

DEFAULTS: Dict[str, Any] = {
    "theme": ["#00D2FF", "#FF9900"],
    "theme_name": "cyan",
    "assistant_name": "A.3.T.H.E.R.",
    "tagline": "Adaptive 3rd-generation Technology for Heuristic Execution & Research · by AL13N Industries",
    "mode": "ai",
    "poll_ms": 3000,
    "globe": True,
    "background": False,          # start hidden (background mode)
    "startup": False,             # auto-start with Windows
    "speech_popup": True,         # talking popup appears when A3THER speaks
    "ha_url": "",                # Home Assistant server (JARVIS integration)
    "ha_token": "",              # Home Assistant long-lived access token
    "hotkeys": {
        "toggle_hud": "Alt+F1",
        "toggle_voice": "Alt+F2",
        "screenshot": "Alt+F3",
        "cycle_mode": "Alt+F4",
        "lock_pc": "Alt+F5",
        "status": "Alt+F6",
        "open_hub": "Alt+F7",
        "show_popup": "Alt+F8",
    },
}

_state: Optional[Dict[str, Any]] = None


def _settings_path() -> Path:
    try:
        from config.paths import get_data_dir  # noqa: PLC0415

        base = get_data_dir()
    except Exception:  # noqa: BLE001
        base = Path.home() / ".a3ther"
    base.mkdir(parents=True, exist_ok=True)
    return base / "ui_settings.json"


def _load() -> Dict[str, Any]:
    global _state
    with _lock:
        if _state is not None:
            return _state
        state = dict(DEFAULTS)
        state["hotkeys"] = dict(DEFAULTS["hotkeys"])
        try:
            raw = json.loads(_settings_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if key == "hotkeys" and isinstance(value, dict):
                        state["hotkeys"].update({k: v for k, v in value.items() if isinstance(v, str)})
                    else:
                        state[key] = value
        except Exception:  # noqa: BLE001
            pass
        _state = state
        return _state


def _save() -> None:
    with _lock:
        try:
            _settings_path().write_text(
                json.dumps(_state or DEFAULTS, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass


def get_ui_settings() -> Dict[str, Any]:
    """Return a copy of the full UI settings map."""
    return json.loads(json.dumps(_load()))


def get_ui_setting(key: str, default: Any = None) -> Any:
    return _load().get(key, default)


def save_ui_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``updates`` into the store and persist. Returns the new state."""
    state = _load()
    with _lock:
        for key, value in updates.items():
            if key == "hotkeys" and isinstance(value, dict):
                state["hotkeys"].update({k: v for k, v in value.items() if isinstance(v, str)})
            else:
                state[key] = value
        _save()
    return json.loads(json.dumps(state))


def save_ui_setting(key: str, value: Any) -> Dict[str, Any]:
    return save_ui_settings({key: value})


def get_hotkeys() -> Dict[str, str]:
    return dict(_load()["hotkeys"])


def save_hotkeys(bindings: Dict[str, str]) -> Dict[str, str]:
    """Persist hotkey bindings; returns the saved map."""
    cleaned = {k: v for k, v in bindings.items() if isinstance(v, str) and v.strip()}
    save_ui_settings({"hotkeys": cleaned})
    return dict(cleaned)


def get_identity() -> Dict[str, str]:
    """Custom assistant name + tagline (used by the HUD and the brain prompt)."""
    state = _load()
    name = str(state.get("assistant_name") or "").strip() or DEFAULTS["assistant_name"]
    tagline = str(state.get("tagline") or "").strip() or DEFAULTS["tagline"]
    return {"name": name, "tagline": tagline}
