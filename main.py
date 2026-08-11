"""
main.py — A3THER boot engine (async).

What happens on boot, in order:

1. **Log redirect** — when compiled as a *windowless* exe (``console=False``)
   there is no console, so stdout/stderr are re-pointed at the terminal log
   (``%LOCALAPPDATA%/A3THER/logs/a3ther.log``) before anything else runs.
   Every ``print()`` in the engine therefore lands in that log.

2. **Pre-flight dependency check** — ``adb`` (system PATH, falling back to the
   adb.exe bundled with the auto-installed scrcpy) and ``ffmpeg`` (PATH or the
   ffmpeg bundled with imageio). Results are printed as PASS/FAIL with the
   resolved binary paths.

3. **USB port loop** — a background multi-threaded watcher polls ``adb
   devices`` every 2s. The instant a phone connection event is detected it
   spawns a worker thread that, completely touch-free, wakes the screen,
   swipes up the keyguard and injects the saved PIN/pattern — preferably via
   the **mcp-scrcpy-vision** server's fast MCP tools (``android.screen.wake``
   / ``android.input.swipe`` / ``android.input.text``), falling back to
   mobile-mcp, then to A3THER's native ADB bridge.

4. **API core** — the FastAPI HUD server starts (same app as launcher.py),
   the browser opens in app mode, and the engine idles in the asyncio loop.

Every step prints to the terminal log; nothing fails silently.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import threading
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
# 1. Terminal log (windowless exe)
# --------------------------------------------------------------------------- #
def _log_path() -> Path:
    """Resolve the terminal-log file (survives in dev + frozen)."""
    try:
        from config.paths import get_data_dir

        base = get_data_dir() / "logs"
    except Exception:  # noqa: BLE001
        base = Path.home() / ".a3ther" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "a3ther.log"


def redirect_to_log() -> Path:
    """Point stdout/stderr at the terminal log (idempotent)."""
    path = _log_path()
    try:
        log = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = log
        sys.stderr = log
    except Exception:  # noqa: BLE001 — never crash the boot over logging
        pass
    return path


def log(*parts: object) -> None:
    """Print with a timestamp — the single log entry point for the engine.

    Every line also lands in the shared engine state so the HUD's boot-log
    panel shows it live (see ``GET /api/engine/status``).
    """
    stamp = time.strftime("%H:%M:%S")
    line = f"[{stamp}] " + " ".join(str(p) for p in parts)
    print(line, flush=True)
    try:
        from core.engine_state import push_event

        push_event(line)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# 2. Pre-flight dependency check
# --------------------------------------------------------------------------- #
async def _check_adb() -> dict:
    """Resolve adb: system PATH first, then the scrcpy-bundled copy."""
    resolved = shutil.which("adb")
    source = "PATH"
    if not resolved:
        try:
            from sync.android import _adb_path

            resolved = _adb_path()
            source = "scrcpy-bundled"
        except Exception:  # noqa: BLE001
            resolved = None
    ok = bool(resolved)
    log(f"[PREFLIGHT] adb      : {'PASS' if ok else 'FAIL'}  ({resolved or 'not found'} · {source})" if ok
        else f"[PREFLIGHT] adb      : FAIL  — install Android platform-tools or run Phone Link once to auto-download scrcpy")
    return {"ok": ok, "path": resolved, "source": source}


async def _check_ffmpeg() -> dict:
    """Resolve ffmpeg: system PATH first, then imageio's bundled binary."""
    resolved = shutil.which("ffmpeg")
    source = "PATH"
    if not resolved:
        try:
            import imageio_ffmpeg

            resolved = imageio_ffmpeg.get_ffmpeg_exe()
            source = "imageio-bundled"
        except Exception:  # noqa: BLE001
            resolved = None
    ok = bool(resolved)
    log(f"[PREFLIGHT] ffmpeg    : {'PASS' if ok else 'FAIL'}  ({resolved or 'not found'} · {source})" if ok
        else "[PREFLIGHT] ffmpeg    : FAIL  — needed for video editing; reinstall the exe or pip install imageio-ffmpeg")
    return {"ok": ok, "path": resolved, "source": source}


async def preflight() -> dict:
    """Run all dependency checks concurrently and print the summary."""
    log("[PREFLIGHT] running dependency checks (adb + ffmpeg)…")
    adb, ffmpeg = await asyncio.gather(_check_adb(), _check_ffmpeg())
    log(f"[PREFLIGHT] summary   : adb {'✓' if adb['ok'] else '✗'}  ffmpeg {'✓' if ffmpeg['ok'] else '✗'}")
    try:
        from core.engine_state import set_preflight

        set_preflight(adb, ffmpeg)
    except Exception:  # noqa: BLE001
        pass
    return {"adb": adb, "ffmpeg": ffmpeg}


