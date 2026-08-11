"""
core/first_run.py — first-deployment setup.

On the first launch A3THER has no LLM API key. This module:

1. detects whether setup is needed (no key for any provider configured),
2. interactively asks for the provider + API key when stdin is a terminal,
3. saves the key to the OS app-data config (``%LOCALAPPDATA%/A3THER`` etc.,
   see config/paths.py) with a repo mirror so every reader sees it.

Run it manually any time:  ``python -m core.first_run``
Skipped automatically when stdin isn't a tty (headless/daemon runs) and
when ``A3THER_NONINTERACTIVE=1`` is set.
"""
from __future__ import annotations

import getpass
import os
import sys

PROVIDERS = {
    "openai": "openai_api_key",
    "deepseek": "deepseek_api_key",
    "gemini": "gemini_api_key",
    "groq": "groq_api_key",
    "anthropic": "anthropic_api_key",
}


def needs_setup() -> bool:
    """True when no LLM provider key is configured anywhere yet."""
    try:
        from config import get_config

        cfg = get_config()
    except Exception:  # noqa: BLE001
        return True
    return not any(cfg.get(key) for key in PROVIDERS.values())


def is_plausible_gemini_key(key: str) -> bool:
    """Accept real Gemini API key formats.

    Google issues two key shapes: the classic ``AIza…`` keys and the newer
    ``AQ.``-prefixed keys. We only reject things that are obviously NOT an
    API key (OAuth tokens, placeholder text, far-too-short strings) so the
    router never wastes a request on them — anything else is trusted.
    """
    key = (key or "").strip()
    if len(key) < 10:
        return False
    lowered = key.lower()
    if lowered.startswith(("ya29.", "oauth", "ghp_", "sk-", "ak-", "<")):
        return False
    return True


def configured_providers() -> list[str]:
    """Names of providers that already have a key set."""
    try:
        from config import get_config

        cfg = get_config()
    except Exception:  # noqa: BLE001
        return []
    return [name for name, key in PROVIDERS.items() if cfg.get(key)]


def invalid_providers() -> list[str]:
    """Providers whose stored key fails format validation (e.g. a Gemini
    key that is clearly an OAuth token rather than an API key). These
    keys exist in config but will never authenticate, so we flag them.
    """
    try:
        from config import get_config

        cfg = get_config()
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for name, key_name in PROVIDERS.items():
        value = cfg.get(key_name)
        if not value:
            continue
        if name == "gemini" and not is_plausible_gemini_key(str(value)):
            out.append(name)
    return out


def save_key(provider: str, key: str) -> dict:
    """Persist an API key for a provider. Returns the merged config."""
    provider = (provider or "").strip().lower()
    key = (key or "").strip()
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"unknown provider '{provider}'; use {sorted(PROVIDERS)}"}
    if not key:
        return {"ok": False, "error": "key cannot be empty"}
    if provider == "gemini" and not is_plausible_gemini_key(key):
        return {
            "ok": False,
            "error": "That doesn't look like a Gemini API key (expected 'AIza…' or 'AQ.…'). "
            "You may have pasted an OAuth token instead.",
        }
    from config import save_config

    data = save_config({PROVIDERS[provider]: key})
    return {"ok": True, "provider": provider, "configured": [p for p, k in PROVIDERS.items() if data.get(k)]}


def _stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:  # noqa: BLE001
        return False


def prompt_for_key() -> dict:
    """Interactive console prompt. Returns the save_key() result dict."""
    from .logging_config import get_logger  # noqa: F401 — ensure logging is on

    print("")
    print("╔══════════════════════════════════════════════════╗")
    print("║   A.3.T.H.E.R. — FIRST-TIME SETUP                ║")
    print("║   No LLM API key found on this machine.          ║")
    print("╚══════════════════════════════════════════════════╝")
    print("")
    print("Pick your provider:")
    for i, name in enumerate(PROVIDERS, 1):
        print(f"  {i}. {name}")
    print("  (or press Enter to skip and run offline with Ollama)")
    try:
        choice = input("Provider [1-5, Enter to skip]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n[Setup] Skipped — you can run 'python -m core.first_run' anytime.")
        return {"ok": False, "skipped": True}

    if not choice:
        print("[Setup] Skipped — run 'python -m core.first_run' to add a key later.")
        return {"ok": False, "skipped": True}

    # Accept either a number or a provider name.
    try:
        idx = int(choice) - 1
        provider = list(PROVIDERS)[idx]
    except Exception:  # noqa: BLE001
        provider = choice

    try:
        key = getpass.getpass(f"API key for {provider}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[Setup] Cancelled.")
        return {"ok": False, "skipped": True}

    result = save_key(provider, key)
    if result.get("ok"):
        print(f"✔  {provider} key saved to the A3THER data folder.")
    else:
        print(f"✘  {result.get('error')}")
    return result


def maybe_run_setup() -> dict | None:
    """Run the interactive prompt on first launch when safe (tty, not forced off).

    Returns the save result, or None when setup isn't needed / can't prompt.
    """
    if os.environ.get("A3THER_NONINTERACTIVE") == "1":
        return None
    if not needs_setup():
        return None
    if not _stdin_is_tty():
        print("[Setup] No LLM API key configured and stdin is not a terminal —")
        print("[Setup] run 'python -m core.first_run' to add one interactively.")
        return None
    return prompt_for_key()


if __name__ == "__main__":
    # Ensure the data dir + migration exist so the key lands in app-data.
    try:
        from config import paths as _paths

        _paths.migrate_all()
    except Exception:  # noqa: BLE001
        pass
    result = prompt_for_key()
    sys.exit(0 if result.get("ok") else 1)
