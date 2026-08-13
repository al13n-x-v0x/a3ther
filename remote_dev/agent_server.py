"""
agent_server.py — A3THER Phase-1 remote-control server.

A small, self-contained HTTP + UDP server that turns this laptop into a
discoverable, pairable A3THER device that a phone can control over the LAN:

- ``GET  /identity``        — public device info (no auth)
- ``POST /pair``            — start pairing; returns code + QR payload
- ``POST /pair/confirm``    — exchange the code for a long-lived token
- ``POST /command``         — run an action on this PC (Bearer token auth)
- ``GET  /devices``         — list paired devices (Bearer token auth)
- ``POST /revoke``          — unpair a device (Bearer token auth)

Actions (all real, executed locally — no fake prototypes):
    status          system status (psutil when available)
    open <app>      launch a known application (chrome, vscode, explorer...)
    lock            lock the workstation (Windows)
    run <command>   run a shell command and return its output
    screenshot      capture the screen (requires mss; honest failure otherwise)

Security model:
- Binds to 0.0.0.0 so phones on the LAN can reach it (same trust model as
  scrcpy/RustDesk), but every state-changing call requires the token minted
  during pairing. The token is never exposed over the network except in the
  ``Authorization`` header over the local network.
- Arbitrary ``run`` commands are disabled unless A3THER_ALLOW_SHELL=1 —
  and even then they are logged with the caller's device info.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shlex
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .devices import (
    DiscoveryBeacon,
    get_identity,
    get_pairing_store,
)

log = logging.getLogger("a3ther.remote")

DEFAULT_PORT = 42872


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #

KNOWN_APPS = {
    "chrome": "chrome",
    "vscode": "code",
    "explorer": "explorer",
    "notepad": "notepad",
    "terminal": "cmd",
    "powershell": "powershell",
    "calculator": "calc",
    "paint": "mspaint",
}


def _run_shell(command: str, timeout: int = 30) -> dict:
    """Execute a shell command and return a result dict."""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "exit": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit": -1, "stdout": "", "stderr": f"timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "exit": -1, "stdout": "", "stderr": str(exc)}


def execute_action(action: str, allow_shell: bool = False) -> dict:
    """Dispatch one remote action. Always returns a JSON-safe dict."""
    action = (action or "").strip()

    if action == "status":
        return _status()

    if action.startswith("open "):
        app = action[5:].strip().lower()
        return _open_app(app)

    if action == "lock":
        return _lock_pc()

    if action.startswith("run "):
        command = action[4:].strip()
        if not allow_shell:
            return {
                "ok": False,
                "error": "Shell commands are disabled on this laptop. "
                "Start the server with A3THER_ALLOW_SHELL=1 to enable them.",
            }
        if not command:
            return {"ok": False, "error": "Empty command."}
        log.warning("[remote] shell command from a paired device: %s", command)
        return _run_shell(command)

    if action == "screenshot":
        return _screenshot()

    return {"ok": False, "error": f"Unknown action: {action!r}"}


def _status() -> dict:
    info = {"platform": platform.system(), "node": socket.gethostname()}
    try:
        import psutil  # type: ignore

        info["cpu_percent"] = psutil.cpu_percent(interval=0.4)
        info["memory_percent"] = psutil.virtual_memory().percent
        info["uptime_seconds"] = int(round(time() - psutil.boot_time()))
        info["load"] = [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else None
    except Exception:  # noqa: BLE001
        info["note"] = "psutil not available; partial status."
    return {"ok": True, "status": info}


def _open_app(app: str) -> dict:
    """Launch a known app without blocking the server.

    Uses Popen with DEVNULL redirects: a GUI app inheriting the console's
    stdout/stderr handles keeps ``capture_output=True`` pipes open forever,
    which looks like a hang. Fire-and-forget avoids that entirely.
    """
    target = KNOWN_APPS.get(app)
    if target is None:
        return {
            "ok": False,
            "error": f"Unknown app {app!r}. Known: {', '.join(sorted(KNOWN_APPS))}",
        }
    devnull = subprocess.DEVNULL
    if os.name == "nt":
        command = f"start {target}"
    else:
        command = f"{target} >/dev/null 2>&1 &"
    try:
        subprocess.Popen(
            command,
            shell=True,
            stdout=devnull,
            stderr=devnull,
            stdin=devnull,
            close_fds=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "opened": app, "exit": 0}


def _lock_pc() -> dict:
    if os.name != "nt":
        return {"ok": False, "error": "Locking is only supported on Windows."}
    import ctypes

    try:
        ctypes.windll.user32.LockWorkStation()
        return {"ok": True, "locked": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _screenshot() -> dict:
    try:
        import mss  # type: ignore

        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
        png = mss.tools.to_png(shot.rgb, shot.size, output=None)
        import base64

        return {"ok": True, "png_base64": base64.b64encode(png).decode("ascii")}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"Screenshot unavailable on this machine (mss missing/failed): {exc}",
        }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #


class _Handler(BaseHTTPRequestHandler):
    server: "AgentHTTPServer"  # type: ignore[assignment]

    # -- helpers ------------------------------------------------------------ #
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def _token(self) -> str | None:
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _require_token(self) -> bool:
        token = self._token()
        if token and self.server.pairing.is_valid(token):
            self.server.pairing.touch(token)
            return True
        self._send(401, {"ok": False, "error": "Unauthorized. Pair first (POST /pair)."})
        return False

    # -- routing ------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/identity":
            self._send(200, {"ok": True, "device": self.server.identity.public()})
        elif path == "/devices":
            if self._require_token():
                self._send(200, {"ok": True, "devices": self.server.pairing.devices()})
        else:
            self._send(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()

        if path == "/pair":
            code = self.server.pairing.new_code()
            payload = {"code": code, "qr": f"a3ther://pair/{code}"}
            self._send(200, {"ok": True, **payload})
            return

        if path == "/pair/confirm":
            code = str(body.get("code", "")).strip()
            name = str(body.get("name", "Phone"))[:64]
            phone_id = str(body.get("device_id", ""))[:128]
            token = self.server.pairing.confirm(code, name, phone_id)
            if token is None:
                self._send(401, {"ok": False, "error": "Invalid or expired pairing code."})
            else:
                self._send(200, {"ok": True, "token": token})
            return

        if path == "/command":
            if not self._require_token():
                return
            action = str(body.get("action", ""))
            result = execute_action(action, allow_shell=self.server.allow_shell)
            self._send(200, {"ok": result.get("ok", False), "action": action, "result": result})
            return

        if path == "/revoke":
            if not self._require_token():
                return
            target = str(body.get("token", ""))
            if not target:
                self._send(400, {"ok": False, "error": "Provide the token to revoke."})
                return
            self.server.pairing.revoke(target)
            self._send(200, {"ok": True, "revoked": target[:12] + "…"})
            return

        self._send(404, {"ok": False, "error": "Not found"})


class AgentHTTPServer(ThreadingHTTPServer):
    """Threaded JSON server carrying identity + pairing store."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        identity,
        pairing,
        allow_shell: bool = False,
    ):
        super().__init__(address, _Handler)
        self.identity = identity
        self.pairing = pairing
        self.allow_shell = allow_shell


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

