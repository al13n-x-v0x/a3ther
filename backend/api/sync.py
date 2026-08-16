"""
backend/api/sync.py — the multi-device orchestration API.

Endpoints
---------
GET  /api/sync/mesh        → mesh status (nodes, kinds, events)
POST /api/sync/broadcast   → fan a DeviceCommand out to all/one node
POST /api/sync/terminate   → JARVIS failsafe (kill processes + mesh)
WS   /ws/mesh              → node join/heartbeat/command delivery

A connected client identifies itself with query params (``kind``, ``name``)
or by sending a ``{"profile": {...}}`` join message first; the user-agent
is sniffed as a fallback so an iPhone hitting the endpoint is profiled
automatically.
"""
from __future__ import annotations

import asyncio
import platform as _platform
import threading
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from sync.broadcaster import get_broadcast_engine
from sync.failsafe import get_failsafe
from sync.mesh import MeshNode, get_mesh_registry, profile_from_user_agent
from sync.protocol import DeviceCommand, MeshEvent

router = APIRouter(prefix="/api/sync", tags=["sync"])
# WebSocket lives at the clean top-level path /ws/mesh (no prefix).
ws_router = APIRouter(tags=["sync-ws"])

# --------------------------------------------------------------------------- #
# Built-in local command hooks (idempotent registration)
# --------------------------------------------------------------------------- #
_BOOTSTRAPPED = False
_BOOTSTRAP_LOCK = threading.Lock()


