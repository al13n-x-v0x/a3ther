"""
core/popup.py — the A3THER talking popup (Windows).

A Claude-macOS / JARVIS-style floating overlay: press Alt+F8 (or let the
assistant speak) and a small always-on-top panel appears — sometimes from
the bottom, sometimes from the top, left or right — showing the A3THER
logo as a "talking" avatar:

* when the assistant is speaking, glowing voice rings pulse around the
  logo and the transcript line shows the words as they're said;
* one-tap actions: Open HUD · Voice · Mode · Shot · Status;
* Esc or ✕ hides it; it auto-hides a few seconds after speech stops.

Design notes
------------
* Own daemon Tk thread + a command queue (``show``/``hide``/``say`` are
  thread-safe), so it never blocks the backend or the hotkey dispatcher.
* Slide-in is a real geometry animation on that thread (``after()`` steps),
  entering from a random edge each time.
* ``say(text)`` is the public hook the TTS pipeline calls when it speaks,
  so the popup doubles as a "who's talking" HUD overlay.
"""

from __future__ import annotations

import queue
import random
import threading
from typing import Optional

# --------------------------------------------------------------------------- #
# Lazily import tkinter so the module imports cleanly even where tk is missing
# --------------------------------------------------------------------------- #
try:
    import tkinter as _tk  # noqa: PLC0415
except Exception:  # noqa: BLE001
    _tk = None

_WIDTH = 380
_HEIGHT = 210
_KEY_COLOR = "#010101"  # transparent key — the window bg colour on Windows

_cmd_q: "queue.Queue[str]" = queue.Queue()
_root: Optional["_tk.Tk"] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()
_visible = False
_speaking = False


def available() -> bool:
    """True when tkinter is importable (the popup can actually run)."""
    return _tk is not None


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def _load_logo_photo(root) -> Optional[object]:
    try:
        from core.resources import asset_path  # noqa: PLC0415

        photo = _tk.PhotoImage(file=asset_path("logo_popup.png"))
        return photo
    except Exception:  # noqa: BLE001 — fall back to a drawn glyph
        canvas = _tk.Canvas(root, width=64, height=64)
        canvas.create_oval(4, 4, 60, 60, fill="#0a1220", outline="#00d2ff", width=2)
        canvas.create_line(22, 46, 32, 18, fill="#00d2ff", width=6, capstyle="round")
        canvas.create_line(32, 18, 42, 46, fill="#00d2ff", width=6, capstyle="round")
        canvas.create_line(25, 32, 39, 32, fill="#00d2ff", width=5)
        canvas.postscript  # noqa: B018 — keep the branch referenced
        return None


def _gradient(canvas, x0: int, y0: int, w: int, h: int, top: str, bottom: str) -> None:
    """Vertical color gradient via thin rectangles (no PIL needed)."""
    try:
        def _hex(hx):
            hx = hx.lstrip("#")
            return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))

        t, b = _hex(top), _hex(bottom)
        strips = 18
        for i in range(strips):
            f = i / max(strips - 1, 1)
            col = tuple(round(t[c] + (b[c] - t[c]) * f) for c in range(3))
            canvas.create_rectangle(x0, y0 + int(h * i / strips), x0 + w,
                                    y0 + int(h * (i + 1) / strips) + 1,
                                    fill=f"#{col[0]:02x}{col[1]:02x}{col[2]:02x}", outline="")
    except Exception:  # noqa: BLE001
        canvas.create_rectangle(x0, y0, x0 + w, y0 + h, fill="#0a0f1a", outline="")


