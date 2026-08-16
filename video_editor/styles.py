"""
video_editor/styles.py — professional edit style presets.

Each preset describes how every shot is treated: frame size (9:16 for
TikTok/Reels, 16:9 for trailers), clip length (fast cuts = intense),
speed ramp, colour grade (saturation/contrast/brightness/hue), white
flash intensity, Ken Burns zoom, and vignette.

The names are used verbatim in the API + HUD:
``POST /api/video/render {"style": "tiktok_intense", …}``
"""
from __future__ import annotations

STYLES: dict[str, dict] = {
    "tiktok_intense": {
        "label": "TikTok Intense",
        "width": 1080,
        "height": 1920,
        "clip_duration": 1.4,   # fast cuts — the "intense edit" feel
        "speed": 1.0,
        "saturation": 1.55,
        "contrast": 1.18,
        "brightness": 0.02,
        "hue": 0.0,
        "flash": 0.10,          # white flash-in on every cut
        "zoom": 0.012,          # Ken Burns zoom speed for stills
        "vignette": False,
        "fps": 30,
    },
    "anime": {
        "label": "Anime",
        "width": 1080,
        "height": 1920,
        "clip_duration": 1.8,
        "speed": 0.9,           # slightly slowed — dramatic beats
        "saturation": 1.65,     # punchy, vivid anime colour
        "contrast": 1.12,
        "brightness": 0.0,
        "hue": -0.03,           # cool/cyan bias
        "flash": 0.06,
        "zoom": 0.008,
        "vignette": False,
        "fps": 30,
    },
    "movie_trailer": {
        "label": "Movie Trailer",
        "width": 1920,
        "height": 1080,
        "clip_duration": 2.4,
        "speed": 0.8,           # slow, cinematic
        "saturation": 0.85,     # desaturated + contrast = cinema
        "contrast": 1.25,
        "brightness": -0.02,
        "hue": 0.02,            # warm bias
        "flash": 0.0,
        "zoom": 0.006,
        "vignette": True,
        "fps": 30,
    },
    "aesthetic": {
        "label": "Aesthetic",
        "width": 1080,
        "height": 1920,
        "clip_duration": 2.0,
        "speed": 1.0,
        "saturation": 1.2,
        "contrast": 0.95,
        "brightness": 0.03,
        "hue": 0.0,
        "flash": 0.04,
        "zoom": 0.004,
        "vignette": True,
        "fps": 30,
    },
}

DEFAULT_STYLE = "tiktok_intense"


def get_style(name: str | None) -> dict:
    """Return a copy of the named preset (defaults to TikTok Intense)."""
    key = (name or "").strip().lower()
    return dict(STYLES.get(key, STYLES[DEFAULT_STYLE]))


def style_names() -> list[str]:
    return list(STYLES.keys())
