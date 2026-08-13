"""
core/hotkeys.py — A3THER global hotkeys (Windows).

Registers system-wide hotkeys (``Alt+F1`` … ``Alt+F12`` by default) so A3THER
can be summoned and driven from *any* app, even when the HUD window is hidden
or running in the background. Pure ctypes — no third-party deps, works from
the frozen exe.

Default bindings (all configurable via :func:`set_bindings`):

============  ============================================
Hotkey        Action
============  ============================================
Alt+F1        Toggle HUD — open/raise the dashboard
Alt+F2        Toggle voice — start/stop wake-word listening
Alt+F3        Screenshot — save a PNG to the data folder
Alt+F4        Cycle mode — humanoid → gaming → dev → …
Alt+F5        Lock the PC (Win+L)
Alt+F6        Push status — print/log current mode + health
Alt+F7        Open the Hub (experiments page)
============  ============================================

The hotkey thread is a daemon: it never blocks shutdown, and unregistering
happens on exit automatically.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# --------------------------------------------------------------------------- #
# Win32 constants (kept local so the module stays self-contained)
# --------------------------------------------------------------------------- #
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_PM_REMOVE = 0x0001

#: Virtual-key codes for the F-keys (and a few extras).
VK = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "A": 0x41, "S": 0x53, "D": 0x44, "L": 0x4C, "T": 0x54, "M": 0x4D,
}

#: Default action catalogue — what each built-in action name does.
ACTION_CATALOG: Dict[str, str] = {
    "toggle_hud": "Open / raise the A3THER dashboard",
    "toggle_voice": "Start or stop wake-word listening",
    "screenshot": "Save a screenshot PNG to the A3THER data folder",
    "cycle_mode": "Cycle through modes (humanoid → gaming → dev → …)",
    "lock_pc": "Lock the PC (Win+L)",
    "status": "Log current mode + system health to the terminal",
    "open_hub": "Open the Hub experiments page",
    "show_popup": "Show the A3THER quick-launch popup (logo + actions)",
}


def _data_dir() -> Path:
    """Resolve the settings file location (survives dev + frozen)."""
    try:
        from config.paths import get_data_dir  # noqa: PLC0415 — repo helper

        base = get_data_dir()
    except Exception:  # noqa: BLE001
        base = Path.home() / ".a3ther"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _settings_path() -> Path:
    return _data_dir() / "hotkeys.json"


#: Default binding: action name → "Alt+F<n>".
DEFAULT_BINDINGS: Dict[str, str] = {
    "toggle_hud": "Alt+F1",
    "toggle_voice": "Alt+F2",
    "screenshot": "Alt+F3",
    "cycle_mode": "Alt+F4",
    "lock_pc": "Alt+F5",
    "status": "Alt+F6",
    "open_hub": "Alt+F7",
    "show_popup": "Alt+F8",
}


def parse_hotkey(spec: str) -> Optional[tuple[int, int]]:
    """Parse ``"Alt+Ctrl+F5"`` → (modifiers, vk). Returns None if invalid."""
    parts = [p.strip() for p in str(spec or "").split("+")]
    if not parts:
        return None
    mods = 0
    key = parts[-1].upper()
    for m in parts[:-1]:
        m = m.upper()
        if m in ("ALT", "MENU"):
            mods |= _MOD_ALT
        elif m in ("CTRL", "CONTROL"):
            mods |= _MOD_CONTROL
        elif m in ("SHIFT",):
            mods |= _MOD_SHIFT
        elif m in ("WIN", "SUPER", "META"):
            mods |= _MOD_WIN
        else:
            return None
    if key not in VK:
        return None
    return (mods, VK[key])


def format_hotkey(mods: int, vk: int) -> str:
    """Inverse of :func:`parse_hotkey` — for display in the HUD."""
    names = []
    if mods & _MOD_WIN:
        names.append("Win")
    if mods & _MOD_CONTROL:
        names.append("Ctrl")
    if mods & _MOD_SHIFT:
        names.append("Shift")
    if mods & _MOD_ALT:
        names.append("Alt")
    key = next((k for k, v in VK.items() if v == vk), f"VK({vk})")
    names.append(key)
    return "+".join(names)


class HotkeyManager:
    """Register/consume global hotkeys on a background thread.

    Actions are plain callables: ``register_action(name, callable)``. The
    default built-ins are wired up lazily by :func:`start_hotkeys`.
    """

    def __init__(self) -> None:
        self._actions: Dict[str, Callable[[], Any]] = {}
        self._bindings: Dict[str, str] = dict(DEFAULT_BINDINGS)
        self._reverse: Dict[int, str] = {}  # hotkey id → action name
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._last_fire: Dict[int, float] = {}
        self._debounce_s = 0.35

    # ---------------- configuration ---------------- #
    def load(self, path: Optional[Path] = None) -> None:
        """Load bindings from JSON (falling back to defaults)."""
        p = path or _settings_path()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            bindings = raw.get("bindings")
            if isinstance(bindings, dict):
                cleaned: Dict[str, str] = {}
                for action, spec in bindings.items():
                    if action in ACTION_CATALOG and parse_hotkey(spec):
                        cleaned[action] = spec
                if cleaned:
                    self._bindings = cleaned
        except Exception:  # noqa: BLE001 — corrupted/missing file → defaults
            pass

    def save(self, path: Optional[Path] = None) -> None:
        p = path or _settings_path()
        try:
            p.write_text(
                json.dumps({"bindings": self._bindings}, indent=2),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    def set_bindings(self, bindings: dict) -> list[str]:
        """Apply new bindings (action → spec). Returns rejected specs."""
        rejected = []
        cleaned: Dict[str, str] = {}
        for action, spec in bindings.items():
            if action in ACTION_CATALOG and parse_hotkey(spec):
                cleaned[action] = spec
            else:
                rejected.append(f"{action}={spec}")
        self._bindings = cleaned
        self.save()
        return rejected

    def get_bindings(self) -> dict:
        return dict(self._bindings)

    def register_action(self, name: str, fn: Callable[[], Any]) -> None:
        self._actions[name] = fn

    # ---------------- registration ---------------- #
    def _register_all(self) -> list[tuple[int, str, str]]:
        """Register every binding; returns [(id, action, spec)] failures."""
        self._reverse.clear()
        failures = []
        for i, (action, spec) in enumerate(self._bindings.items()):
            parsed = parse_hotkey(spec)
            if parsed is None:
                failures.append((i, action, spec))
                continue
            mods, vk = parsed
            hotkey_id = i + 1  # ids are 1-based, non-zero
            ok = ctypes.windll.user32.RegisterHotKey(None, hotkey_id, mods, vk)
            if ok:
                self._reverse[hotkey_id] = action
            else:
                failures.append((hotkey_id, action, spec))
        return failures

    def _unregister_all(self) -> None:
        for hotkey_id in self._reverse:
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self._reverse.clear()

    # ---------------- dispatch ---------------- #
    def _handle(self, action: str) -> None:
        fn = self._actions.get(action)
        if fn is None:
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — a hotkey action must never kill the thread
            print(f"[HOTKEY] action '{action}' failed: {exc}")

    def _consume_loop(self) -> None:
        msg = wt.MSG()
        while self._running:
            # Blocking peek — returns immediately when a hotkey arrives.
            res = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res == 0:  # WM_QUIT
                break
            if res == -1:
                time.sleep(0.05)
                continue
            if msg.message == _WM_HOTKEY:
                hotkey_id = msg.wParam
                action = self._reverse.get(hotkey_id)
                if action:
                    # Debounce: ignore repeats within the window (RegisterHotKey
                    # with MOD_NOREPEAT already helps, but belt-and-braces).
                    now = time.monotonic()
                    if now - self._last_fire.get(hotkey_id, 0) > self._debounce_s:
                        self._last_fire[hotkey_id] = now
                        self._handle(action)
            else:
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))

    # ---------------- lifecycle ---------------- #
    def start(self) -> bool:
        """Register bindings + start the background thread. Idempotent."""
        if self._running:
            return True
        if os.name != "nt":
            return False
        try:
            failures = self._register_all()
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] registration failed: {exc}")
            return False
        for _hid, action, spec in failures:
            print(f"[HOTKEY] could not register {action} ({spec}) — already in use?")
        self._running = True
        self._thread = threading.Thread(
            target=self._consume_loop, daemon=True, name="a3ther-hotkeys"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        try:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread.ident if self._thread else 0, _WM_HOTKEY + 1000, 0, 0
            )
        except Exception:  # noqa: BLE001
            pass
        self._unregister_all()

    @property
    def running(self) -> bool:
        return self._running


# --------------------------------------------------------------------------- #
# Module-level singleton + built-in wiring
# --------------------------------------------------------------------------- #
_manager = HotkeyManager()


def get_hotkey_manager() -> HotkeyManager:
    return _manager


def _builtin_actions() -> None:
    """Wire the built-in actions (lazy — imports the heavy bits on demand)."""
    m = _manager

    def _toggle_hud() -> None:
        try:
            port = int(os.environ.get("A3THER_PORT", "8000"))
            url = f"http://127.0.0.1:{port}/"
            # Re-open the dashboard in the default browser (raises it if open).
            import webbrowser  # noqa: PLC0415

            webbrowser.open(url)
            print(f"[HOTKEY] HUD → {url}")
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] toggle_hud failed: {exc}")

    def _toggle_voice() -> None:
        try:
            from voice.pipeline import get_voice_pipeline  # noqa: PLC0415

            pipeline = get_voice_pipeline()
            if pipeline.running:
                pipeline.stop()
                print("[HOTKEY] voice listening stopped")
            else:
                pipeline.start()
                print("[HOTKEY] voice listening started")
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] toggle_voice failed: {exc}")

    def _screenshot() -> None:
        try:
            from PIL import ImageGrab  # type: ignore  # noqa: PLC0415

            shot = ImageGrab.grab()
            out = _data_dir() / "screenshots"
            out.mkdir(parents=True, exist_ok=True)
            path = out / time.strftime("shot-%Y%m%d-%H%M%S.png")
            shot.save(path)
            print(f"[HOTKEY] screenshot saved → {path}")
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] screenshot failed (Pillow installed?): {exc}")

    def _cycle_mode() -> None:
        try:
            from core.modes import ModeManager, MODE_REGISTRY  # noqa: PLC0415

            mgr = ModeManager()
            order = [k for k in ("humanoid", "gaming", "dev", "ai", "chill", "research", "mentor") if k in MODE_REGISTRY]
            current = mgr.get_mode()
            nxt = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
            mgr.set_mode(nxt)
            # Persist so the backend picks it up on next init.
            try:
                from core.ui_settings import save_ui_setting  # noqa: PLC0415

                save_ui_setting("mode", nxt)
            except Exception:  # noqa: BLE001
                pass
            meta = mgr.get_mode_metadata(nxt)
            # ASCII-only print: the arrow crashes on cp1252 consoles.
            print(f"[HOTKEY] mode -> {meta['name']} ({meta['vibe']})")
            try:
                from core.engine_state import push_event  # noqa: PLC0415

                push_event(f"[HOTKEY] mode → {meta['name']}")
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] cycle_mode failed: {exc}")

    def _lock_pc() -> None:
        try:
            ctypes.windll.user32.LockWorkStation()
            print("[HOTKEY] PC locked")
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] lock_pc failed: {exc}")

    def _status() -> None:
        try:
            from core.modes import ModeManager  # noqa: PLC0415
            import platform  # noqa: PLC0415

            mgr = ModeManager()
            meta = mgr.get_mode_metadata()
            print(
                f"[HOTKEY] A3THER · mode={meta['name']} · "
                f"platform={platform.system()} {platform.release()} · "
                f"hotkeys={'armed' if m.running else 'off'}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] status failed: {exc}")

    def _open_hub() -> None:
        try:
            port = int(os.environ.get("A3THER_PORT", "8000"))
            import webbrowser  # noqa: PLC0415

            webbrowser.open(f"http://127.0.0.1:{port}/hub")
            print("[HOTKEY] Hub opened")
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] open_hub failed: {exc}")

    def _show_popup() -> None:
        try:
            from core.popup import show  # noqa: PLC0415

            if show():
                print("[HOTKEY] popup shown — click an action or press Esc")
            else:
                print("[HOTKEY] popup unavailable (tkinter missing?)")
        except Exception as exc:  # noqa: BLE001
            print(f"[HOTKEY] show_popup failed: {exc}")

    m.register_action("toggle_hud", _toggle_hud)
    m.register_action("toggle_voice", _toggle_voice)
    m.register_action("screenshot", _screenshot)
    m.register_action("cycle_mode", _cycle_mode)
    m.register_action("lock_pc", _lock_pc)
    m.register_action("status", _status)
    m.register_action("open_hub", _open_hub)
    m.register_action("show_popup", _show_popup)


def start_hotkeys() -> bool:
    """Load bindings, wire built-ins, start the background thread."""
    m = get_hotkey_manager()
    m.load()
    _builtin_actions()
    ok = m.start()
    print(f"[HOTKEY] global hotkeys {'armed' if ok else 'unavailable (non-Windows or no bindings)'}"
          if ok else "[HOTKEY] global hotkeys unavailable on this platform")
    return ok


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    start_hotkeys()
    print("Hotkeys armed — press Alt+F1..F7. Ctrl+C to quit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        get_hotkey_manager().stop()
