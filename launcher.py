"""
launcher.py — A.3.T.H.E.R. desktop entry point.

Starts the FastAPI server (backend.api.server), opens the HUD in the
default browser, runs the first-run API-key prompt when needed, and
prints the phone-control URL. This is the file PyInstaller turns into
``A3THER.exe`` — users double-click the exe, no Python or pip needed.

Usage
-----
    python launcher.py                 # start + open browser (default)
    python launcher.py --port 9000     # different port
    python launcher.py --no-browser    # server only
    python launcher.py --headless      # server only, quiet startup
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser


def _ensure_data_dir() -> None:
    """Migrate any repo state into the OS app-data folder on first launch."""
    try:
        from config.paths import migrate_all, get_data_dir

        migrated = migrate_all()
        if any(migrated.values()):
            print(f"[A3THER] State folder ready: {get_data_dir()}")
            moved = [k for k, v in migrated.items() if v]
            print(f"[A3THER] Migrated {len(moved)} file(s): {', '.join(moved)}")
    except Exception as exc:  # noqa: BLE001
        print(f"[A3THER] Data-folder setup skipped: {exc}")


def _ensure_bundle_mirror() -> None:
    """Materialise config/api_keys.json from the bundled EMPTY template.

    Only matters in the frozen exe: a fresh bundle ships the template (no
    real keys — those live in the OS app-data folder), and this writes the
    mirror copy so legacy readers that hit config/api_keys.json directly
    never crash. Real keys are never bundled.
    """
    try:
        from pathlib import Path

        import config as config_pkg

        mirror = Path(config_pkg.__file__).resolve().parent / "api_keys.json"
        if mirror.exists():
            return
        template = Path(config_pkg.__file__).resolve().parent / "api_keys.template.json"
        if template.exists():
            mirror.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _run_first_run() -> None:
    """Interactive API-key prompt on first launch (skips when not a tty)."""
    try:
        from core.first_run import maybe_run_setup

        result = maybe_run_setup()
        if result is not None:
            print("[A3THER] Setup complete.")
    except Exception:  # noqa: BLE001
        pass


def _open_browser_later(url: str, delay: float = 1.6) -> None:
    """Open the HUD in an app-style window (no tabs/address bar) when a
    Chromium browser is available, otherwise the default browser."""
    def _open() -> None:
        time.sleep(delay)
        # App mode: chrome/msedge --app=<url> gives a frameless window that
        # behaves like a real desktop app instead of a browser tab.
        import shutil
        import subprocess

        path, exe = _find_app_browser()
        if path:
            try:
                subprocess.Popen(
                    [path, "--app=" + url, "--new-window"],
                    close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                print(f"[A3THER] HUD opened in app window ({exe}).")
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_open, daemon=True).start()


def _find_app_browser() -> tuple[str | None, str]:
    """Locate Chrome/Edge/Chromium for --app window mode.

    ``shutil.which`` only sees PATH, but Windows installs these browsers in
    Program Files / LocalAppData — probe those explicit paths too, so the
    frameless app window actually opens on most machines.
    """
    import shutil

    for name in ("chrome", "msedge", "chromium"):
        found = shutil.which(name)
        if found:
            return found, name
    candidates = [
        (os.environ.get("ProgramFiles", ""), "Google\\Chrome\\Application\\chrome.exe", "chrome"),
        (os.environ.get("ProgramFiles(x86)", ""), "Google\\Chrome\\Application\\chrome.exe", "chrome"),
        (os.environ.get("LocalAppData", ""), "Google\\Chrome\\Application\\chrome.exe", "chrome"),
        (os.environ.get("ProgramFiles", ""), "Microsoft\\Edge\\Application\\msedge.exe", "msedge"),
        (os.environ.get("ProgramFiles(x86)", ""), "Microsoft\\Edge\\Application\\msedge.exe", "msedge"),
        (os.environ.get("LocalAppData", ""), "Microsoft\\Edge\\Application\\msedge.exe", "msedge"),
    ]
    for base, rel, name in candidates:
        if not base:
            continue
        path = os.path.join(base, rel)
        if os.path.exists(path):
            return path, name
    return None, ""


def _print_phone_link_later(port: int, delay: float = 5.0) -> None:
    """Print the phone-control URL once the server is actually up."""
    def _run() -> None:
        time.sleep(delay)
        try:
            import json
            import urllib.request

            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/sync/phone-link")
            with urllib.request.urlopen(req, timeout=5) as resp:
                info = json.loads(resp.read().decode("utf-8", "replace"))
            print(f"[A3THER] Phone control (same Wi-Fi, no install): {info.get('url')}")
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, daemon=True).start()


def _start_remote_server(port: int | None = None, allow_shell: bool = False) -> None:
    """Start the Phase-1 LAN remote-control server (pairing + actions).

    This is the desktop half of the A3THER phone link: it broadcasts the
    device on the LAN, serves pairing codes, and executes authenticated
    actions (open/lock/status) from the paired phone app. Self-contained
    (stdlib only) so it works even if the web stack can't start.
    """
    try:
        from remote_dev.agent_server import start_server as start_remote

        start_remote(port=port or int(os.environ.get("A3THER_REMOTE_PORT", "42872")), allow_shell=allow_shell, background=True)
        print(f"[A3THER] Remote control ready on LAN port {port or 42872} — pair from the phone app.")
    except Exception as exc:  # noqa: BLE001
        print(f"[A3THER] Remote control unavailable: {exc}")


def main() -> int:
    # Force UTF-8 output so the ASCII banners never crash on cp1252 consoles
    # (matters when stdout is redirected to a file or a pipe).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    parser = argparse.ArgumentParser(description="A.3.T.H.E.R. desktop launcher")
    parser.add_argument("--port", type=int, default=int(os.environ.get("A3THER_PORT", "8000")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--headless", action="store_true", help="server only, minimal output")
    parser.add_argument(
        "--remote-port",
        type=int,
        default=int(os.environ.get("A3THER_REMOTE_PORT", "42872")),
        help="LAN port for the phone remote-control server",
    )
    parser.add_argument(
        "--no-remote",
        action="store_true",
        help="disable the LAN remote-control server",
    )
    parser.add_argument(
        "--allow-remote-shell",
        action="store_true",
        help="let the paired phone run shell commands on this PC",
    )
    args = parser.parse_args()

    if not args.headless:
        print("╔════════════════════════════════════════════╗")
        print("║        A.3.T.H.E.R. — DESKTOP MODE         ║")
        print("╚════════════════════════════════════════════╝")

    _ensure_data_dir()
    _ensure_bundle_mirror()
    _run_first_run()

    if not args.headless:
        print(f"[A3THER] HUD: http://127.0.0.1:{args.port}/")
        _print_phone_link_later(args.port)
        if not args.no_browser:
            _open_browser_later(f"http://127.0.0.1:{args.port}/")

    os.environ["A3THER_PORT"] = str(args.port)

    # Phase-1 LAN remote control: discoverable, pairable, phone-driven.
    if not args.no_remote:
        _start_remote_server(args.remote_port, allow_shell=args.allow_remote_shell)

    # Import AFTER env is set so /api/sync/phone-link reports the right port.
    import backend.api.server as app_module  # noqa: PLC0415

    try:
        import uvicorn  # noqa: PLC0415

        uvicorn.run(
            app_module.app,
            host=args.host,
            port=args.port,
            log_level="warning" if args.headless else "info",
        )
    except KeyboardInterrupt:
        print("\n[A3THER] Shutting down.")
    except Exception as exc:  # noqa: BLE001
        print(f"[A3THER] Server error: {exc}")
        if args.port == 8000:
            print("[A3THER] Port 8000 busy? Try: launcher --port 9000")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
