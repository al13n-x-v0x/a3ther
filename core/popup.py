"""
core/popup.py — A3THER quick-launch popup (Windows).

A Claude-macOS-style floating overlay: press a hotkey (default Alt+F8) and a
small always-on-top dark panel appears near the cursor showing the A3THER
logo, the current mode, and one-tap actions — Summon HUD, Toggle Voice,
Cycle Mode, Screenshot, Status. Esc or the ✕ hides it.

Design notes
------------
* Runs on its own daemon thread with a private ``Tk`` root, so it never
  touches the backend's threads and never blocks the hotkey dispatcher.
* ``show()`` / ``hide()`` are thread-safe: they push commands onto a queue
  that the Tk thread polls with ``after()``.
* The logo + panel are drawn with the ``Canvas`` widget (no PIL dependency
  at runtime) and the window is frameless + always-on-top + transparent
  background via ``-transparentcolor`` keying, so it reads as a floating
  chip like Claude / Whisper Voice OS.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

# --------------------------------------------------------------------------- #
# Lazily import tkinter so the module imports cleanly even where tk is missing
# --------------------------------------------------------------------------- #
try:
    import tkinter as _tk  # noqa: PLC0415
except Exception:  # noqa: BLE001
    _tk = None

_WIDTH = 320
_HEIGHT = 150
_KEY_COLOR = "#010101"  # transparent key — the window bg colour on Windows

_cmd_q: "queue.Queue[str]" = queue.Queue()
_root: Optional["_tk.Tk"] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()
_visible = False


def available() -> bool:
    """True when tkinter is importable (the popup can actually run)."""
    return _tk is not None


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def _draw_logo(canvas) -> None:
    """Draw the A3THER glyph: dark rounded chip, glowing cyan 'A'."""
    w, h = 64, 64
    x0, y0 = 16, 16
    canvas.create_oval(x0, y0, x0 + w, y0 + h, fill="#0a1220", outline="#00d2ff", width=2)
    # Glow: two faint rings (6-digit hex only — tkinter rejects #rrggbbaa).
    canvas.create_oval(x0 - 4, y0 - 4, x0 + w + 4, y0 + h + 4, outline="#2a7a95", width=1)
    canvas.create_oval(x0 - 9, y0 - 9, x0 + w + 9, y0 + h + 9, outline="#1c5368", width=1)
    # The 'A' — angled bars + crossbar.
    cx, cy = x0 + w / 2, y0 + h / 2
    canvas.create_line(cx - 15, cy + 20, cx, cy - 18, fill="#00d2ff", width=6, capstyle="round")
    canvas.create_line(cx, cy - 18, cx + 15, cy + 20, fill="#00d2ff", width=6, capstyle="round")
    canvas.create_line(cx - 8, cy + 4, cx + 8, cy + 4, fill="#00d2ff", width=5)


def _build_root() -> "_tk.Tk":
    global _visible
    root = _tk.Tk()
    root.withdraw()
    root.overrideredirect(True)  # frameless
    root.attributes("-topmost", True)
    try:
        root.attributes("-transparentcolor", _KEY_COLOR)  # Windows: key → see-through
    except Exception:  # noqa: BLE001
        pass
    root.configure(bg=_KEY_COLOR)

    canvas = _tk.Canvas(root, width=_WIDTH, height=_HEIGHT, bg=_KEY_COLOR, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    # Panel (dark, rounded feel via plain rect on transparent bg).
    canvas.create_rectangle(2, 2, _WIDTH - 2, _HEIGHT - 2, fill="#0a0f1a", outline="#1b2a44", width=1)

    _draw_logo(canvas)

    # Mode label.
    mode_text = canvas.create_text(
        100, 34, anchor="w", fill="#e8f6ff",
        font=("Segoe UI", 12, "bold"),
        text="A.3.T.H.E.R.",
    )
    mode_sub = canvas.create_text(
        100, 54, anchor="w", fill="#7a94b8",
        font=("Segoe UI", 9),
        text="…",
    )

    # Close button (✕).
    close_btn = _tk.Label(root, text="✕", bg="#0a0f1a", fg="#7a94b8", font=("Segoe UI", 11), cursor="hand2")
    close_btn.place(x=_WIDTH - 30, y=6, width=22, height=22)

    # Action buttons (hotkey parity via the shared registry).
    actions = [
        ("OPEN HUD", "toggle_hud"),
        ("VOICE", "toggle_voice"),
        ("MODE", "cycle_mode"),
        ("SHOT", "screenshot"),
        ("STATUS", "status"),
    ]
    btn_w = (_WIDTH - 40) // len(actions)
    for i, (label, action) in enumerate(actions):
        btn = _tk.Label(
            root, text=label, bg="#101c30", fg="#9fd8ff",
            font=("Segoe UI", 8, "bold"), cursor="hand2",
            borderwidth=1, relief="flat",
        )
        btn.place(x=14 + i * btn_w, y=_HEIGHT - 44, width=btn_w - 6, height=30)
        btn.bind("<Button-1>", lambda e, a=action: (_dispatch(a), hide()))
        btn.bind("<Enter>", lambda e, b=btn: b.configure(bg="#16304d"))
        btn.bind("<Leave>", lambda e, b=btn: b.configure(bg="#101c30"))

    close_btn.bind("<Button-1>", lambda e: hide())
    root.bind("<Escape>", lambda e: hide())
    # Clicking the logo area = summon the HUD.
    canvas.tag_bind("all", "<Button-1>", lambda e: (_dispatch("toggle_hud"), hide()))

    def _refresh_mode() -> None:
        try:
            from core.modes import ModeManager  # noqa: PLC0415

            meta = ModeManager().get_mode_metadata()
            canvas.itemconfigure(mode_text, text=f"A.3.T.H.E.R.  ·  {meta['name']}")
            canvas.itemconfigure(mode_sub, text=f"{meta.get('vibe', '').upper()}  ·  Alt+F8 to hide")
        except Exception:  # noqa: BLE001
            pass

    _refresh_mode()
    root.after(3000, _refresh_mode)

    return root


def _dispatch(action: str) -> None:
    """Run a hotkey-registered action by name (same registry as the tray)."""
    try:
        from core.hotkeys import get_hotkey_manager, _builtin_actions  # noqa: PLC0415

        mgr = get_hotkey_manager()
        if action not in mgr._actions:  # noqa: SLF001 — same-process registry
            _builtin_actions()
        mgr._handle(action)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Tk thread + queue pump
# --------------------------------------------------------------------------- #
def _tk_loop() -> None:
    global _root
    try:
        root = _build_root()
        _root = root

        def _pump() -> None:
            global _visible
            try:
                while True:
                    cmd = _cmd_q.get_nowait()
                    if cmd == "show":
                        _show_now(root)
                    elif cmd == "hide":
                        root.withdraw()
                        _visible = False
            except queue.Empty:
                pass
            root.after(80, _pump)

        root.after(80, _pump)
        root.mainloop()
    except Exception as exc:  # noqa: BLE001
        print(f"[POPUP] tk loop failed: {exc}")
    finally:
        _root = None


def _show_now(root) -> None:
    global _visible
    try:
        # Center near the cursor when possible.
        x = root.winfo_pointerx() - _WIDTH // 2
        y = root.winfo_pointery() - 20
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = max(8, min(x, sw - _WIDTH - 8))
        y = max(8, min(y, sh - _HEIGHT - 8))
        root.geometry(f"{_WIDTH}x{_HEIGHT}+{x}+{y}")
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        _visible = True
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Public API (thread-safe)
# --------------------------------------------------------------------------- #
def show() -> bool:
    """Show the popup near the cursor. Spawns the Tk thread on first use."""
    global _thread
    if not available():
        return False
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_tk_loop, daemon=True, name="a3ther-popup")
            _thread.start()
    _cmd_q.put("show")
    return True


def hide() -> None:
    _cmd_q.put("hide")


def is_visible() -> bool:
    return _visible


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    print("available:", available())
    if available():
        show()
        print("popup shown — Esc or ✕ to hide. Ctrl+C to quit.")
        import time

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            hide()