def _bootstrap_hooks() -> None:
    """Wire the failsafe + host-level deep-automation hooks into the engine."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return
        from sync.broadcaster import register_command

        register_command("terminate", lambda params: get_failsafe().terminate(
            reason=str((params or {}).get("reason") or "mesh terminate order")
        ))

        def _host_diag(params: dict | None) -> dict:
            try:
                import psutil

                return {
                    "host": __import__("platform").node(),
                    "cpu_percent": psutil.cpu_percent(interval=0.2),
                    "ram_percent": psutil.virtual_memory().percent,
                    "process_count": len(psutil.pids()),
                }
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

        register_command("initialize_diagnostic", _host_diag)
        register_command("unlock_interface", lambda _p: {"host": "interface already unlocked"})
        register_command("system_sleep", lambda _p: {"host": "sleep deferred (HITL not enabled)"})

        def _android_hook(params: dict | None) -> dict:
            """Drive a connected Android device from a mesh broadcast.

            Usage: broadcast android_control action=tap x=500 y=900
            or     broadcast android_control action=unlock
            Raises on failure so the mesh reports ok=False honestly.
            """
            from sync.android import control as adb_control

            params = dict(params or {})
            action = str(params.pop("action", "") or "").strip().lower()
            if not action:
                raise ValueError("android_control requires an 'action' param (e.g. action=unlock)")
            result = adb_control(action, params)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or f"android {action} failed")
            detail = result.get("stdout") or f"android {action} ok on {result.get('serial', '?')}"
            return str(detail)[:400]

        # -- REAL host control: phone → laptop ----------------------------------- #

        def _host_open_app(params: dict | None) -> dict:
            """Open an app on THIS machine (the one the phone is controlling)."""
            params = dict(params or {})
            app = str(params.get("app") or params.get("app_name") or "").strip()
            if not app:
                raise ValueError("open_app requires an 'app' param (e.g. app=notepad)")
            try:
                from actions.open_app import open_app as _open

                return _open({"app_name": app})
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

        def _host_close_app(params: dict | None) -> dict:
            """Close the foreground app on this machine (Alt+F4 / Cmd+Q)."""
            try:
                from actions.computer_settings import close_app

                close_app()
                return "closed the foreground app"
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

        def _host_type_text(params: dict | None) -> dict:
            """Type text into the focused window on this machine."""
            params = dict(params or {})
            text = str(params.get("text") or "")
            if not text:
                raise ValueError("type_text requires a 'text' param")
            try:
                from actions.computer_settings import type_text

                type_text(text, press_enter_after=bool(params.get("enter")))
                return f"typed {len(text)} character(s)"
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

        def _host_screenshot(params: dict | None) -> dict:
            """Capture this machine's screen and save it to the A3THER data dir."""
            try:
                import mss
                import time as _t

                from config.paths import data_path

                dest = data_path(f"host_{int(_t.time())}.png")
                with mss.mss() as sct:
                    sct.shot(output=str(dest))
                return f"screenshot saved: {dest}"
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

        def _host_notify(params: dict | None) -> dict:
            """Show a native Windows notification on this machine."""
            params = dict(params or {})
            title = str(params.get("title") or "A.3.T.H.E.R.")[:60]
            body = str(params.get("body") or "Command from your phone.")[:180]
            try:
                from win10toast import ToastNotifier

                ToastNotifier().show_toast(title, body, duration=5, threaded=True)
                return f"notification shown: {title}"
            except Exception:  # noqa: BLE001
                try:
                    import subprocess

                    # PS single-quoted strings are literal; doubling the quote
                    # is the only escape needed — blocks break-out injection.
                    safe_title = title.replace("'", "''")
                    safe_body = body.replace("'", "''")
                    script = (
                        "[void][Windows.UI.Notifications.ToastNotificationManager, "
                        "Windows.UI.Notifications, ContentType = WindowsRuntime]; "
                        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                        "$txt = $xml.GetElementsByTagName('text'); "
                        f"$txt.Item(0).AppendChild($xml.CreateTextNode('{safe_title}')) > $null; "
                        f"$txt.Item(1).AppendChild($xml.CreateTextNode('{safe_body}')) > $null; "
                        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                        "'A3THER').Show($toast)"
                    )
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command", script],
                        timeout=10,
                    )
                    return f"notification queued: {title}"
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

        def _host_flash(params: dict | None) -> dict:
            """Confirm the flash command; the HUD's own screen flashes on receipt."""
            return "host flash triggered — check the dashboard"

        def _unlock_phone_hook(params: dict | None) -> dict:
            """Voice/terminal phone unlock: wake → stored PIN/pattern → verify.

            Returns a spoken string so the voice brain echoes a clean result.
            """
            from sync.android import unlock_phone

            params = dict(params or {})
            result = unlock_phone(str(params.get("serial") or "") or None)
            if result.get("ok") and result.get("unlocked"):
                if result.get("already_unlocked"):
                    return "your phone is already unlocked"
                return f"phone unlocked with {result.get('method', 'PIN')}"
            if result.get("need_secret"):
                return "your phone is locked and I don't have its PIN or pattern yet — tell me: my pin is 1234"
            if result.get("wrong_secret"):
                return "that PIN or pattern was wrong — unlock your phone again on the screen, or tell me the correct one"
            return f"couldn't unlock the phone: {result.get('error') or 'unknown error'}"

        def _phone_secret_hook(params: dict | None) -> dict:
            """Remember/forget a phone's PIN or pattern from a spoken command."""
            from sync.android import remember_secret
            from sync.phone_vault import delete_secret

            params = dict(params or {})
            action = str(params.get("action") or "save").lower()
            if action == "delete":
                result = delete_secret(str(params.get("serial") or "") or "default")
                return "phone credential forgotten" if result.get("removed") else "nothing to forget"
            result = remember_secret(
                str(params.get("kind") or "pin").lower(),
                str(params.get("value") or ""),
                str(params.get("serial") or "") or None,
            )
            if result.get("ok"):
                return f"{result.get('kind')} remembered for your phone"
            return f"could not remember: {result.get('error')}"

        register_command("unlock_phone", _unlock_phone_hook)
        register_command("phone_secret", _phone_secret_hook)
        register_command("android_control", _android_hook)
        register_command("open_app", _host_open_app)
        register_command("close_app", _host_close_app)
        register_command("type_text", _host_type_text)
        register_command("screenshot", _host_screenshot)
        register_command("push_notification", _host_notify)
        register_command("flash_screen", _host_flash)
        _BOOTSTRAPPED = True