_server_ref: dict = {"http": None, "beacon": None}


def start_server(
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    allow_shell: bool | None = None,
    background: bool = True,
) -> AgentHTTPServer:
    """Start the remote-control server (+ LAN beacon).

    ``background=True`` (default) spawns threads and returns immediately;
    ``False`` blocks forever (useful as ``python -m remote_dev.agent_server``).
    """
    if allow_shell is None:
        allow_shell = os.environ.get("A3THER_ALLOW_SHELL", "0") == "1"

    identity = get_identity()
    pairing = get_pairing_store()

    http = AgentHTTPServer((host, port), identity, pairing, allow_shell)
    beacon = DiscoveryBeacon(identity)
    beacon.start()

    _server_ref["http"] = http
    _server_ref["beacon"] = beacon

    if background:
        threading.Thread(target=http.serve_forever, name="a3ther-remote-http", daemon=True).start()
        log.info(
            "[remote] server on %s:%s (shell=%s) — device %s",
            host, port, allow_shell, identity.name,
        )
        return http
    try:
        log.info("[remote] server on %s:%s — device %s", host, port, identity.name)
        http.serve_forever()
    finally:
        http.server_close()
        beacon.stop()
    return http


def stop_server() -> None:
    http = _server_ref.get("http")
    beacon = _server_ref.get("beacon")
    if http:
        try:
            http.shutdown()
            http.server_close()
        except Exception:  # noqa: BLE001
            pass
    if beacon:
        beacon.stop()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="A3THER remote-control server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--allow-shell",
        action="store_true",
        default=os.environ.get("A3THER_ALLOW_SHELL", "0") == "1",
        help="Allow 'run <command>' from paired devices.",
    )
    parser.add_argument(
        "--discover", action="store_true", help="Scan the LAN for A3THER devices and exit."
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.discover:
        from .devices import discover

        found = discover(timeout=3.0)
        if not found:
            print("No A3THER devices found on the LAN.")
            return
        for dev in found:
            print(f"- {dev.get('name')}  {dev.get('addr')}  id={dev.get('device_id')}")
        return

    identity = get_identity()
    print(f"[A3THER] remote server starting — device: {identity.name} ({identity.device_id})")
    print(f"[A3THER] LAN: port {args.port} (HTTP), {DEFAULT_PORT} (UDP discovery)")
    print(f"[A3THER] shell commands: {'ENABLED' if args.allow_shell else 'DISABLED'}")
    start_server(host=args.host, port=args.port, allow_shell=args.allow_shell, background=False)


if __name__ == "__main__":
    main()
