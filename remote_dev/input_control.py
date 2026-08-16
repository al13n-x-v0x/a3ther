"""
remote_dev/input_control.py — remote mouse & keyboard input (Windows).

Turns remote input events into REAL input on this PC via ctypes (user32):
- mouse move / click / drag / wheel (SetCursorPos + mouse_event / SendInput)
- keyboard text + special keys (SendInput with KEYEVENTF_UNICODE)

Coordinates are NORMALIZED (0..1) relative to the streamed screen, so the
phone/browser never needs to know the real resolution — the server scales
to the actual monitor size. Pure stdlib (ctypes) — no dependencies.

Every call is wrapped so a bad event can never crash the server thread.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time

log = logging.getLogger("a3ther.remote.input")

# --- user32 constants -------------------------------------------------------
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004

_VK_MAP = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "backspace": 0x08, "space": 0x20,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C,
    "ctrl": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": 0x12, "lalt": 0xA4, "ralt": 0xA5,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "capslock": 0x14, "printscreen": 0x2C, "pause": 0x13,
}

_USER32 = ctypes.windll.user32


class _INPUT(ctypes.Structure):
    class _UNION(ctypes.Union):
        class _MOUSE(ctypes.Structure):
            _fields_ = [
                ("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
            ]

        class _KEYBD(ctypes.Structure):
            _fields_ = [
                ("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
            ]

        _fields_ = [("mi", _MOUSE), ("ki", _KEYBD)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _UNION)]


def _send_input(*inputs: _INPUT) -> bool:
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    sent = _USER32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(_INPUT))
    return sent == n


def screen_size() -> tuple[int, int] | None:
    """Primary-monitor size in pixels (what the stream is captured from)."""
    try:
        return _USER32.GetSystemMetrics(0), _USER32.GetSystemMetrics(1)
    except Exception:  # noqa: BLE001
        return None


def _scale(x: float | None, y: float | None) -> tuple[int, int] | None:
    """Normalized (0..1) → absolute pixels on the primary monitor."""
    size = screen_size()
    if not size:
        return None
    w, h = size
    px = int(round((x or 0.5) * w))
    py = int(round((y or 0.5) * h))
    return max(0, min(px, w - 1)), max(0, min(py, h - 1))


def _click(button: str, down: bool) -> None:
    flags = {
        ("left", True): _MOUSEEVENTF_LEFTDOWN, ("left", False): _MOUSEEVENTF_LEFTUP,
        ("right", True): _MOUSEEVENTF_RIGHTDOWN, ("right", False): _MOUSEEVENTF_RIGHTUP,
        ("middle", True): _MOUSEEVENTF_MIDDLEDOWN, ("middle", False): _MOUSEEVENTF_MIDDLEUP,
    }.get((button, down))
    if flags is None:
        return
    _USER32.mouse_event(flags, 0, 0, 0, 0)


# --------------------------------------------------------------------------- #
# Public API — every function returns a dict {"ok": bool, "error": str|None}
# --------------------------------------------------------------------------- #


def move(x: float, y: float) -> dict:
    pos = _scale(x, y)
    if not pos:
        return {"ok": False, "error": "cannot read screen size"}
    _USER32.SetCursorPos(*pos)
    return {"ok": True}


def click(x: float, y: float, button: str = "left") -> dict:
    pos = _scale(x, y)
    if not pos:
        return {"ok": False, "error": "cannot read screen size"}
    _USER32.SetCursorPos(*pos)
    time.sleep(0.02)
    _click(button, True)
    time.sleep(0.03)
    _click(button, False)
    return {"ok": True}


def button(button: str, down: bool) -> dict:
    """Raw mouse button press/release (for drags)."""
    _click(button, down)
    return {"ok": True}


def double_click(x: float, y: float, button: str = "left") -> dict:
    click(x, y, button)
    time.sleep(0.06)
    click(x, y, button)
    return {"ok": True}


def wheel(dy: int) -> dict:
    """Vertical scroll (positive = up)."""
    _USER32.mouse_event(_MOUSEEVENTF_WHEEL, 0, 0, int(dy * 120), 0)
    return {"ok": True}


def text(words: str) -> dict:
    """Type a string using Unicode keyboard events (handles any charset)."""
    if not words:
        return {"ok": True}
    for ch in words:
        inputs = [
            _INPUT(type=_INPUT_KEYBOARD, ki=_INPUT._UNION._KEYBD(wVk=0, wScan=ord(ch), dwFlags=_KEYEVENTF_UNICODE, time=0, dwExtraInfo=None)),
            _INPUT(type=_INPUT_KEYBOARD, ki=_INPUT._UNION._KEYBD(wVk=0, wScan=ord(ch), dwFlags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
        ]
        _send_input(*inputs)
        time.sleep(0.004)
    return {"ok": True, "chars": len(words)}


def key(name: str) -> dict:
    """Press a named key (enter, esc, tab, backspace, arrows, F-keys, …)."""
    vk = _VK_MAP.get((name or "").lower())
    if vk is None:
        return {"ok": False, "error": f"unknown key {name!r}"}
    inputs = [
        _INPUT(type=_INPUT_KEYBOARD, ki=_INPUT._UNION._KEYBD(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None)),
        _INPUT(type=_INPUT_KEYBOARD, ki=_INPUT._UNION._KEYBD(wVk=vk, wScan=0, dwFlags=_KEYEVENTF_KEYUP, time=0, dwExtraInfo=None)),
    ]
    _send_input(*inputs)
    return {"ok": True}


def handle_event(event: dict) -> dict:
    """Dispatch one remote input event dict; never raises."""
    kind = str(event.get("type", "")).lower()
    try:
        if kind == "move":
            return move(float(event.get("x", 0.5)), float(event.get("y", 0.5)))
        if kind == "click":
            return click(float(event.get("x", 0.5)), float(event.get("y", 0.5)), str(event.get("button", "left")))
        if kind == "double":
            return double_click(float(event.get("x", 0.5)), float(event.get("y", 0.5)), str(event.get("button", "left")))
        if kind == "button":
            return button(str(event.get("button", "left")), bool(event.get("down", False)))
        if kind == "wheel":
            return wheel(int(event.get("dy", 0)))
        if kind == "text":
            return text(str(event.get("text", "")))
        if kind == "key":
            return key(str(event.get("key", "")))
        return {"ok": False, "error": f"unknown input type {kind!r}"}
    except Exception as exc:  # noqa: BLE001
        log.warning("[input] event failed: %s", exc)
        return {"ok": False, "error": str(exc)}