_bootstrap_hooks()


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #

class BroadcastRequest(BaseModel):
    command: str
    params: dict = {}
    target: str | None = None
    source: str = "dashboard"
    ack_required: bool = False


class TerminateRequest(BaseModel):
    reason: str = "manual abort from dashboard"
    scope: str = "all"


class AndroidControlRequest(BaseModel):
    action: str
    params: dict = {}


class PhoneSecretRequest(BaseModel):
    kind: str = "pin"
    value: str = ""
    serial: str | None = None


class PhoneUnlockRequest(BaseModel):
    serial: str | None = None


class BleConnectRequest(BaseModel):
    address: str
    name: str | None = None


class BleWriteRequest(BaseModel):
    uuid: str
    data: str = ""
    hex: bool = False


# --------------------------------------------------------------------------- #
# REST endpoints
# --------------------------------------------------------------------------- #

@router.get("/mesh")
def mesh_status():
    try:
        return get_broadcast_engine().mesh_status()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/broadcast")
def sync_broadcast(body: BroadcastRequest):
    try:
        engine = get_broadcast_engine()
        summary = engine.broadcast(
            command=body.command,
            params=body.params,
            target=body.target,
            source=body.source,
            ack_required=body.ack_required,
        )
        return summary
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/terminate")
def sync_terminate(body: TerminateRequest):
    try:
        return get_failsafe().terminate(reason=body.reason, scope=body.scope)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# --------------------------------------------------------------------------- #
# Phone link (zero-install mobile control)
# --------------------------------------------------------------------------- #

