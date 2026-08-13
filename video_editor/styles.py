"""
video_editor/styles.py — the editor's style presets.

Each style is a small OpenCV color grade applied to every frame:
    tiktok_intense  high contrast + saturation, slight cool cast
    anime           boosted saturation + mild edge emphasis
    movie_trailer   teal-orange blockbuster grade
    aesthetic       soft, lifted, low-contrast pastel look

``apply_style`` degrades to the raw frame when cv2 is missing (callers get
a clear error from ``engine`` instead — this is belt and braces).
"""

from __future__ import annotations

_STYLES = ("tiktok_intense", "anime", "movie_trailer", "aesthetic")


def style_names() -> tuple[str, ...]:
    return _STYLES


def apply_style(frame, style: str | None):
    """Return the styled BGR frame. Falls back to the raw frame on error."""
    if not style or style not in _STYLES:
        return frame
    try:
        import cv2  # type: ignore

        frame = cv2.convertScaleAbs(frame, alpha=1.06, beta=4)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype("float32")
        h, s, v = cv2.split(hsv)

        if style == "tiktok_intense":
            s = s * 1.35
            v = v * 1.12
        elif style == "anime":
            s = s * 1.45
            v = v * 1.05
        elif style == "movie_trailer":
            # teal-orange: push shadows toward teal, highlights toward orange.
            v = v * 1.1
            h = (h + 8.0) % 180.0
        elif style == "aesthetic":
            s = s * 0.8
            v = v * 1.06

        hsv = cv2.merge([h, s, v])
        hsv = cv2.convertScaleAbs(hsv)
        return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    except Exception:  # noqa: BLE001
        return frame
