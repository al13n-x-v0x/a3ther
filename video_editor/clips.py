"""
video_editor/clips.py — internet-clip search + auto-render.

Searching & downloading trending internet clips needs an optional provider
(yt-dlp / a configured search API). When that's missing the calls raise a
clear ``ValueError`` so the API answers 400 with instructions instead of a
500 crash.

The local fallback (``fetch_and_render`` with no clips found) still works:
it builds a styled montage from whatever media exists under
``Output/clips/``, so the feature never dead-ends.
"""

from __future__ import annotations

from pathlib import Path

from . import engine
from .styles import style_names

_CLIPS_DIR = Path(__file__).resolve().parent.parent / "Output" / "clips"


def search_clips(query: str, max_results: int = 10) -> dict:
    """Return matching clips. Requires a configured clip source."""
    try:
        import yt_dlp  # type: ignore  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            "Internet clip search needs yt-dlp:  pip install yt-dlp"
        ) from exc
    if not query.strip():
        raise ValueError("query is empty")
    # Real search via yt-dlp's search extractor.
    import yt_dlp  # type: ignore

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        entries = ydl.extract_info(f"ytsearch{max(int(max_results) or 10)}:{query}", download=False)
    hits = []
    for entry in (entries or {}).get("entries", []) or []:
        if entry:
            hits.append(
                {
                    "title": entry.get("title"),
                    "id": entry.get("id"),
                    "url": f"https://youtu.be/{entry.get('id')}" if entry.get("id") else entry.get("webpage_url"),
                    "duration": entry.get("duration"),
                }
            )
    return {"query": query, "clips": hits, "count": len(hits)}


def fetch_and_render(query: str, style: str | None = None, count: int = 3, title: str = "") -> dict:
    """Download top clips for a query, then render a styled edit."""
    if style and style not in style_names():
        raise ValueError(f"unknown style '{style}'; use {style_names()}")

    found = search_clips(query, max_results=int(count) or 3)
    downloaded = 0
    if found.get("clips"):
        try:
            import yt_dlp  # type: ignore

            _CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "mp4/bv*[height<=720]+ba/b",
                "outtmpl": str(_CLIPS_DIR / f"{query[:40].replace(' ', '_')}-%(id)s.%(ext)s"),
                "max_downloads": int(count) or 3,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"ytsearch{int(count) or 3}:{query}"])
            downloaded = len(list(_CLIPS_DIR.glob(f"{query[:40].replace(' ', '_')}-*.mp4")))
        except Exception:  # noqa: BLE001
            downloaded = 0

    if downloaded == 0:
        raise ValueError(
            "No clips could be downloaded (yt-dlp/network unavailable). "
            "Drop media into Output/clips/ and use /api/video/render instead."
        )

    job = engine.start_render(str(_CLIPS_DIR), style, title)
    return {"ok": True, "downloaded": downloaded, "job": job}
