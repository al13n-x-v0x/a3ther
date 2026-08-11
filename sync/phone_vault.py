"""
sync/phone_vault.py — remembers a phone's PIN / unlock pattern.

When the user says "my pin is 1234" (or pattern "1-5-9"), A3THER stores it
here and reuses it the next time they say "unlock my phone" — entering the
credential automatically over ADB. If the credential is wrong, the unlock
flow reports it and asks the user to unlock again (no face unlock).

Storage
-------
The vault is a JSON file in the A3THER app-data dir
(``%LOCALAPPDATA%/A3THER/phone_secrets.json``). Values are NOT plaintext:
each secret is obfuscated with an XOR key derived from the machine + app,
so the file is unreadable without this install. Treat this as convenience
obfuscation, not strong encryption — the phone itself remains the real
security boundary.

Secrets are keyed by device (adb serial, or "default" when unknown), so
multiple phones keep separate credentials.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import platform
import threading
import time
import uuid
from pathlib import Path

from config.paths import data_path

LOGGER = logging.getLogger("a3ther.phone_vault")

_VALID_KINDS = ("pin", "pattern")
_VALIDATION = {
    "pin": lambda v: v.isdigit() and 4 <= len(v) <= 8,
    # Patterns accept the natural 1-9 keypad notation (1-5-9); the unlock
    # routine normalises to the 0-8 grid coordinates internally.
    "pattern": lambda v: all(p.isdigit() and 1 <= int(p) <= 9 for p in v.split("-")) and "-" in v,
}

_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Obfuscation (XOR + base64, keyed per machine + install)
# --------------------------------------------------------------------------- #
def _obf_key() -> bytes:
    raw = f"a3ther-vault::{platform.node()}::{uuid.getnode()}::a3ther"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _obfuscate(plain: str) -> str:
    key = _obf_key()
    data = plain.encode("utf-8")
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.b64encode(out).decode("ascii")


def _deobfuscate(blob: str) -> str:
    key = _obf_key()
    data = base64.b64decode(blob.encode("ascii"))
    out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return out.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _vault_path() -> Path:
    return data_path("phone_secrets.json")


def _load_raw() -> dict:
    path = _vault_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — corrupt vault → start clean
            LOGGER.warning("phone_secrets.json unreadable — starting fresh")
    return {}


def _save_raw(data: dict) -> None:
    path = _vault_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_secret(device_key: str, kind: str, value: str) -> dict:
    """Store (or update) a PIN/pattern for a device. Returns a result dict."""
    kind = (kind or "").strip().lower()
    value = (value or "").strip()
    device_key = (device_key or "default").strip() or "default"

    if kind not in _VALID_KINDS:
        return {"ok": False, "error": f"kind must be one of {_VALID_KINDS}"}
    check = _VALIDATION[kind]
    if not check(value):
        if kind == "pin":
            return {"ok": False, "error": "a PIN must be 4-8 digits (e.g. 1234)"}
        return {"ok": False, "error": "a pattern must be dot numbers 1-9 separated by '-' (e.g. 1-5-9)"}

    with _LOCK:
        data = _load_raw()
        data[device_key] = {
            "kind": kind,
            "blob": _obfuscate(value),
            "updated": time.time(),
        }
        _save_raw(data)
    return {"ok": True, "device": device_key, "kind": kind, "remembered": True}


def get_secret(device_key: str | None = None) -> dict | None:
    """Return {kind, value} for a device (or the 'default' entry)."""
    device_key = (device_key or "").strip() or "default"
    with _LOCK:
        data = _load_raw()
        entry = data.get(device_key) or data.get("default")
        if not entry:
            return None
        try:
            return {"kind": entry["kind"], "value": _deobfuscate(entry["blob"])}
        except Exception:  # noqa: BLE001
            return None


def delete_secret(device_key: str) -> dict:
    device_key = (device_key or "").strip() or "default"
    with _LOCK:
        data = _load_raw()
        removed = data.pop(device_key, None) is not None
        if not removed:
            removed = data.pop("default", None) is not None
        if removed:
            _save_raw(data)
    return {"ok": True, "device": device_key, "removed": removed}


def status() -> dict:
    """Which devices have a remembered secret (never the values themselves)."""
    with _LOCK:
        data = _load_raw()
    entries = []
    for key, entry in data.items():
        entries.append({
            "device": key,
            "kind": entry.get("kind"),
            "updated": entry.get("updated"),
        })
    return {"count": len(entries), "entries": entries}