def _build_root() -> "_tk.Tk":
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
    # Glass gradient panel — interpolated strips (dark at the bottom,
    # cyan-tinged at the top) so the popup reads as a lit glass chip.
    _gradient(canvas, 0, 0, _WIDTH, _HEIGHT, "#12203a", "#060a14")
    canvas.create_line(4, 3, _WIDTH - 4, 3, fill="#00d2ff", width=1)  # top glow line
    canvas.create_rectangle(2, 2, _WIDTH - 2, _HEIGHT - 2, outline="#1b2a44", width=1)

    # Logo (talking avatar) at the left.
    photo = _load_logo_photo(root)
    if photo is not None:
        canvas.photo = photo  # keep a reference — tkinter drops images otherwise
        canvas.create_image(50, 62, image=photo)
    else:
        canvas.create_text(50, 62, text="A", fill="#00d2ff", font=("Segoe UI", 40, "bold"))

    # Voice rings — ovals around the logo; the speaking loop pulses them.
    # Created first (or lowered) so they glow BEHIND the logo, not on top.
    rings = []
    for i in range(3):
        rings.append(canvas.create_oval(36, 48, 64, 76, outline="#00d2ff", width=1))
    for item in rings:
        canvas.tag_lower(item)
    canvas.ring_items = rings

    # Name + status — the name gets a soft glow halo behind it.
    canvas.create_text(
        104, 26, anchor="w", fill="#123a55", font=("Segoe UI", 14, "bold"), width=4
    )
    canvas.create_text(
        104, 26, anchor="w", fill="#e8f6ff", font=("Segoe UI", 13, "bold"), text="A.3.T.H.E.R."
    )
    mode_sub = canvas.create_text(
        104, 46, anchor="w", fill="#7a94b8", font=("Segoe UI", 9), text="…",
    )
    # Transcript — the words the assistant is saying (wraps on 2 lines).
    transcript = canvas.create_text(
        104, 84, anchor="nw", fill="#cfe8ff", font=("Segoe UI", 10),
        width=_WIDTH - 130, justify="left",
    )
    canvas.refs = {"transcript": transcript, "mode_sub": mode_sub}

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
    canvas.tag_bind("all", "<Button-1>", lambda e: (_dispatch("toggle_hud"), hide()))

    def _refresh_mode() -> None:
        try:
            from core.modes import ModeManager  # noqa: PLC0415

            meta = ModeManager().get_mode_metadata()
            canvas.itemconfigure(mode_sub, text=f"{meta['name'].upper()}  ·  {meta.get('vibe', '').upper()}")
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
# Animation helpers (run on the Tk thread)
# --------------------------------------------------------------------------- #
def _target_rect(root) -> tuple[int, int, int, int]:
    """Target geometry near the cursor, clamped to the screen."""
    try:
        x = root.winfo_pointerx() - _WIDTH // 2
        y = root.winfo_pointery() - 20
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = max(8, min(x, sw - _WIDTH - 8))
        y = max(8, min(y, sh - _HEIGHT - 8))
    except Exception:  # noqa: BLE001
        x, y = 60, 60
    return x, y, _WIDTH, _HEIGHT


def _slide_in(root, x: int, y: int, w: int, h: int) -> None:
    """Animate the window in from a random edge (sometimes down, sometimes up).

    Slides AND fades (window alpha) with a smoothstep ease.
    """
    edge = random.choice(("up", "down", "left", "right"))
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    starts = {
        "up": (x, -h - 4),
        "down": (x, sh + 4),
        "left": (-w - 4, y),
        "right": (sw + 4, y),
    }
    sx, sy = starts[edge]
    steps = 14
    try:
        root.attributes("-alpha", 0.0)
    except Exception:  # noqa: BLE001
        pass
    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)  # smoothstep
        cx = round(sx + (x - sx) * ease)
        cy = round(sy + (y - sy) * ease)
        root.geometry(f"{w}x{h}+{cx}+{cy}")
        try:
            root.attributes("-alpha", round(min(1.0, 0.35 + 0.65 * ease), 2))
        except Exception:  # noqa: BLE001
            pass
        try:
            root.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        _tk_root_wait(0.012)


def _tk_root_wait(seconds: float) -> None:
    try:
        import time

        time.sleep(seconds)
    except Exception:  # noqa: BLE001
        pass


def _pulse_rings(root, canvas, active: bool) -> None:
    """Voice rings: fast & wide while speaking, a slow alive pulse otherwise.

    The loop keeps running while the popup is visible so the avatar always
    feels "on"; the amplitude/period scale with the speaking state.
    """
    import math  # noqa: PLC0415

    def _frame(i: int = 0) -> None:
        items = getattr(canvas, "ring_items", [])
        cx, cy = 50, 62
        if not _visible and not _speaking:
            return
        if _speaking:
            period, base, swing = 30, 16, 26
            delay = 40
        else:
            period, base, swing = 72, 12, 5   # slow idle pulse
            delay = 60
        phase = (i % period) / period
        for k, item in enumerate(items):
            rad = base + swing * (0.5 - 0.5 * math.cos(2 * math.pi * (phase + k * 0.14)))
            try:
                canvas.coords(item, cx - rad, cy - rad, cx + rad, cy + rad)
                canvas.itemconfigure(item, width=2 if k == 0 else 1)
            except Exception:  # noqa: BLE001
                pass
        if _visible or _speaking:
            root.after(delay, lambda: _frame(i + 1))

    _frame()