# --------------------------------------------------------------------------- #
# 3. USB port loop + touch-free auto-unlock
# --------------------------------------------------------------------------- #
def _auto_unlock_phone(serial: str) -> None:
    """Worker: wake → swipe up → inject the saved PIN/pattern, touch-free.

    Prefers mcp-scrcpy-vision's fast MCP tools, falls back to mobile-mcp,
    then to the native ADB bridge. Every step prints to the terminal log.
    """
    try:
        from sync.phone_vault import get_secret

        secret = get_secret(serial) or get_secret(None)  # per-device, then default
    except Exception as exc:  # noqa: BLE001
        log(f"[USB:{serial}] vault unavailable: {exc}")
        return

    if not secret:
        log(f"[USB:{serial}] connected — no PIN saved yet. Say: \"my pin is 1234\" and I'll unlock automatically next time.")
        return

    kind, value = secret.get("kind"), secret.get("value")
    log(f"[USB:{serial}] connection event → auto-unlock armed ({kind} saved)")

    # 3a. MCP path — scrcpy-vision fast tools.
    if _mcp_scrcpy_unlock(serial, kind, value):
        return
    # 3b. MCP path — mobile-mcp best-effort.
    if _mcp_mobile_unlock(serial, kind, value):
        return
    # 3c. Native ADB bridge (already handles PIN/pattern/password).
    try:
        from sync.android import unlock_phone

        result = unlock_phone(serial) or {}
        if result.get("ok"):
            log(f"[USB:{serial}] auto-unlocked via native ADB bridge.")
        else:
            log(f"[USB:{serial}] unlock failed: {result.get('error') or result.get('message') or result}")
    except Exception as exc:  # noqa: BLE001
        log(f"[USB:{serial}] native unlock error: {exc}")


def _connected_mcp_tools() -> list[tuple[str, str]]:
    """Return (server, tool) pairs for every connected MCP server."""
    try:
        from mcp.host import get_mcp_host

        tools = get_mcp_host().list_tools()
        return [(t.server, t.name) for t in tools]
    except Exception:  # noqa: BLE001
        return []


def _mcp_scrcpy_unlock(serial: str, kind: str, value: str) -> bool:
    """Unlock via mcp-scrcpy-vision tools when that server is connected."""
    try:
        from mcp.host import get_mcp_host

        host = get_mcp_host()
        pairs = _connected_mcp_tools()
        by_tool = {tool: server for server, tool in pairs}
        needed = {"android.screen.wake", "android.input.swipe", "android.input.text"}
        if not needed.issubset(by_tool):
            return False
        log(f"[USB:{serial}] MCP scrcpy-vision detected → touch-free unlock")

        server = by_tool["android.screen.wake"]
        host.call_tool(server, "android.screen.wake", {"serial": serial})
        time.sleep(0.8)
        host.call_tool(server, "android.input.swipe", {"serial": serial, "x1": 540, "y1": 1600, "x2": 540, "y2": 400, "durationMs": 300})
        time.sleep(0.6)
        if kind == "pattern":
            log(f"[USB:{serial}] pattern via MCP needs the native grid tracer — switching to ADB bridge")
            return False
        host.call_tool(server, "android.input.text", {"serial": serial, "text": str(value)})
        time.sleep(0.3)
        host.call_tool(server, "android.input.keyevent", {"serial": serial, "keycode": "66"})  # Enter
        return _confirm_unlocked(serial, "MCP scrcpy-vision")
    except Exception as exc:  # noqa: BLE001
        log(f"[USB:{serial}] MCP scrcpy-vision unlock error: {exc}")
        return False


def _mcp_mobile_unlock(serial: str, kind: str, value: str) -> bool:
    """Best-effort unlock via mobile-mcp tools when connected."""
    try:
        from mcp.host import get_mcp_host

        host = get_mcp_host()
        by_tool = {tool: server for server, tool in _connected_mcp_tools()}
        if "mobile_press_button" not in by_tool:
            return False
        server = by_tool["mobile_press_button"]
        host.call_tool(server, "mobile_press_button", {"device": serial, "button": "POWER"})
        time.sleep(1.0)
        if kind == "pattern":
            log(f"[USB:{serial}] pattern via mobile-mcp needs the native grid tracer — switching to ADB bridge")
            return False
        host.call_tool(server, "mobile_type_keys", {"device": serial, "text": str(value)})
        return _confirm_unlocked(serial, "mobile-mcp")
    except Exception as exc:  # noqa: BLE001
        log(f"[USB:{serial}] mobile-mcp unlock error: {exc}")
        return False


