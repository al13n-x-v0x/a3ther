"""
crypto.py — local at-rest encryption for sensitive memory fields.

Guidelines implemented here:

- AES-128/256 via Fernet (``cryptography``, already a dependency).
- The key comes from ``A3THER_MEMORY_KEY`` (env, preferred) or a local
  key file ``memory/.memory_key`` created on first use with owner-only
  permissions (chmod 600 on POSIX).
- Never store raw secrets in plain text: sensitive values are wrapped
  with ``encrypt_str`` before hitting disk.

NOTE: encryption protects against casual file reads, not against an
attacker running as the same user (the key lives on the same machine).
For higher assurance, put the key in the OS keychain or an HSM.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from config import base_dir, get_env

LOGGER = logging.getLogger("a3ther.memory")

KEY_FILE = base_dir() / "memory" / ".memory_key"
_CRYPTO_OK = False
_fernet = None


def _ensure_key() -> bytes:
    key = get_env("A3THER_MEMORY_KEY")
    if key:
        return key.encode("utf-8")

    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()

    from cryptography.fernet import Fernet

    generated = Fernet.generate_key()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(generated)
    try:
        os.chmod(KEY_FILE, 0o600)
    except Exception:  # noqa: BLE001 — Windows has no chmod
        pass
    LOGGER.info("Generated new memory encryption key at %s", KEY_FILE)
    return generated


def _box():
    global _fernet, _CRYPTO_OK
    if _fernet is None:
        try:
            from cryptography.fernet import Fernet

            _fernet = Fernet(_ensure_key())
            _CRYPTO_OK = True
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Encryption unavailable (%s) — storing plaintext", exc)
            _CRYPTO_OK = False
    return _fernet


def encrypt_str(value: str) -> str:
    """Return an ``enc:v1:...`` token, or the plaintext if crypto is missing."""
    if not value:
        return value
    box = _box()
    if box is None:
        return value
    try:
        return "enc:v1:" + box.encrypt(value.encode("utf-8")).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Encrypt failed: %s", exc)
        return value


def decrypt_str(token: str) -> str:
    """Reverse :func:`encrypt_str` (plaintext passes through unchanged)."""
    if not token or not token.startswith("enc:v1:"):
        return token
    box = _box()
    if box is None:
        return token
    try:
        return box.decrypt(token[len("enc:v1:"):].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001
        return token
