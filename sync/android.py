"""
sync/android.py — Android control bridge over ADB.

Gives A3THER real control of connected Android devices (phones, tablets,
TV boxes) through the Android Debug Bridge: unlock, home/back/recent, tap,
swipe, type text, launch apps, and take screenshots.

Safety model
------------
- Commands are built from an explicit allowlist — never from raw user text.
- ``shell=True`` is never used; every argument is passed as a list element.
- Numeric inputs are coerced, package names are regex-validated, and text is
  URL-quoted exactly the way ``adb shell input text`` expects.
- Every control returns a result dict and never raises into the caller.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .logging import log

_PKG_RE = re.compile(r"^[a-zA-Z0-9_.]+$")
_NUM_RE = re.compile(r"^-?\d{1,5}$")

# Wireless auto-discovery cache: finding the phone's IP involves a LAN scan,
# which is slow — cache the successful serial so calls stay snappy.
_WIRELESS: dict = {"serial": None, "tried_at": 0.0}
_WIRELESS_RETRY_SECONDS = 30.0

# Friendly app names → Android package names (spoken/typed "open whatsapp").
_APP_ALIASES = {
    "whatsapp": "com.whatsapp",
    "youtube": "com.google.android.youtube",
    "instagram": "com.instagram.android",
    "spotify": "com.spotify.music",
    "chrome": "com.android.chrome",
    "browser": "com.android.chrome",
    "telegram": "org.telegram.messenger",
    "twitter": "com.twitter.android",
    "x": "com.twitter.android",
    "facebook": "com.facebook.katana",
    "snapchat": "com.snapchat.android",
    "tiktok": "com.zhiliaoapp.musically",
    "netflix": "com.netflix.mediaclient",
    "camera": "com.android.camera",
    "photos": "com.google.android.apps.photos",
    "gallery": "com.google.android.apps.photos",
    "gmail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps",
    "google": "com.google.android.googlequicksearchbox",
    "settings": "com.android.settings",
    "calculator": "com.google.android.calculator",
    "calendar": "com.google.android.calendar",
    "clock": "com.google.android.deskclock",
    "contacts": "com.google.android.contacts",
    "phone": "com.google.android.dialer",
    "dialer": "com.google.android.dialer",
    "messages": "com.google.android.apps.messaging",
    "play store": "com.android.vending",
    "playstore": "com.android.vending",
}

_ADB: str | None = None


def adb_available() -> bool:
    """True when the ``adb`` binary is on PATH.

    Only positive lookups are cached, so installing platform-tools while
    A3THER runs is picked up on the next call (self-healing).
    """
    global _ADB
    if _ADB is None:
        found = shutil.which("adb")
        if found:
            _ADB = found
    return bool(_ADB)


def _run(args: list[str], timeout: float = 10.0) -> dict:
    """One adb invocation; returns {ok, stdout, stderr, error}."""
    if not adb_available():
        return {"ok": False, "error": "adb not found on PATH — install Android platform-tools"}
    try:
        proc = subprocess.run(
            [_ADB] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"adb timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def adb_devices() -> dict:
    """List connected devices (serial + state) from ``adb devices``."""
    result = _run(["devices"])
    if not result["ok"]:
        return result
    devices = []
    for line in result["stdout"].splitlines():
        # "List of devices attached" header has 4+ words; device rows are
        # exactly "<serial> <state>" — that alone filters the header out.
        parts = line.split()
        if len(parts) == 2 and parts[0] != "List":
            devices.append({"serial": parts[0], "state": parts[1]})
    return {"ok": True, "devices": devices, "count": len(devices)}


def find_phone_ips() -> list[str]:
    """Candidate IPs for the user's phone, from the mesh + LAN table.

    Enables ADB-over-WiFi: no USB cable needed once wireless debugging is
    on — A3THER finds the phone on the network and ``adb connect``s to it.
    """
    ips: list[str] = []
    # 1) Mesh nodes that joined the phone-link page are definitely phones.
    try:
        from sync.mesh import get_mesh_registry

        for node in get_mesh_registry().nodes():
            kind = (node.profile.kind or "").lower()
            if kind in ("android", "iphone", "ipad"):
                raw = node.profile.platform or ""
                import re as _re

                m = _re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", raw)
                if m and m.group(1) not in ips:
                    ips.append(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    # 2) Every LAN host — try adb connect on each (cheap, timeout-guarded).
    try:
        from backend.services.device_service import get_lan_hosts

        for host in get_lan_hosts().get("devices", []):
            ip = host.get("ip")
            if ip and ip not in ips:
                ips.append(ip)
    except Exception:  # noqa: BLE001
        pass
    return ips


def _adb_path() -> str | None:
    """adb from PATH, or the one bundled with auto-installed scrcpy."""
    if adb_available():
        return _ADB
    try:
        from .cast import _find_scrcpy_exe

        exe = _find_scrcpy_exe()
        if exe:
            bundled = Path(exe).parent / "adb.exe"
            if bundled.exists():
                return str(bundled)
    except Exception:  # noqa: BLE001
        pass
    return None


def connect_wireless(ip: str | None = None, port: int = 5555) -> dict:
    """Connect to the phone over WiFi via ``adb connect`` (no USB cable).

    ``ip`` auto-discovers from mesh/LAN when omitted. Returns honest
    results: connected / already / not_found / needs_setup.
    """
    binary = _adb_path()
    if not binary:
        return {"ok": False, "error": "adb not found — install scrcpy (bundles adb) or Android platform-tools"}
    # Use the resolved binary (PATH or scrcpy-bundled).
    def _raw(args: list[str], timeout: float = 8.0) -> dict:
        try:
            proc = subprocess.run([binary] + args, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
            return {"ok": proc.returncode == 0, "stdout": (proc.stdout or "").strip(),
                    "stderr": (proc.stderr or "").strip()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if ip:
        candidates = [str(ip).strip()]
    else:
        candidates = find_phone_ips()
    if not candidates:
        return {
            "ok": False,
            "needs_setup": True,
            "error": "no phone found on the network — open the phone-link page on your phone "
            "(or enable Wireless debugging in Developer options), then retry",
        }

    tried = []
    for candidate in candidates[:20]:
        target = f"{candidate}:{port}"
        tried.append(target)
        # Skip candidates that are clearly not this LAN (gateway/router).
        if candidate.endswith(".1") or candidate.endswith(".255"):
            continue
        # Skip if already connected.
        existing = adb_devices()
        if any(d.get("serial") == target and d.get("state") == "device" for d in existing.get("devices", [])):
            return {"ok": True, "connected": True, "already": True, "serial": target}
        result = _raw(["connect", target], timeout=3)
        if result.get("ok"):
            out = (result.get("stdout") or "").lower()
            if "connected" in out or "already" in out:
                return {"ok": True, "connected": True, "serial": target}
        if "cannot connect" in (result.get("stdout") or "").lower():
            # Connection refused usually means ADB-over-WiFi is off on the phone.
            continue
    return {
        "ok": False,
        "needs_setup": True,
        "error": "couldn't reach the phone over WiFi (tried " + ", ".join(tried[:6]) + ") — "
        "enable Wireless debugging in Developer options (or plug in USB once for the pattern)",
    }


def _pick_serial(serial: str | None) -> str | None:
    """Validate/choose a serial. Returns None when none usable.

    Falls back to ADB-over-WiFi: when no USB device is plugged, it tries to
    ``adb connect`` to the phone's discovered IP so control keeps working
    without a cable (USB only needed once, to enable Wireless debugging).
    """
    if serial:
        return serial
    info = adb_devices()
    if info.get("ok") and info["devices"]:
        return info["devices"][0]["serial"]

    # No USB device → try wireless (cached, retried every N seconds).
    now = time.time()
    if _WIRELESS["serial"] and now - _WIRELESS["tried_at"] < _WIRELESS_RETRY_SECONDS:
        return _WIRELESS["serial"]
    _WIRELESS["tried_at"] = now
    result = connect_wireless()
    if result.get("ok") and result.get("serial"):
        _WIRELESS["serial"] = result["serial"]
        return result["serial"]
    _WIRELESS["serial"] = None
    return None


def _screen_size(serial: str) -> tuple[int, int] | None:
    """(width, height) from ``wm size`` — used to map pattern dots to pixels."""
    result = _run(["-s", serial, "shell", "wm", "size"])
    for line in result.get("stdout", "").splitlines():
        m = re.search(r"(\d+)x(\d+)", line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def _is_locked(serial: str) -> bool | None:
    """Lockscreen state: True=locked, False=unlocked, None=can't determine."""
    result = _run(["-s", serial, "shell", "dumpsys", "window"])
    out = result.get("stdout", "") or ""
    showing = re.search(r"mShowingLockscreen=(true|false)", out)
    dreaming = re.search(r"mDreamingLockscreen=(true|false)", out)
    if not showing and not dreaming:
        return None
    locked = False
    if showing:
        locked = locked or showing.group(1) == "true"
    if dreaming:
        locked = locked or dreaming.group(1) == "true"
    return locked


