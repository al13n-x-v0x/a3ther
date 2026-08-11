"""
core/desktop.py — A3THER's native desktop window.

Turns the windowless exe into a real app: instead of spawning a browser tab,
the process itself opens a native window (Edge WebView2 on Windows) that hosts
the HUD at ``http://127.0.0.1:<port>/``.

Lifecycle contract (the "open = backend starts, close = backend stops" rule):

* ``open_hud_window(url, on_closed=...)`` blocks until the window is closed.
* When the user closes the window, ``on_closed`` is invoked first (the caller
  uses it to set ``uvicorn.Server.should_exit = True``), then the function
  returns so the app can shut the backend down and exit.

If pywebview is unavailable or the platform backend fails to start (no WebView2
runtime, sandboxed environment, etc.), it transparently falls back to opening
the HUD in the user's default browser so the app is never dead in the water.
"""

from __future__ import annotations

import time
from urllib.request import urlopen

__all__ = ["open_hud_window", "wait_ready"]


def wait_ready(url: str, timeout: float = 25.0) -> bool:
    """Block until ``url`` answers HTTP, or ``timeout`` seconds pass.

    Used to make sure uvicorn is accepting connections before we mount the
    window, so the HUD never shows a connection-error page.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2):  # noqa: S310 — loopback only
                return True
        except Exception:  # noqa: BLE001 — server not up yet, keep polling
            time.sleep(0.35)
    return False


def _open_browser(url: str) -> None:
    """Last-resort fallback: open the HUD in the default browser."""
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass


def open_hud_window(
    url: str,
    on_closed: callable | None = None,
    title: str = "A3THER — Desktop HUD",
    width: int = 1440,
    height: int = 900,
) -> bool:
    """Open the HUD in a native window owned by this process.

    Returns True if the window opened (and we waited for it to close), False
    if we fell back to the browser instead.
    """
    try:
        import webview  # noqa: PLC0415 — heavy, lazy import

        window = webview.create_window(
            title,
            url,
            width=width,
            height=height,
            min_size=(1024, 640),
            background_color="#05070d",
        )

        if on_closed is not None:
            window.events.closed += on_closed

        # Blocks until every window is closed. On Windows this runs the
        # WebView2 message loop on the calling thread — fine here since the
        # backend lives in its own thread (see main.py).
        webview.start(gui=None, debug=False)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[desktop] native window unavailable ({exc}) — opening browser")
        _open_browser(url)
        return False