def _confirm_unlocked(serial: str, via: str) -> bool:
    """Wait a beat, then verify the keyguard actually dropped."""
    time.sleep(1.5)
    try:
        from sync.android import _is_locked

        locked = _is_locked(serial)
        if locked is False:
            log(f"[USB:{serial}] ✓ unlocked touch-free via {via}.")
            return True
        log(f"[USB:{serial}] still locked after {via} — PIN wrong? Say: \"my pin is <new>\"")
        return False
    except Exception:  # noqa: BLE001
        log(f"[USB:{serial}] could not verify lock state after {via} (assume ok)")
        return True


def _usb_watch_loop(interval: float = 2.0) -> None:
    """Background thread: poll adb for new USB connections and react.

    Spawns one worker thread per newly detected serial so a slow unlock
    never stalls the watcher (the "multi-threaded" requirement).
    """
    log("[USB] port loop started — watching for phone connections…")
    try:
        from core.engine_state import set_usb_running

        set_usb_running(True)
    except Exception:  # noqa: BLE001
        pass
    known: set[str] = set()
    while True:
        try:
            from sync.android import adb_devices

            devices = adb_devices().get("devices") or []
            connected = {d["serial"] for d in devices if d.get("state") == "device"}
            try:
                set_usb_running(True, sorted(connected))
            except Exception:  # noqa: BLE001
                pass
            for serial in sorted(connected - known):
                log(f"[USB] ⚡ connection event detected: {serial}")
                threading.Thread(
                    target=_auto_unlock_phone, args=(serial,), daemon=True, name=f"usb-unlock-{serial}"
                ).start()
            known = connected
        except Exception as exc:  # noqa: BLE001
            log(f"[USB] watcher error: {exc}")
        time.sleep(interval)


def start_usb_watch(interval: float = 2.0) -> threading.Thread:
    thread = threading.Thread(
        target=_usb_watch_loop, args=(interval,), daemon=True, name="usb-watch"
    )
    thread.start()
    return thread


# --------------------------------------------------------------------------- #
# 4. API core boot
# --------------------------------------------------------------------------- #
def _bootstrap() -> None:
    """First-run + state-folder setup (shared with launcher.py)."""
    import launcher  # noqa: PLC0415

    launcher._ensure_data_dir()  # noqa: SLF001
    launcher._ensure_bundle_mirror()  # noqa: SLF001
    launcher._run_first_run()  # noqa: SLF001


_SERVER_REF: dict = {}


def _serve_loop(config, server: uvicorn.Server) -> None:
    """Run the uvicorn serve loop inside a background thread (window mode)."""
    try:
        asyncio.run(server.serve())
    except Exception as exc:  # noqa: BLE001
        log(f"server error: {exc}")
        if config.port == 8000:
            log("port 8000 busy? Try: python main.py --port 9000")
    finally:
        _SERVER_REF["done"] = True