def _show_now(root) -> None:
    global _visible
    try:
        x, y, w, h = _target_rect(root)
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)
        _slide_in(root, x, y, w, h)
        _visible = True
        canvas = _canvas_of(root)
        if canvas is not None:
            _pulse_rings(root, canvas, _speaking)
    except Exception:  # noqa: BLE001
        _visible = False


def _fade_out(root) -> None:
    """Quick fade before withdrawing (avoids a hard pop-off)."""
    try:
        for i in range(5, 0, -1):
            root.attributes("-alpha", round(i / 5, 2))
            root.update_idletasks()
            _tk_root_wait(0.012)
    except Exception:  # noqa: BLE001
        pass
    try:
        root.attributes("-alpha", 1.0)
    except Exception:  # noqa: BLE001
        pass
    root.withdraw()


def _canvas_of(root) -> Optional[object]:
    try:
        return root.winfo_children()[0]  # the Canvas
    except Exception:  # noqa: BLE001
        return None


def _say_now(root, text: str) -> None:
    """Show + update transcript + start the speaking animation."""
    global _visible, _speaking
    canvas = _canvas_of(root)
    if canvas is None:
        return
    _speaking = True
    try:
        refs = getattr(canvas, "refs", {})
        canvas.itemconfigure(refs["transcript"], text=text[:220])
    except Exception:  # noqa: BLE001
        pass
    if not _visible:
        _show_now(root)
    else:
        _pulse_rings(root, canvas, True)


def _stop_speaking_now(root) -> None:
    global _speaking
    _speaking = False
    canvas = _canvas_of(root)
    if canvas is not None:
        _pulse_rings(root, canvas, False)
    # Auto-hide a moment after silence so the popup doesn't linger.
    root.after(3200, _auto_hide_if_quiet)


def _auto_hide_if_quiet() -> None:
    global _visible
    if not _speaking and _visible:
        try:
            _root.withdraw()
            _visible = False
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
                        _fade_out(root)
                        _visible = False
                    elif cmd.startswith("say:"):
                        _say_now(root, cmd[4:])
                    elif cmd == "stopspeaking":
                        _stop_speaking_now(root)
            except queue.Empty:
                pass
            root.after(80, _pump)

        root.after(80, _pump)
        root.mainloop()
    except Exception as exc:  # noqa: BLE001
        print(f"[POPUP] tk loop failed: {exc}")
    finally:
        _root = None


# --------------------------------------------------------------------------- #
# Public API (thread-safe)
# --------------------------------------------------------------------------- #
def show() -> bool:
    """Show the popup near the cursor (slide-in from a random edge)."""
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


def say(text: str) -> None:
    """The assistant is saying ``text`` — show the popup and animate it.

    Called by the TTS pipeline; safe from any thread.
    """
    if not available() or not _speech_enabled():
        return
    if not text:
        return
    # Bounce long text through the queue as one command.
    _cmd_q.put("say:" + text[:220])


def stop_speaking() -> None:
    """The assistant finished talking — stop rings, auto-hide shortly after."""
    _cmd_q.put("stopspeaking")


def is_visible() -> bool:
    return _visible


def _speech_enabled() -> bool:
    """Respect the Settings toggle (Settings → Quick Popup → speech popup)."""
    try:
        from core.ui_settings import get_ui_setting  # noqa: PLC0415

        return bool(get_ui_setting("speech_popup", True))
    except Exception:  # noqa: BLE001
        return True


if __name__ == "__main__":  # pragma: no cover — manual smoke test
    print("available:", available())
    if available():
        show()
        print("popup shown (slide-in). Simulating speech…")
        import time

        say("Hello, this is A3THER. Everything is online and I am ready to help you.")
        time.sleep(4)
        stop_speaking()
        time.sleep(2)
        hide()
        print("done.")