def _lan_ip() -> str:
    """Best LAN-facing IPv4 — the URL a phone on the same Wi-Fi uses."""
    import socket

    for probe in ("8.8.8.8", "1.1.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.4)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:  # noqa: BLE001
            pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


@router.get("/phone-link")
def phone_link():
    """The URL a phone opens (same Wi-Fi) to become a control node.

    The phone page needs no install: it joins /ws/mesh, receives
    broadcasts, and can fire commands back at the mesh.
    """
    import os

    port = os.environ.get("A3THER_PORT", "8000")
    url = f"http://{_lan_ip()}:{port}/phone"
    return {"url": url, "port": port, "lan_ip": _lan_ip()}


# --------------------------------------------------------------------------- #
# Android ADB bridge
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Phone screen casting (USB confirm → cast to laptop)
# --------------------------------------------------------------------------- #
class CastStartRequest(BaseModel):
    serial: str | None = None
    prefer_scrcpy: bool = True


@router.get("/cast/status")
def cast_status():
    """Cast engine state: running, mode (scrcpy/lite), USB devices, frame age."""
    try:
        from sync.cast import cast_status

        return cast_status()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/cast/confirm-usb")
def cast_confirm_usb(body: CastStartRequest):
    """'Did you connect the device with USB?' — confirm, auto-unlock with the
    remembered PIN/pattern, and report readiness to cast.
    """
    try:
        from sync.cast import confirm_usb

        return confirm_usb(body.serial)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/cast/start")
def cast_start(body: CastStartRequest):
    """Start casting the phone screen (scrcpy auto-installs on first use)."""
    try:
        from sync.cast import start_cast

        return start_cast(body.serial, body.prefer_scrcpy)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/cast/install")
def cast_install():
    """Auto-download + extract the scrcpy release so full casting works."""
    try:
        from sync.cast import install_scrcpy

        return install_scrcpy()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/control/devices")
def control_devices():
    """Control Phone panel — every controllable device with a connection badge.

    ADB Androids get live screens (``has_screen`` → thumbnails via
    ``/control/screen/{serial}``); mesh clients (phones/laptops that joined
    over the network) carry kind/platform; the host laptop is included with
    its own screen grab. Connection types: usb | wifi | mesh | local.
    """
    devices: list[dict] = []
    # 1. ADB Androids — USB or wireless debugging, live screen available.
    try:
        from sync.cast import list_screen_devices

        devices += list_screen_devices()
    except Exception:  # noqa: BLE001
        pass
    # 2. Mesh clients — phones/laptops joined over the network.
    try:
        from sync.mesh import get_mesh_registry

        for n in get_mesh_registry().status().get("online", []):
            devices.append(
                {
                    "serial": str(n.get("node_id") or ""),
                    "model": str(n.get("name") or "mesh node"),
                    "connection": "mesh",
                    "has_screen": False,
                    "kind": str(n.get("kind") or "web"),
                    "platform": str(n.get("platform") or ""),
                }
            )
    except Exception:  # noqa: BLE001
        pass
    # 3. Bluetooth devices — real, discovered over BLE. No screen over BT
    #    (screen casting needs USB/WiFi), but each is connectable: battery,
    #    device info, services and write-commands over the link.
    try:
        from backend.services.bluetooth_service import get_bluetooth_devices

        bt = get_bluetooth_devices()
        for d in bt.get("devices") or []:
            devices.append(
                {
                    "serial": str(d.get("address") or ""),
                    "model": str(d.get("name") or "Bluetooth Device"),
                    "connection": "bluetooth",
                    "has_screen": False,
                    "kind": "bluetooth",
                    "platform": f"BLE · {d.get('rssi') if d.get('rssi') is not None else '—'} dBm",
                    "bt": True,
                }
            )
    except Exception:  # noqa: BLE001
        pass
    # 4. This host — the laptop itself, screen grab included.
    devices.append(
        {
            "serial": "host",
            "model": "THIS LAPTOP",
            "connection": "local",
            "has_screen": True,
            "kind": "laptop",
            "platform": _platform.platform(),
        }
    )
    return {"devices": devices}


# Host screen grabs are slow (~5s) but the Control panel polls every 2.5s —
# without a cache every poll aborts the previous in-flight capture. This
# single-flight cache returns the last frame instantly while a grab runs.
_HOST_SHOT: dict = {"lock": threading.Lock(), "at": 0.0, "data": None, "busy": False}
_HOST_SHOT_TTL = 2.0  # seconds; polls closer than this reuse the cached frame


def _grab_host_screen() -> bytes:
    """Capture the primary monitor as PNG bytes (mss), single-flight.

    A full 1080p grab takes ~5s but the panel polls every 2.5s. With a plain
    cache the browser aborts every in-flight capture. This returns the last
    frame instantly while a grab is busy and refreshes it once per ~5s.
    """
    now = time.time()
    with _HOST_SHOT["lock"]:
        if _HOST_SHOT["data"] is not None and now - _HOST_SHOT["at"] < _HOST_SHOT_TTL:
            return _HOST_SHOT["data"]  # fresh enough — reuse
        if _HOST_SHOT["busy"]:
            return _HOST_SHOT["data"]  # a grab is running — reuse the last frame
        _HOST_SHOT["busy"] = True
    import os
    import tempfile

    import mss
    from mss.tools import to_png

    # mss's to_png wants a file path (bundled version) — write to a temp
    # file, read the bytes back, clean up.
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])  # primary monitor
            to_png(shot.rgb, shot.size, output=tmp)
        with open(tmp, "rb") as fh:
            data = fh.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    with _HOST_SHOT["lock"]:
        _HOST_SHOT["busy"] = False
        _HOST_SHOT["data"] = data
        _HOST_SHOT["at"] = time.time()
    return data


