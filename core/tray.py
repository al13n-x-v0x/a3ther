"""
core/tray.py — A3THER system-tray icon (Windows).

Gives background mode a real presence: a tray icon shows live status
(current mode · hotkeys armed · voice state), and its menu summons the HUD,
toggles the voice, cycles modes, takes screenshots, and quits the app —
so you never *need* Alt+F1.

Design: the menu dispatches through the *same* action registry as the global
hotkeys (``core.hotkeys``), so a menu click and an Alt+F-key do identical
things. ``Quit`` invokes an ``on_quit`` callback the launcher wires to the
running server (uvicorn ``should_exit``); the tray thread is a daemon, so it
never blocks shutdown.

``pystray`` + Pillow are optional — if they're missing the module imports
fine but :func:`start_tray` returns False (background mode simply has no
icon; hotkeys still work).
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Optional

# --------------------------------------------------------------------------- #
# Lazy imports — the module must import cleanly even without pystray/PIL
# --------------------------------------------------------------------------- #
_pystray = None
_PIL_Image = None
_PIL_Draw = None
try:
    import pystray as _pystray
    from PIL import Image as _PIL_Image
    from PIL import ImageDraw as _PIL_Draw
except Exception:  # noqa: BLE001 — optional deps
    _pystray = None

_icon: object | None = None
_icon_lock = threading.Lock()
_on_quit: Optional[Callable[[], None]] = None
_tooltip: str = "A.3.T.H.E.R."


def available() -> bool:
    """True when the tray can actually run (pystray + Pillow installed)."""
    return _pystray is not None and _PIL_Image is not None


def _build_icon_image() -> object:
    """The real A3THER logo (assets/logo_tray.png), resized for the tray.

    Falls back to a drawn cyan 'A' glyph only when the asset is missing.
    """
    try:
        from core.resources import asset_path  # noqa: PLC0415

        img = _PIL_Image.open(asset_path("logo_tray.png")).convert("RGBA")
        return img.resize((64, 64), _PIL_Image.LANCZOS)
    except Exception:  # noqa: BLE001 — fall back to the drawn glyph
        size = 64
        img = _PIL_Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = _PIL_Draw.Draw(img)
        # Rounded-square background.
        draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=14, fill=(5, 8, 15, 255))
        # Glow ring.
        draw.rounded_rectangle([4, 4, size - 4, size - 4], radius=13, outline=(0, 210, 255, 160), width=2)
        # The 'A' — drawn as two angled bars + crossbar (Orbitron-ish feel).
        cyan = (0, 210, 255, 255)
        draw.line([(18, 46), (32, 14)], fill=cyan, width=5)
        draw.line([(32, 14), (46, 46)], fill=cyan, width=5)
        draw.line([(24, 34), (40, 34)], fill=cyan, width=4)
        return img


def _dispatch(action: str) -> None:
    """Run a hotkey-registered action by name (menu = hotkey parity).

    Built-ins are wired lazily so the tray works even when ``--no-hotkeys``
    was passed (the registry may be empty — e.g. when hotkey registration
    failed because the keys are taken).
    """
    try:
        from core.hotkeys import get_hotkey_manager

        mgr = get_hotkey_manager()
        if action not in mgr._actions:  # noqa: SLF001 — same-process registry
            from core.hotkeys import _builtin_actions  # noqa: PLC0415

            _builtin_actions()
        mgr._handle(action)  # noqa: SLF001
    except Exception:  # noqa: BLE001 — tray actions must never crash the thread
        pass


def _refresh_tooltip() -> None:
    """Update the tray tooltip with live status (mode + hotkey state)."""
    global _tooltip
    mode = ""
    try:
        from core.modes import ModeManager

        mode = ModeManager().get_mode_metadata()["name"]
    except Exception:  # noqa: BLE001
        pass
    hk = "hotkeys off"
    try:
        from core.hotkeys import get_hotkey_manager

        hk = "hotkeys armed" if get_hotkey_manager().running else "hotkeys off"
    except Exception:  # noqa: BLE001
        pass
    _tooltip = f"A.3.T.H.E.R. — {mode or 'AI'} · {hk}"
    with _icon_lock:
        icon = _icon
        if icon is not None:
            try:
                icon.title = _tooltip  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass


def update_status() -> None:
    """Public hook — call after a mode/hotkey change to refresh the tooltip."""
    _refresh_tooltip()


def _on_quit_impl() -> None:
    with _icon_lock:
        icon = _icon
        if icon is not None:
            try:
                icon.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
    cb = _on_quit
    if cb:
        cb()


def _build_menu() -> object:
    """pystray.Menu with real actions (parity with the Alt+F-key registry)."""
    Menu = _pystray.Menu
    MenuItem = _pystray.MenuItem

    def _item(action: str, label: str, default: bool = False) -> object:
        return MenuItem(
            label,
            lambda icon, item: _dispatch(action),
            default=default,
        )

    return Menu(
        _item("toggle_hud", "Summon HUD", default=True),
        _item("toggle_voice", "Toggle Voice"),
        _item("cycle_mode", "Cycle Mode"),
        _item("screenshot", "Take Screenshot"),
        _item("status", "Show Status"),
        Menu.SEPARATOR,
        MenuItem("Quit A.3.T.H.E.R.", lambda icon, item: _on_quit_impl()),
    )


def start_tray(on_quit: Optional[Callable[[], None]] = None) -> bool:
    """Start the tray icon on a daemon thread. Returns True when running.

    ``on_quit`` is invoked (on the tray thread) when the user picks Quit —
    wire it to stop the uvicorn server so the whole app exits.
    """
    global _icon, _on_quit
    if not available():
        return False
    _on_quit = on_quit
    _refresh_tooltip()

    def _run() -> None:
        global _icon
        try:
            icon = _pystray.Icon(
                "a3ther",
                _build_icon_image(),
                _tooltip,
                menu=_build_menu(),
            )
            with _icon_lock:
                _icon = icon
            icon.run()  # blocks on the tray message loop (Windows)
        except Exception as exc:  # noqa: BLE001
            print(f"[TRAY] tray icon failed: {exc}")
        finally:
            with _icon_lock:
                _icon = None

    thread = threading.Thread(target=_run, daemon=True, name="a3ther-tray")
    thread.start()
    return True


def stop_tray() -> None:
    """Best-effort stop (idempotent)."""
    with _icon_lock:
        icon = _icon
    if icon is not None:
        try:
            icon.stop()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    print("tray available:", available())
    if available():
        start_tray(on_quit=lambda: print("quit requested"))
        print("tray started — look for the A3THER icon. Ctrl+C to quit.")
        import time

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_tray()
