"""
sync/cast.py — see your phone's screen on the laptop.

Two casting modes, both over ADB (USB or wireless debugging):

1. **Full cast (scrcpy)** — when ``scrcpy`` is installed, a real mirrored
   window opens on the laptop and you can drive the phone with mouse +
   keyboard. This is the "u see the screen, u cast it on laptop" flow.
2. **Lite cast (HUD frame)** — always available: a background thread pulls
   ``screencap`` frames every ~1.5 s and serves the latest one to the HUD
   at ``/api/sync/cast/frame.png``, so the dashboard shows the live screen
   even without scrcpy installed.

The flow the user asked for: Settings → PHONE LINK → it checks whether a
phone is connected over USB → asks "did you connect the device with USB?"
→ on confirmation it remembers your password (PIN/pattern) and starts the
cast. Wrong credential → it asks you to unlock again on the phone.

Safety: subprocesses are allowlisted (adb/scrcpy only), a cap on frame
rate and bytes, and every failure degrades to an honest message.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from .android import _pick_serial, adb_available, adb_devices
from .phone_vault import get_secret

LOGGER = logging.getLogger("a3ther.sync.cast")

_FRAME_INTERVAL = 1.5          # seconds between screencap pulls (lite cast)
_MAX_FRAME_BYTES = 6 * 1024 * 1024

_STATE = {
    "running": False,
    "mode": None,               # "scrcpy" | "lite"
    "serial": None,
    "scrcpy_installed": False,
    "adb_connected": False,
    "usb_confirmed": False,
    "last_frame": None,         # Path to the latest PNG
    "frame_age": 0.0,
    "started_at": 0.0,
    "error": "",
}
_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_SCRCPY_PROC: subprocess.Popen | None = None


# --------------------------------------------------------------------------- #
# Per-device screen grabs (Control Phone panel)
# --------------------------------------------------------------------------- #
_SCREEN_CACHE: dict[str, tuple[float, bytes]] = {}
_SCREEN_TTL = 1.2          # seconds — serve cached frame instead of re-capturing
_MODEL_CACHE: dict[str, str] = {}


def screencap_now(serial: str) -> bytes | None:
    """One-shot ``screencap -p`` for a device, cached briefly per serial.

    Used by the Control Phone panel to show live thumbnails for every
    connected Android — USB or wireless — without starting a full cast.
    """
    now = time.time()
    with _LOCK:
        hit = _SCREEN_CACHE.get(serial)
        if hit and now - hit[0] < _SCREEN_TTL:
            return hit[1]
    ok, data = _adb(["-s", serial, "exec-out", "screencap", "-p"])
    if not ok or not data or len(data) < 100 or len(data) > _MAX_FRAME_BYTES:
        return None
    with _LOCK:
        _SCREEN_CACHE[serial] = (now, data)
        if len(_SCREEN_CACHE) > 8:  # prune stale entries
            for k in list(_SCREEN_CACHE):
                if now - _SCREEN_CACHE[k][0] > 30:
                    _SCREEN_CACHE.pop(k, None)
    return data


def device_model(serial: str) -> str:
    """Best-effort model name for a device (cached; never raises)."""
    if serial in _MODEL_CACHE:
        return _MODEL_CACHE[serial]
    model = serial
    try:
        ok, out = _adb(["-s", serial, "shell", "getprop", "ro.product.model"])
        if ok:
            model = out.decode("utf-8", "replace").strip() or serial
    except Exception:  # noqa: BLE001
        pass
    _MODEL_CACHE[serial] = model
    return model


def list_screen_devices() -> list[dict]:
    """Every ADB-connected Android with a live-screen capability.

    Returns serial/model/connection (usb | wifi) per device so the Control
    Phone panel can show thumbnails + badges for all of them.
    """
    devices = adb_devices().get("devices") or []
    out = []
    for d in devices:
        if d.get("state") != "device":
            continue
        serial = d["serial"]
        conn = "wifi" if ":" in serial else "usb"
        out.append(
            {
                "serial": serial,
                "model": device_model(serial),
                "connection": conn,
                "has_screen": True,
            }
        )
    return out


def _adb_binary() -> str | None:
    """adb from PATH, or the one bundled with auto-installed scrcpy."""
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    exe = _find_scrcpy_exe()
    if exe:
        bundled = Path(exe).parent / "adb.exe"
        if bundled.exists():
            return str(bundled)
    return None


def _adb(args: list[str], timeout: float = 12.0) -> tuple[bool, bytes]:
    binary = _adb_binary()
    if not binary:
        return False, b"adb not found"
    try:
        proc = subprocess.run([binary] + args, capture_output=True, timeout=timeout)
        return proc.returncode == 0, proc.stdout
    except Exception as exc:  # noqa: BLE001
        return False, str(exc).encode()


def adb_usb_connected() -> dict:
    """Is an Android device connected (over USB or wireless debugging)?"""
    binary = _adb_binary()
    if not binary:
        return {"connected": False, "error": "adb not found — install scrcpy (bundles adb) or Android platform-tools"}
    ok, out = _adb(["devices"])
    if not ok:
        return {"connected": False, "error": "adb devices failed"}
    devices = []
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] != "List":
            devices.append({"serial": parts[0], "state": parts[1]})
    # USB devices are the non-TCP ones (no 5555-style port in the serial).
    usb = [d["serial"] for d in devices if ":" not in d["serial"]]
    return {
        "connected": bool(devices),
        "usb": usb,
        "wireless": [d["serial"] for d in devices if ":" in d["serial"]],
        "devices": [d["serial"] for d in devices],
        "count": len(devices),
    }


# --------------------------------------------------------------------------- #
# scrcpy auto-install (Windows) — download the release zip, no manual setup.
# --------------------------------------------------------------------------- #
def _scrcpy_dir() -> Path | None:
    """A REAL folder for the scrcpy binary (native exe, no AppData trickery).

    Microsoft-Store Python virtualises ``%LOCALAPPDATA%`` writes: Python
    "sees" the extracted scrcpy but the native scrcpy.exe cannot read its
    own DLLs there. Prefer ``~/Videos/A3THER/scrcpy`` — real for both
    Python and native processes — and fall back to the app-data folder.
    """
    candidates = [
        Path.home() / "Videos" / "A3THER" / "scrcpy",
        Path.cwd() / ".cache" / "scrcpy",
    ]
    try:
        from config.paths import data_path

        candidates.append(data_path("scrcpy"))
    except Exception:  # noqa: BLE001
        pass
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            return base
        except Exception:  # noqa: BLE001
            continue
    return None


def _find_scrcpy_exe() -> str | None:
    """Locate a usable scrcpy.exe: PATH first, then the auto-installed copy."""
    on_path = shutil.which("scrcpy")
    if on_path:
        return on_path
    base = _scrcpy_dir()
    if base is not None and base.is_dir():
        # The zip extracts into a scrcpy-win64-vX.Y/ folder.
        matches = sorted(base.glob("scrcpy-win64-*/scrcpy.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return str(matches[0])
    return None


# Download size limit + chunked write so a flaky connection doesn't corrupt.
_SCRCPY_DOWNLOAD_TIMEOUT = 600
_SCRCPY_MAX_BYTES = 200 * 1024 * 1024


def install_scrcpy(force: bool = False) -> dict:
    """Download + extract the latest scrcpy release (Windows) automatically.

    Runs synchronously and returns an honest result: ``installed`` with the
    exe path, or ``error`` with the reason (offline, GitHub down, …). The
    cast flow calls this when scrcpy is missing so "Start Cast" always ends
    up with a mirrored window.
    """
    if not force:
        existing = _find_scrcpy_exe()
        if existing:
            return {"ok": True, "installed": True, "exe": existing, "note": "already installed"}

    base = _scrcpy_dir()
    if base is None:
        return {"ok": False, "error": "could not resolve a data folder for scrcpy"}

    try:
        # 1) resolve the latest release asset URL via the GitHub API
        with urllib.request.urlopen(
            "https://api.github.com/repos/Genymobile/scrcpy/releases/latest",
            timeout=30,
        ) as resp:
            import json

            release = json.loads(resp.read().decode("utf-8"))
        asset_url = None
        asset_name = None
        for asset in release.get("assets") or []:
            name = asset.get("name", "")
            if name.startswith("scrcpy-win64-v") and name.endswith(".zip"):
                asset_url = asset.get("browser_download_url")
                asset_name = name
                break
        if not asset_url:
            return {"ok": False, "error": "could not find the scrcpy Windows release zip on GitHub"}

        # 2) download with a size guard
        print(f"[CAST] Downloading scrcpy ({asset_name}) — one-time, ~30 MB…")
        zip_path = base / asset_name
        tmp = zip_path.with_suffix(".zip.part")
        with urllib.request.urlopen(asset_url, timeout=_SCRCPY_DOWNLOAD_TIMEOUT) as resp, tmp.open("wb") as out:
            total = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > _SCRCPY_MAX_BYTES:
                    tmp.unlink(missing_ok=True)
                    return {"ok": False, "error": "scrcpy download exceeded the size guard"}
                out.write(chunk)
        tmp.rename(zip_path)

        # 3) extract (zip contains a scrcpy-win64-vX.Y/ folder)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(base)
        zip_path.unlink(missing_ok=True)

        exe = _find_scrcpy_exe()
        if not exe:
            return {"ok": False, "error": "scrcpy downloaded but the executable was not found in the archive"}
        with _LOCK:
            _STATE["scrcpy_installed"] = True
        return {"ok": True, "installed": True, "exe": exe, "note": f"installed scrcpy ({asset_name})"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"scrcpy auto-install failed: {type(exc).__name__}: {exc}"}


def start_cast(serial: str | None = None, prefer_scrcpy: bool = True) -> dict:
    """Start casting the connected phone's screen.

    Uses scrcpy (mirrored window) when available — auto-downloading it on
    first use — otherwise the HUD lite stream. Returns the state dict.
    """
    global _THREAD, _SCRCPY_PROC
    with _LOCK:
        if _STATE["running"]:
            return dict(_STATE)
        if not (_adb_binary() or adb_available()):
            _STATE["error"] = "adb not found — install scrcpy (bundles adb) or Android platform-tools"
            return dict(_STATE)
        info = adb_devices()
        if not info.get("devices"):
            # No USB phone — try ADB-over-WiFi before giving up.
            try:
                from .android import connect_wireless

                wl = connect_wireless()
                if wl.get("ok") and wl.get("serial"):
                    serial = wl["serial"]
                else:
                    _STATE["error"] = (
                        wl.get("error")
                        or "no phone reachable — plug in via USB once to enable Wireless debugging, "
                        "then it connects over WiFi automatically"
                    )
                    return dict(_STATE)
            except Exception:  # noqa: BLE001
                _STATE["error"] = "no Android device connected — plug it in via USB (enable USB debugging)"
                return dict(_STATE)
        else:
            serial = _pick_serial(serial) or info["devices"][0]["serial"]
        _STATE["serial"] = serial
        _STATE["adb_connected"] = True
        _STATE["error"] = ""
        _STOP.clear()

    scrcpy = _find_scrcpy_exe()
    if not scrcpy and prefer_scrcpy:
        install = install_scrcpy()
        if install.get("ok"):
            scrcpy = install.get("exe")
        else:
            # Honest note in the state; lite cast still works.
            with _LOCK:
                _STATE["error"] = install.get("error", "scrcpy auto-install failed")
    with _LOCK:
        _STATE["scrcpy_installed"] = bool(scrcpy)
        _STATE["mode"] = "scrcpy" if (scrcpy and prefer_scrcpy) else "lite"

    if _STATE["mode"] == "scrcpy":
        try:
            _SCRCPY_PROC = subprocess.Popen(
                [scrcpy, "-s", serial, "--stay-awake"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _STATE["running"] = True
            _STATE["started_at"] = time.time()
            return dict(_STATE)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("scrcpy failed (%s) — falling back to lite stream", exc)
            _STATE["mode"] = "lite"

    # Lite cast: background screencap thread.
    _THREAD = threading.Thread(target=_frame_loop, args=(serial,), daemon=True, name="phone-cast")
    _THREAD.start()
    with _LOCK:
        _STATE["running"] = True
        _STATE["started_at"] = time.time()
    return dict(_STATE)


def stop_cast() -> dict:
    """Stop the cast (kill scrcpy window / stop the frame thread)."""
    global _SCRCPY_PROC
    _STOP.set()
    if _SCRCPY_PROC is not None:
        try:
            _SCRCPY_PROC.kill()
        except Exception:  # noqa: BLE001
            pass
        _SCRCPY_PROC = None
    with _LOCK:
        _STATE["running"] = False
        _STATE["mode"] = None
        _STATE["error"] = ""
    return dict(_STATE)


def _frame_loop(serial: str) -> None:
    try:
        from config.paths import data_path

        base = data_path("cast")
        base.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        base = Path.home() / "Videos" / "A3THER" / "cast"
        base.mkdir(parents=True, exist_ok=True)

    frame_path = base / "phone_frame.png"
    while not _STOP.is_set():
        ok, data = _adb(["-s", serial, "exec-out", "screencap", "-p"])
        if ok and data and len(data) > 100 and len(data) < _MAX_FRAME_BYTES:
            try:
                frame_path.write_bytes(data)
                with _LOCK:
                    _STATE["last_frame"] = str(frame_path)
                    _STATE["frame_age"] = time.time()
                    _STATE["error"] = ""
            except Exception as exc:  # noqa: BLE001
                with _LOCK:
                    _STATE["error"] = f"frame write failed: {exc}"
        else:
            # Phone may be locked / screen off — keep trying.
            with _LOCK:
                _STATE["error"] = "frame capture failed — is the phone unlocked and USB debugging on?"
        if _STOP.wait(_FRAME_INTERVAL):
            break
    with _LOCK:
        _STATE["running"] = False


def confirm_usb(serial: str | None = None) -> dict:
    """The 'did you connect the device with USB?' confirmation.

    Marks the connection as confirmed and, when a PIN/pattern is already
    remembered, auto-unlocks the phone so the cast starts on the home
    screen instead of the lockscreen.
    """
    if not adb_available():
        return {"ok": False, "error": "adb not found on PATH"}
    serial = _pick_serial(serial)
    if not serial:
        return {"ok": False, "error": "no Android device connected — plug it in via USB"}
    with _LOCK:
        _STATE["usb_confirmed"] = True
        _STATE["serial"] = serial
    secret = get_secret(serial) or get_secret(None)
    unlocked = False
    note = ""
    if secret:
        try:
            from .android import unlock_phone

            res = unlock_phone(serial)
            unlocked = bool(res.get("unlocked"))
            note = res.get("error") or res.get("note") or ""
        except Exception as exc:  # noqa: BLE001
            note = str(exc)
    return {
        "ok": True,
        "serial": serial,
        "usb_confirmed": True,
        "auto_unlocked": unlocked,
        "has_secret": bool(secret),
        "note": note or ("phone unlocked — ready to cast" if unlocked else "no PIN/pattern remembered yet"),
    }


def cast_status() -> dict:
    with _LOCK:
        state = dict(_STATE)
    state["adb_available"] = bool(_adb_binary() or adb_available())
    state["usb"] = adb_usb_connected() if (_adb_binary() or adb_available()) else {"connected": False}
    state["scrcpy_installed"] = bool(_find_scrcpy_exe())
    return state


def current_frame() -> Path | None:
    """Path to the latest phone frame, or None when not casting."""
    with _LOCK:
        if not _STATE["running"] or not _STATE["last_frame"]:
            return None
        return Path(_STATE["last_frame"])