@router.get("/control/screen/{serial}")
def _downscale(data: bytes, width: int = 0) -> bytes:
    """Shrink a PNG to ``width`` px wide when asked (PIL).

    Full-res 1080p grabs are multiple MB and crash the native HUD window's
    WebView2 renderer when polled repeatedly — downscaling to a thumbnail
    keeps the live preview while making each frame ~20x smaller.
    Returns the original bytes when width <= 0 or PIL is unavailable.
    """
    if not width or width <= 0:
        return data
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(data))
        if img.width <= width:
            return data
        ratio = width / img.width
        img = img.resize((width, max(1, round(img.height * ratio))), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 — best-effort, never break the grab
        return data


@router.get("/control/screen/{serial}")
def control_screen(serial: str, w: int = 0):
    """Live PNG screen grab for the Control Phone panel.

    ADB serials capture via ``screencap`` (USB or WiFi); ``host``/``laptop``
    grab this machine's primary monitor (mss, single-flight cached). The
    panel polls this with a cache-buster for live thumbnails; ``w`` asks for
    a downscaled frame (thumbnails) instead of the full-res grab.
    """
    try:
        if serial in ("host", "laptop"):
            data = _grab_host_screen()
        else:
            from sync.cast import screencap_now

            data = screencap_now(serial)
        if not data:
            return JSONResponse(
                {"error": "screen capture failed — is the phone unlocked and USB debugging on?"},
                status_code=502,
            )
        return Response(content=_downscale(data, w), media_type="image/png")
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/cast/stop")
def cast_stop():
    try:
        from sync.cast import stop_cast

        return stop_cast()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/cast/frame.png")
def cast_frame():
    """Latest phone screen frame (lite cast stream for the HUD)."""
    try:
        from sync.cast import current_frame

        path = current_frame()
        if path is None or not path.is_file():
            return JSONResponse({"error": "cast not running"}, status_code=404)
        return FileResponse(str(path), media_type="image/png", headers={"Cache-Control": "no-store"})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/android/wireless")
def android_wireless(body: CastStartRequest):
    """Connect to the phone over WiFi (ADB wireless) — no USB cable.

    Auto-discovers the phone's IP from the mesh/LAN when ``ip`` is empty.
    """
    try:
        from sync.android import connect_wireless

        return connect_wireless(body.serial)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/android")
def android_status():
    """ADB availability + connected Android devices + allowed actions."""
    try:
        from sync.android import describe

        return describe()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/android/control")
def android_control(body: AndroidControlRequest):
    """Run one allowlisted Android control against a connected device.

    Actions: devices · status · unlock · lock · home · back · recent ·
    key · tap · swipe · text · open · open_url · screenshot ·
    unlock_phone · remember_secret · forget_secret · vault_status
    """
    try:
        from sync.android import control as adb_control

        result = adb_control(body.action, body.params)
        return result
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/phone-secret")
def phone_secret(body: PhoneSecretRequest):
    """Remember (or forget) a phone's PIN / pattern.

    ``kind`` = pin | pattern; ``value`` = e.g. "1234" or "1-5-9".
    """
    try:
        from sync.phone_vault import save_secret

        return save_secret(body.serial or "default", body.kind, body.value)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/phone-vault")
def phone_vault_status():
    """Which devices have a remembered secret (never the values)."""
    try:
        from sync.phone_vault import status

        return status()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/phone-unlock")
def phone_unlock(body: PhoneUnlockRequest):
    """Unlock the Android phone using its remembered PIN/pattern."""
    try:
        from sync.android import unlock_phone

        return unlock_phone(body.serial)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# --------------------------------------------------------------------------- #
# Bluetooth LE controller — connect to a real device and talk to it
# --------------------------------------------------------------------------- #
@router.get("/ble/status")
def ble_status():
    """Current BLE link state — safe to poll from the HUD."""
    try:
        from sync.ble_controller import get_ble_controller

        return get_ble_controller().status()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/ble/connect")
def ble_connect(body: BleConnectRequest):
    """Open a BLE connection to a discovered device (phone, earbuds, watch…)."""
    try:
        from sync.ble_controller import get_ble_controller

        return get_ble_controller().connect(body.address, name=body.name)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/ble/disconnect")
def ble_disconnect():
    """Drop the current BLE link."""
    try:
        from sync.ble_controller import get_ble_controller

        return get_ble_controller().disconnect()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/ble/info")
def ble_info():
    """Read battery + device info over the live link (best effort)."""
    try:
        from sync.ble_controller import get_ble_controller

        return get_ble_controller().read_info()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/ble/services")
def ble_services():
    """Enumerate every GATT service + characteristic of the connected device."""
    try:
        from sync.ble_controller import get_ble_controller

        return get_ble_controller().list_services()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/ble/write")
def ble_write(body: BleWriteRequest):
    """Send a command (text or hex) to a writable characteristic."""
    try:
        from sync.ble_controller import get_ble_controller

        return get_ble_controller().write(body.uuid, body.data, as_hex=body.hex)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# --------------------------------------------------------------------------- #
# WebSocket — a node joining the mesh
# --------------------------------------------------------------------------- #

@ws_router.websocket("/ws/mesh")
async def mesh_ws(websocket: WebSocket, kind: str = "", name: str = "", node_id: str = ""):
    await websocket.accept()

    registry = get_mesh_registry()
    loop = asyncio.get_running_loop()

    # Register immediately (default profile from params + UA sniff) so no
    # broadcast is ever lost during the join handshake, then upgrade the
    # profile in place if the client sends a richer join message.
    profile = profile_from_user_agent(
        user_agent=websocket.headers.get("user-agent", ""),
        name=name,
        kind_hint=kind,
        node_id=node_id or __import__("uuid").uuid4().hex[:12],
    )
    node = MeshNode(profile=profile, loop=loop, send=websocket.send_json)
    registry.register(node)

    try:
        # Non-blocking peek at a possible join upgrade (0.8s, then proceed).
        try:
            first = await asyncio.wait_for(websocket.receive_json(), timeout=0.8)
        except Exception:  # noqa: BLE001 — no join message, defaults fine
            first = None
        if isinstance(first, dict) and first.get("type") == "join":
            upgraded = profile_from_user_agent(
                user_agent=websocket.headers.get("user-agent", ""),
                name=first.get("name") or name,
                kind_hint=first.get("kind") or kind,
                node_id=first.get("node_id") or node.profile.node_id,
            )
            node.profile = upgraded

        # Greet the node so it knows it is wired.
        try:
            await websocket.send_json({
                "type": "welcome",
                "node_id": node.profile.node_id,
                "kind": node.profile.kind,
                "mesh_size": registry.count(),
            })
        except Exception:  # noqa: BLE001
            pass

        await _mesh_loop(websocket, node, registry)
    except WebSocketDisconnect:
        registry.unregister(node.profile.node_id, reason="websocket closed")
    except Exception as exc:  # noqa: BLE001
        registry.unregister(node.profile.node_id, reason=f"error: {exc}")
    finally:
        registry.unregister(node.profile.node_id, reason="disconnected")


async def _mesh_loop(websocket: WebSocket, node: MeshNode, registry) -> None:
    """Heartbeat + outbound command delivery, both non-blocking."""
    async def _receiver() -> None:
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type", "")
            if mtype == "heartbeat":
                node.touch()
            elif mtype == "state":
                node.touch()
                try:
                    node.profile.capabilities = list(msg.get("capabilities") or node.profile.capabilities)
                except Exception:  # noqa: BLE001
                    pass
            elif mtype == "ack":
                node.touch()
            elif mtype == "join":
                node.touch()

    async def _sender() -> None:
        while True:
            try:
                payload = await asyncio.wait_for(node.heartbeat.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # Idle: send a ping so the node can refresh its heartbeat.
                try:
                    await websocket.send_json({"type": "ping", "at": time.time()})
                except Exception:  # noqa: BLE001
                    return
                continue
            try:
                await websocket.send_json(payload)
            except Exception:  # noqa: BLE001
                return

    receiver = asyncio.create_task(_receiver())
    sender = asyncio.create_task(_sender())
    try:
        done, _ = await asyncio.wait(
            {receiver, sender},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            # Retrieve the exception so asyncio doesn't log
            # "Task exception was never retrieved" on every disconnect.
            try:
                task.exception()
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            if task is receiver:
                sender.cancel()
            else:
                receiver.cancel()
    finally:
        for task in (receiver, sender):
            if not task.done():
                task.cancel()