# --------------------------------------------------------------------------- #
# Phone unlock with remembered PIN / pattern
# --------------------------------------------------------------------------- #
_PATTERN_DOTS = {
    # 3x3 grid, dot 0-8 → fractional (x, y) of screen size.
    0: (0.20, 0.42), 1: (0.50, 0.42), 2: (0.80, 0.42),
    3: (0.20, 0.62), 4: (0.50, 0.62), 5: (0.80, 0.62),
    6: (0.20, 0.82), 7: (0.50, 0.82), 8: (0.80, 0.82),
}


def _wake_and_show_keyguard(serial: str, size: tuple[int, int] | None) -> dict:
    """Wake the screen and swipe up to reveal the PIN/pattern pad."""
    wake = _run(["-s", serial, "shell", "input", "keyevent", "82"])  # menu = wake
    width, height = size or (1080, 2400)
    swipe = _run([
        "-s", serial, "shell", "input", "swipe",
        str(width // 2), str(int(height * 0.8)),
        str(width // 2), str(int(height * 0.2)), "250",
    ])
    return {"wake_ok": bool(wake.get("ok")), "swipe_ok": bool(swipe.get("ok"))}


def unlock_phone(serial: str | None = None) -> dict:
    """Unlock an Android phone using its remembered PIN or pattern.

    Flow: wake → swipe up → enter stored credential → verify. Honest
    outcomes:

    - already unlocked                 → ``already_unlocked``
    - no secret remembered             → ``need_secret`` (ask the user)
    - credential entered + verified    → ``unlocked``
    - credential wrong / still locked  → ``wrong_secret`` (user unlocks again)
    """
    serial = _pick_serial(serial)
    if not serial:
        return {"ok": False, "error": "no Android device connected (adb devices)"}

    try:
        from .phone_vault import get_secret
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "phone vault unavailable"}

    locked = _is_locked(serial)
    if locked is False:
        return {"ok": True, "unlocked": True, "already_unlocked": True, "serial": serial}

    secret = get_secret(serial) or get_secret(None)  # device key, then default
    if not secret:
        return {
            "ok": False,
            "need_secret": True,
            "serial": serial,
            "error": "phone is locked and I don't have its PIN or pattern yet — "
            "tell me: my pin is 1234 (or: my pattern is 1-5-9)",
        }

    size = _screen_size(serial)
    _wake_and_show_keyguard(serial, size)
    entered, note = _enter_secret(serial, secret, size)
    if not entered:
        return {
            "ok": False,
            "wrong_secret": True,
            "serial": serial,
            "method": secret["kind"],
            "error": "that PIN/pattern didn't unlock it — unlock your phone again "
            "on the screen, or tell me the correct pin/pattern and I'll remember it",
        }
    still_locked = _is_locked(serial)
    if still_locked is None:
        return {
            "ok": True, "unlocked": True, "verified": False,
            "serial": serial, "method": secret["kind"],
            "note": "entered credential; lockscreen state could not be verified",
        }
    return {"ok": True, "unlocked": True, "verified": True, "serial": serial, "method": secret["kind"]}


def _enter_secret(serial: str, secret: dict, size: tuple[int, int] | None) -> tuple[bool, str]:
    """Enter a remembered PIN/pattern on the unlocked keyguard.

    Returns ``(entered_ok, note)``. Never raises — every adb call degrades
    to a failed entry so callers can report honestly.
    """
    kind, value = secret["kind"], secret["value"]
    base = ["-s", serial, "shell", "input"]
    if kind == "pin":
        _run(base + ["text", quote(value, safe="")])
        _run(base + ["keyevent", "66"])  # Enter
    else:
        try:
            # 1-9 keypad notation → 0-8 grid coordinates.
            dots = [int(p) - 1 for p in value.split("-")]
        except Exception:  # noqa: BLE001
            dots = []
        width, height = size or (1080, 2400)
        points = [
            (int(width * _PATTERN_DOTS[d][0]), int(height * _PATTERN_DOTS[d][1]))
            for d in dots if d in _PATTERN_DOTS
        ]
        for i in range(len(points) - 1):
            x1, y1 = points[i]
            x2, y2 = points[i + 1]
            _run(["-s", serial, "shell", "input", "swipe",
                  str(x1), str(y1), str(x2), str(y2), "120"])  # quick, pattern-like
            time.sleep(0.08)
    time.sleep(1.0)
    if _is_locked(serial) is True:
        return False, "credential rejected by the phone"
    return True, "credential entered"


def _ensure_unlocked(serial: str) -> tuple[bool, str]:
    """Best-effort auto-unlock before a screen-needing command.

    Returns ``(did_unlock, note)``. If the phone is already unlocked, or no
    credential is remembered, it does nothing (the caller still runs its
    command — the note explains the state honestly).
    """
    if _is_locked(serial) is not True:
        return False, ""
    try:
        from .phone_vault import get_secret

        secret = get_secret(serial) or get_secret(None)
    except Exception:  # noqa: BLE001
        return False, "vault unavailable"
    if not secret:
        return False, "phone locked (no PIN/pattern remembered — tell me: my pin is 1234)"
    size = _screen_size(serial)
    _wake_and_show_keyguard(serial, size)
    entered, _ = _enter_secret(serial, secret, size)
    if entered:
        return True, "auto-unlocked with remembered PIN/pattern"
    return False, "auto-unlock failed — wrong PIN/pattern, unlock the phone again on screen"


def remember_secret(kind: str, value: str, serial: str | None = None) -> dict:
    """Remember a PIN/pattern for the connected device (or 'default')."""
    device_key = _pick_serial(serial) or "default"
    try:
        from .phone_vault import save_secret

        return save_secret(device_key, kind, value)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# Allowlisted controls
# --------------------------------------------------------------------------- #

def control(action: str, params: dict | None = None) -> dict:
    """Execute an allowlisted Android control.

    ``params`` may include ``serial``, ``x``/``y``, ``x1``…, ``text``,
    ``package``, ``url``, ``code``, ``ms``.
    """
    params = dict(params or {})
    action = (action or "").lower()

    # Device-independent actions run before any serial requirement.
    if action == "forget_secret":
        try:
            from .phone_vault import delete_secret

            return delete_secret(str(params.get("serial") or "") or "default")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if action == "vault_status":
        try:
            from .phone_vault import status

            return status()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if action == "devices":
        return adb_devices()

    serial = _pick_serial(params.get("serial") or None)
    if not serial:
        return {"ok": False, "error": "no Android device connected (adb devices)"}

    base = ["-s", serial]

    def num(value: Any, name: str) -> int | None:
        text = str(value or "")
        return int(text) if _NUM_RE.match(text) else None

    # Auto-unlock guard: screen-needing commands unlock the phone first using
    # the remembered PIN/pattern (when the caller didn't disable it).
    def with_unlock(result_fn):
        do_unlock = bool(params.get("auto_unlock", True))
        did, note = _ensure_unlocked(serial) if do_unlock else (False, "")
        result = result_fn()
        if isinstance(result, dict):
            result["auto_unlock"] = did
            if note and not did:
                result.setdefault("note", note)
        return result

    # -- input keyevent helpers ---------------------------------------------- #
    if action == "unlock":
        # Menu key wakes the screen; dismiss-keyguard clears the lockscreen.
        # Report the real result — never hardcode success when adb failed.
        wake = _run(base + ["shell", "input", "keyevent", "82"])
        dismiss = _run(base + ["shell", "wm", "dismiss-keyguard"])
        return {
            "ok": bool(wake.get("ok") and dismiss.get("ok")),
            "action": "unlock",
            "serial": serial,
            "error": dismiss.get("error") or wake.get("error"),
        }
    if action == "lock":
        return {"ok": True, **_run(base + ["shell", "input", "keyevent", "26"])}  # power
    if action == "home":
        return {"ok": True, **_run(base + ["shell", "input", "keyevent", "3"])}
    if action == "back":
        return {"ok": True, **_run(base + ["shell", "input", "keyevent", "4"])}
    if action == "recent":
        return {"ok": True, **_run(base + ["shell", "input", "keyevent", "187"])}
    if action == "key":
        code = num(params.get("code"), "code")
        if code is None:
            return {"ok": False, "error": "key requires a numeric 'code' (e.g. 26=power, 82=menu)"}
        return {"ok": True, **_run(base + ["shell", "input", "keyevent", str(code)])}

    # -- input touch ----------------------------------------------------------- #
    if action == "tap":
        x, y = num(params.get("x"), "x"), num(params.get("y"), "y")
        if x is None or y is None:
            return {"ok": False, "error": "tap requires numeric x and y"}
        return with_unlock(lambda: {"ok": True, **_run(base + ["shell", "input", "tap", str(x), str(y)])})
    if action == "swipe":
        x1, y1 = num(params.get("x1"), "x1"), num(params.get("y1"), "y1")
        x2, y2 = num(params.get("x2"), "x2"), num(params.get("y2"), "y2")
        if None in (x1, y1, x2, y2):
            return {"ok": False, "error": "swipe requires x1 y1 x2 y2"}
        ms = num(params.get("ms"), "ms") or 300
        return with_unlock(lambda: {"ok": True, **_run(base + ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms)])})

    # -- text / open ------------------------------------------------------------- #
    if action == "text":
        text = str(params.get("text") or "")
        if not text:
            return {"ok": False, "error": "text requires a 'text' value"}
        # adb shell input text wants URL-encoding (spaces → %s).
        return with_unlock(lambda: {"ok": True, **_run(base + ["shell", "input", "text", quote(text, safe="")])})
    if action == "open":
        package = str(params.get("package") or params.get("pkg") or params.get("app") or "")
        if not _PKG_RE.match(package):
            # Friendly name → package; unknown names get an honest error.
            alias = _APP_ALIASES.get(package.lower().strip())
            if not alias:
                return {
                    "ok": False,
                    "error": f"unknown app '{package}' — use a package name "
                    f"(e.g. com.whatsapp) or one of: {', '.join(sorted(_APP_ALIASES))}",
                }
            package = alias
        return with_unlock(lambda: {"ok": True, **_run(base + ["shell", "monkey", "-p", package, "1"], timeout=15)})
    if action == "open_url":
        url = str(params.get("url") or "")
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "open_url requires an http(s) url"}
        intent = f"am start -a android.intent.action.VIEW -d {quote(url, safe=':/?&=.#%+')}"
        return with_unlock(lambda: {"ok": True, **_run(base + ["shell", intent], timeout=15)})

    # -- screenshot ---------------------------------------------------------------- #
    if action == "screenshot":
        try:
            from config.paths import data_path

            dest = data_path(f"android_{int(time.time())}.png")
        except Exception:  # noqa: BLE001
            dest = None
        if dest is None:
            return {"ok": False, "error": "could not resolve screenshot destination"}
        def _shot() -> dict:
            proc = subprocess.run(
                [_ADB] + base + ["exec-out", "screencap", "-p"],
                capture_output=True, timeout=15,
            )
            if proc.returncode != 0 or not proc.stdout:
                return {"ok": False, "error": "screencap failed", "stderr": proc.stderr[:200]}
            dest.write_bytes(proc.stdout)
            return {"ok": True, "path": str(dest), "bytes": len(proc.stdout)}

        return with_unlock(_shot)

    # -- phone unlock + secret memory ----------------------------------------- #
    if action == "unlock_phone":
        return unlock_phone(serial)
    if action == "remember_secret":
        kind = str(params.get("kind") or "pin").lower()
        value = str(params.get("value") or params.get("secret") or "")
        if not value:
            return {"ok": False, "error": "remember_secret requires a 'value' (and 'kind' pin|pattern)"}
        return remember_secret(kind, value, serial)
    if action == "status":
        info = adb_devices()
        battery = _run(base + ["shell", "dumpsys", "battery"])
        pct = None
        for line in battery.get("stdout", "").splitlines():
            if "level:" in line:
                try:
                    pct = int(line.split(":", 1)[1].strip())
                except Exception:  # noqa: BLE001
                    pass
        return {"ok": True, "serial": serial, "devices": info.get("devices", []), "battery_percent": pct}

    return {"ok": False, "error": f"unknown android action '{action}'"}


def describe() -> dict:
    """Status block for the HUD / terminal."""
    available = adb_available()
    info = adb_devices() if available else {"devices": []}
    return {
        "available": available,
        "connected": info.get("count", 0),
        "devices": info.get("devices", []),
        "actions": ["devices", "status", "unlock", "lock", "home", "back", "recent",
                    "key", "tap", "swipe", "text", "open", "open_url", "screenshot",
                    "unlock_phone", "remember_secret", "forget_secret", "vault_status"],
    }