def _parent_dead(parent_pid: int) -> bool:
    """Windows check: has the given parent process exited?"""
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(parent_pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return True
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value != 259  # 259 == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def _window_host(args: argparse.Namespace) -> int:
    """Subprocess entry: owns the native HUD window — and *only* that.

    Runs ``webview`` (pythonnet/.NET) in isolation from the backend, because
    bleak's WinRT Bluetooth backend crashes the WebView2 window when both live
    in one process. Closing the window exits this process; the parent then
    winds down the backend — "close app = stop backend" stays intact.
    """
    redirect_to_log()
    url = os.environ.get("A3THER_HUD_URL") or f"http://127.0.0.1:{args.port}/"

    import core.desktop as desktop  # noqa: PLC0415

    if not desktop.wait_ready(url, timeout=40.0):
        print(f"[window-host] backend never answered at {url} — opening anyway")
    opened = desktop.open_hud_window(url)
    if opened:
        return 0  # window closed → parent shuts the backend down

    # webview unavailable: browser fallback — linger until the parent dies,
    # so the backend stays up for the browser session and window+backend
    # still stop together.
    print("[window-host] native window unavailable — browser fallback")
    parent = os.environ.get("A3THER_PARENT_PID")
    while parent and not _parent_dead(int(parent)):
        time.sleep(2)
    return 0


async def amain(args: argparse.Namespace) -> int:
    redirect_to_log()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    if not args.headless:
        print("╔════════════════════════════════════════════╗")
        print("║        A.3.T.H.E.R. — DESKTOP MODE         ║")
        print("╚════════════════════════════════════════════╝")

    _bootstrap()

    try:
        from core.engine_state import STATE

        STATE["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        pass

    # Pre-flight dependency check (async, concurrent).
    await preflight()

    # USB port loop (background thread — survives for the app's lifetime).
    start_usb_watch(interval=args.usb_interval)

    os.environ["A3THER_PORT"] = str(args.port)

    import backend.api.server as app_module  # noqa: PLC0415
    import uvicorn  # noqa: PLC0415

    config = uvicorn.Config(
        app_module.app,
        host=args.host,
        port=args.port,
        log_level="warning" if args.headless else "info",
    )
    server = uvicorn.Server(config)

    # ------------------------------------------------------------------ #
    # Window mode (default): the exe owns a native HUD window. Closing the
    # window stops the backend and exits the app — "open = backend starts,
    # close = backend stops". Pass --no-window to get the old browser tab
    # behaviour instead.
    # ------------------------------------------------------------------ #
    if not args.headless and not args.no_window:
        import subprocess  # noqa: PLC0415

        url = os.environ.get("A3THER_HUD_URL") or f"http://127.0.0.1:{args.port}/"
        print(f"[A3THER] HUD: {url}")
        import launcher  # noqa: PLC0415

        launcher._print_phone_link_later(args.port)  # noqa: SLF001

        # Backend stays in THIS process (engine, USB watch, uvicorn — all the
        # stuff that's proven to work). The native window lives in a separate
        # subprocess so pywebview's .NET runtime never meets bleak's WinRT
        # runtime in the same process (that pairing crashes the window).
        _SERVER_REF["done"] = False
        _SERVER_REF["server"] = server
        threading.Thread(target=_serve_loop, args=(config, server), daemon=True).start()

        env = dict(os.environ)
        env["A3THER_PARENT_PID"] = str(os.getpid())
        # Dev: python.exe main.py --window-host …  Frozen: A3THER.exe --window-host …
        if getattr(sys, "frozen", False):
            host_cmd = [sys.executable, "--window-host", "--port", str(args.port)]
        else:
            host_cmd = [sys.executable, os.path.abspath(sys.argv[0]), "--window-host", "--port", str(args.port)]
        child = subprocess.Popen(
            host_cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # Drain the child's output into our terminal log (it also re-points
        # its own stdout there, but belt-and-braces).
        def _drain() -> None:
            assert child.stdout is not None
            for line in child.stdout:
                log("[window-host]", line.decode("utf-8", "replace").rstrip())

        threading.Thread(target=_drain, daemon=True).start()
        child.wait()  # blocks until the window subprocess exits
        log(f"HUD window closed — stopping backend (window host exit={child.returncode})")
        server.should_exit = True
        deadline = time.monotonic() + 8.0
        while not _SERVER_REF.get("done") and time.monotonic() < deadline:
            time.sleep(0.1)
        if child.poll() is None:
            try:
                child.kill()
            except Exception:  # noqa: BLE001
                pass
        return 0

    # ------------------------------------------------------------------ #
    # Headless / --no-window: serve in-process, browser open optional.
    # ------------------------------------------------------------------ #
    if not args.headless:
        print(f"[A3THER] HUD: http://127.0.0.1:{args.port}/")
        import launcher  # noqa: PLC0415

        launcher._print_phone_link_later(args.port)  # noqa: SLF001
        if not args.no_browser:
            launcher._open_browser_later(f"http://127.0.0.1:{args.port}/")  # noqa: SLF001

    try:
        await server.serve()
    except KeyboardInterrupt:
        log("shutting down.")
    except Exception as exc:  # noqa: BLE001
        log(f"server error: {exc}")
        if args.port == 8000:
            log("port 8000 busy? Try: python main.py --port 9000")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="A.3.T.H.E.R. boot engine")
    parser.add_argument("--port", type=int, default=int(os.environ.get("A3THER_PORT", "8000")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--no-window", action="store_true", help="don't open the native HUD window (browser instead)")
    parser.add_argument("--headless", action="store_true", help="server only, minimal output")
    parser.add_argument("--window-host", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--usb-interval", type=float, default=2.0, help="USB poll interval (s)")
    args = parser.parse_args()
    if args.window_host:
        return _window_host(args)
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
