"""
video_editor/clips.py — pull the best clips from the internet.

Implements the "TikTok genre" fetch: give A3THER a vibe ("anime edit",
"sigma edit", "movie edits") and it searches the web for short-form videos
(YouTube Shorts / vertical edits), downloads the best candidates, and
stages them in a folder ready for the edit engine to render with the
TikTok Intense / Anime / Movie Trailer / Aesthetic presets.

How it works
------------
1. ``search_clips(query)`` — yt-dlp search for short (<90 s) videos,
   ranked by view count, filtered to vertical (9:16-ish) where detectable.
2. ``fetch_clips(query, count)`` — downloads the top ``count`` clips as
   mp4 into a staging folder under the videos dir, returning the folder +
   per-clip info for the API / HUD.
3. The staged folder is passed straight to ``start_render`` (engine.py),
   so internet clips get the exact same grade/cuts/music as local ones.

Safety: only yt-dlp (no shell), per-video timeout, hard cap on clip count
and bytes, and every failure degrades to an honest message.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from .engine import get_videos_dir

LOGGER = logging.getLogger("a3ther.video.clips")

_MAX_CLIPS = 12
_MAX_BYTES = 80 * 1024 * 1024   # ~80 MB per clip guard
# Edits in the TikTok genre are typically 2-4 min; the engine cuts every
# clip into short styled shots anyway, so keep anything up to ~5 min.
_MAX_DURATION = 300             # seconds
_SEARCH_RESULTS = 10

# Clean the search query into a safe folder name.
_SAFE_RE = re.compile(r"[^A-Za-z0-9 _\-]+")


def _fmt(duration: float | None) -> str:
    if duration is None:
        return "?"
    return f"{int(duration // 60)}:{int(duration % 60):02d}"


def _is_short(entry: dict) -> bool:
    """True when the entry is a clip we can cut into an edit."""
    duration = entry.get("duration") or 0
    if duration and duration > _MAX_DURATION:
        return False
    # Live streams / playlists are not clips.
    if entry.get("live_status") in ("is_live", "is_upcoming"):
        return False
    if entry.get("_type") not in (None, "url"):
        return False
    return True


def search_clips(query: str, max_results: int = _SEARCH_RESULTS) -> dict:
    """Search the internet for the best short clips for ``query``.

    Returns ``{"ok": True, "clips": [{id, title, duration, views, url}]}``.
    Never raises — failures come back as ``{"ok": False, "error": …}``.
    """
    try:
        import yt_dlp  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"yt-dlp not installed: {exc}"}

    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "search needs a query (e.g. 'anime edit', 'sigma edit')"}

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": True,
        "playlist_items": f"1-{max_results}",
        "socket_timeout": 20,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Clip search failed for %r: %s", query, exc)
        return {"ok": False, "error": f"clip search failed: {type(exc).__name__}: {exc}"}

    clips = []
    for entry in (info.get("entries") or [])[:max_results]:
        if not entry or not _is_short(entry):
            continue
        title = str(entry.get("title") or "untitled")[:120]
        if "shorts" in str(entry.get("url", "")) or "shorts" in str(entry.get("webpage_url", "")):
            pass  # already short-form
        clips.append({
            "id": entry.get("id"),
            "title": title,
            "duration": entry.get("duration"),
            "duration_str": _fmt(entry.get("duration")),
            "views": entry.get("view_count") or 0,
            "url": entry.get("webpage_url") or entry.get("url") or "",
        })
    return {"ok": True, "query": query, "clips": clips, "count": len(clips)}


_FFMPEG_SHIM: Path | None = None


def _ffmpeg_location() -> str | None:
    """Give yt-dlp a properly-named ffmpeg so it can merge formats.

    imageio-ffmpeg ships a static ffmpeg whose name is version-suffixed
    (``ffmpeg-win-x86_64-v7.1.exe``), and it is NOT on PATH — yt-dlp only
    recognises ``ffmpeg.exe``/``ffmpeg``, so merges abort. We create a shim
    directory with a ``ffmpeg.exe`` copy and point yt-dlp at it.
    """
    global _FFMPEG_SHIM
    if _FFMPEG_SHIM is not None:
        return str(_FFMPEG_SHIM)
    try:
        import imageio_ffmpeg  # noqa: PLC0415

        exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
        shim = get_videos_dir() / ".ffmpeg_shim"
        shim.mkdir(parents=True, exist_ok=True)
        target = shim / "ffmpeg.exe"
        if not target.exists():
            shutil.copyfile(exe, target)
        _FFMPEG_SHIM = shim
        return str(shim)
    except Exception:  # noqa: BLE001
        return None


def fetch_clips(query: str, count: int = 6) -> dict:
    """Download the best ``count`` clips for ``query`` into a staging folder.

    Returns ``{"ok": True, "folder": str, "files": [names], "clips": [...]}``
    ready for ``start_render(folder, style, title)``. Never raises.
    """
    try:
        import yt_dlp  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"yt-dlp not installed: {exc}"}

    count = max(1, min(int(count or 6), _MAX_CLIPS))
    search = search_clips(query, max_results=_SEARCH_RESULTS)
    if not search.get("ok"):
        return search
    clips = search.get("clips", [])
    if not clips:
        return {"ok": False, "error": f"no short clips found for '{query}' — try a different vibe"}

    # Sort by views (best clips first), take the top N.
    clips.sort(key=lambda c: c.get("views") or 0, reverse=True)
    clips = clips[:count]

    root = get_videos_dir() / "internet_clips"
    root.mkdir(parents=True, exist_ok=True)
    folder = root / f"{_SAFE_RE.sub('_', query).strip('_') or 'clips'}_{uuid.uuid4().hex[:6]}"
    folder.mkdir(parents=True, exist_ok=True)

    downloaded: list[dict] = []
    failed: list[str] = []
    for clip in clips:
        dest = folder / f"clip_{len(downloaded) + 1:02d}.mp4"
        opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
            "outtmpl": str(dest).replace(".mp4", ".%(ext)s"),
            "merge_output_format": "mp4",
            "ffmpeg_location": _ffmpeg_location(),
            "noplaylist": True,
            "socket_timeout": 25,
            "retries": 1,
            "max_filesize": _MAX_BYTES,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([clip["url"]])
            final = dest
            if not final.exists():
                # yt-dlp may have picked a different extension.
                cands = list(folder.glob(f"clip_{len(downloaded) + 1:02d}.*"))
                if cands:
                    final = cands[0]
            if final.exists():
                downloaded.append({
                    "file": final.name,
                    "title": clip["title"],
                    "duration": clip.get("duration"),
                    "views": clip.get("views") or 0,
                })
            else:
                failed.append(clip["title"])
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Clip download failed (%s): %s", clip["title"], exc)
            failed.append(clip["title"])
        if len(downloaded) >= count:
            break

    if not downloaded:
        return {
            "ok": False,
            "error": f"couldn't download any clips for '{query}' — check your internet",
            "failed": failed[:5],
        }
    return {
        "ok": True,
        "query": query,
        "folder": str(folder),
        "count": len(downloaded),
        "files": [d["file"] for d in downloaded],
        "clips": downloaded,
        "failed": failed[:5],
    }


# --------------------------------------------------------------------------- #
# Convenience: fetch + render in one call (used by the API "render-clips")
# --------------------------------------------------------------------------- #
def fetch_and_render(query: str, style: str | None = None, count: int = 6, title: str = "") -> dict:
    """Fetch internet clips and start a background TikTok-style render.

    Returns a job dict (same shape as ``start_render``) or an error dict.
    """
    from .engine import _MIN_CLIPS, start_render

    fetched = fetch_clips(query, count)
    if not fetched.get("ok"):
        return fetched
    if fetched["count"] < _MIN_CLIPS:
        return {
            "ok": False,
            "error": f"only {fetched['count']} clip{'s' if fetched['count'] != 1 else ''} downloaded "
            f"for '{query}' (need at least {_MIN_CLIPS}) — try again or another vibe",
        }
    try:
        job = start_render(fetched["folder"], style, title or f"{query} edit")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result = job.to_dict()
    result["source"] = "internet"
    result["query"] = query
    result["clips_fetched"] = fetched["count"]
    return {"ok": True, "job": result}
