"""
core/auto_deps.py — self-healing optional dependencies.

The bundled exe ships every *core* dependency (FastAPI, uvicorn, …), but a
few optional ones are too heavy / too variable to bake in for everyone —
specifically the native voice stack (``vosk``, ``edge-tts``, ``miniaudio``).
Without them STT silently produces nothing and TTS falls back to the robot
SAPI voice.

This module makes the exe (and dev runs) self-sufficient: on first launch it
pings the missing modules and ``pip install``s them into the *same*
interpreter the app is running from. It runs once (a ``deps_checked`` flag in
the data folder), logs every step, and never crashes startup — if the install
fails (offline, no pip) the app just keeps running without the optional
voice, exactly as before.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Optional module → pip package (only these are auto-installed).
OPTIONAL_DEPS: dict[str, str] = {
    "vosk": "vosk",
    "edge_tts": "edge-tts",
    "miniaudio": "miniaudio",
}

#: When A3THER_SKIP_AUTO_DEPS=1, never touch pip (CI / locked-down boxes).
_SKIP_ENV = "A3THER_SKIP_AUTO_DEPS"


def _flag_path() -> Path:
    try:
        from config.paths import get_data_dir

        base = get_data_dir()
    except Exception:  # noqa: BLE001
        base = Path.home() / ".a3ther"
    base.mkdir(parents=True, exist_ok=True)
    return base / "deps_checked.flag"


def _missing() -> list[str]:
    """Module names that can't be imported right now."""
    out = []
    for module in OPTIONAL_DEPS:
        try:
            __import__(module)
        except Exception:  # noqa: BLE001
            out.append(module)
    return out


def _install(module: str, pkg: str) -> bool:
    """pip-install one package into the running interpreter. Returns success."""
    try:
        print(f"[AUTO-DEPS] installing {pkg} (one-time, {module} missing)…")
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", pkg]
        if sys.platform == "win32":
            # No console flash for GUI apps.
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        else:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        _ = proc.communicate(timeout=240)
        if proc.returncode == 0:
            print(f"[AUTO-DEPS] {pkg} installed.")
            return True
        print(f"[AUTO-DEPS] {pkg} install failed (exit {proc.returncode}) — continuing without it.")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[AUTO-DEPS] {pkg} install skipped: {exc}")
        return False


def ensure_optional_deps(force: bool = False) -> dict[str, bool]:
    """Install missing optional deps once. Returns {module: installed}.

    * Runs at most once per machine (flag file) unless ``force``.
    * Honors ``A3THER_SKIP_AUTO_DEPS=1``.
    * Never raises — every failure is logged and ignored.
    """
    if os.environ.get(_SKIP_ENV) == "1":
        return {}
    if not force and _flag_path().exists():
        return {}
    missing = _missing()
    if not missing:
        try:
            _flag_path().write_text("ok", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return {}

    results: dict[str, bool] = {}
    for module in missing:
        pkg = OPTIONAL_DEPS.get(module, module)
        ok = _install(module, pkg)
        results[module] = ok
    # Mark as done regardless so we don't re-try every launch.
    try:
        _flag_path().write_text("ok", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return results


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    print("missing:", _missing())
    print("result:", ensure_optional_deps(force=True))
