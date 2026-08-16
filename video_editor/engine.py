"""
video_editor/engine.py — the professional AI editing engine.

Turns a folder of clips/images into a stylised, cut-to-the-beat edit using
a pure-ffmpeg pipeline (the binary ships with the ``imageio-ffmpeg`` pip
package, so there is no system install):

1. Every source clip is **normalised** to the style's frame size @ 30fps
   with the preset grade: colour (saturation/contrast/brightness/hue),
   speed ramp, white flash-in on the cut, and Ken Burns zoom for stills.
2. Normalised shots are **hard-cut concatenated** (the authentic
   TikTok/edits style — no soft transitions).
3. A final pass burns in the **title text** (anime/trailer styling) and,
   if the source folder contains a music file, mixes it in as the
   background track.

Renders run on a background thread; the API polls ``job_status()`` for
progress. Every failure degrades to a clear message — never a hang.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import uuid
import zlib
from pathlib import Path

from config.paths import data_path
from .styles import get_style, style_names

LOGGER = logging.getLogger("a3ther.video")

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

_MAX_CLIPS = 40          # guard against absurd folders
_MIN_CLIPS = 2
_CLIP_MIN_DUR = 0.8
_MAX_JOBS = 1            # one render at a time (ffmpeg is heavy)


# A minimal valid 1x1 RGB PNG — used by the write-probe below so the only
# way ffmpeg fails to read it is a genuinely broken/virtualised path.
def _mini_png() -> bytes:
    def _chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x80\x00\x00")  # filter 0 + one RGB pixel
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


_MINI_PNG = _mini_png()


# --------------------------------------------------------------------------- #
# ffmpeg discovery
# --------------------------------------------------------------------------- #
def get_ffmpeg() -> str:
    """Path to a working ffmpeg binary (imageio-ffmpeg ships one)."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "ffmpeg unavailable — run: pip install imageio-ffmpeg"
        ) from exc


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _probe_duration(ffmpeg: str, path: str) -> float | None:
    """Best-effort duration (seconds) parsed from ``ffmpeg -i`` stderr."""
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", path],
            capture_output=True, text=True, timeout=20,
        )
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", proc.stderr)
        if m:
            h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mn * 60 + s
    except Exception:  # noqa: BLE001
        pass
    return None


def _run(ffmpeg: str, args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "ffmpeg failed").strip()[-500:])
    return proc


def _font_path() -> str:
    """A usable Windows font for drawtext ("" when none found)."""
    for candidate in (
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\impact.ttf",
    ):
        if Path(candidate).exists():
            # ffmpeg filter escaping for Windows paths.
            return candidate.replace("\\", "/").replace(":", "\\:")
    return ""


def _media_in(folder: Path) -> list[tuple[Path, bool]]:
    """[(path, is_image)] sorted by name; videos first so stills bridge cuts."""
    items = []
    for f in sorted(folder.iterdir()):
        ext = f.suffix.lower()
        if ext in VIDEO_EXTS:
            items.append((f, False))
    for f in sorted(folder.iterdir()):
        ext = f.suffix.lower()
        if ext in IMAGE_EXTS:
            items.append((f, True))
    return items[: _MAX_CLIPS]


# --------------------------------------------------------------------------- #
# Per-shot normalisation
# --------------------------------------------------------------------------- #
def _normalize_clip(ffmpeg: str, src: Path, out: Path, style: dict, is_image: bool) -> None:
    """Render one shot to the style's grade at a hard-cut duration."""
    w, h = style["width"], style["height"]
    fps = style["fps"]
    dur = max(_CLIP_MIN_DUR, float(style["clip_duration"]))
    speed = max(0.4, float(style["speed"]))

    grade = (
        f"eq=saturation={style['saturation']}:contrast={style['contrast']}:"
        f"brightness={style['brightness']}"
    )
    if style.get("hue"):
        grade += f",hue=h={style['hue']}"

    flash = float(style["flash"])
    fx = f"fade=t=in:st=0:d={max(0.05, flash)}:color=white" if flash > 0 else "null"

    if is_image:
        # Ken Burns zoom on a still: loop the image, scale to cover, zoom in.
        zoom = float(style["zoom"])
        zexpr = f"min(1.0+{zoom}*on,1.3)"
        vf = (
            f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
            f"crop={w * 2}:{h * 2},"
            f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={w}x{h}:fps={fps},"
            f"{grade},{fx}"
        )
        args = ["-loop", "1", "-t", f"{dur}", "-i", str(src), "-vf", vf,
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", str(fps), str(out)]
    else:
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"fps={fps},setpts={speed}*PTS,{grade},{fx}"
        )
        args = ["-i", str(src), "-vf", vf, "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", str(fps), "-t", f"{dur}", str(out)]
    _run(ffmpeg, args)


# --------------------------------------------------------------------------- #
# Render job
# --------------------------------------------------------------------------- #
class RenderJob:
    def __init__(self, job_id: str, source_dir: str, style_name: str, title: str):
        self.id = job_id
        self.source_dir = source_dir
        self.style_name = style_name
        self.title = (title or "").strip()[:60]
        self.status = "queued"          # queued | rendering | done | error
        self.progress = 0.0             # 0..1
        self.message = "Queued…"
        self.output_name = ""
        self.output_path = ""
        self.error = ""
        self.created = time.time()
        self.finished: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 2),
            "message": self.message,
            "output_name": self.output_name,
            "output_path": self.output_path,
            "error": self.error,
            "style": self.style_name,
            "title": self.title,
            "created": self.created,
            "finished": self.finished,
        }


_JOBS: dict[str, RenderJob] = {}
_JOB_LOCK = threading.Lock()
_RENDER_SEM = threading.Semaphore(_MAX_JOBS)

_VIDEOS_DIR: Path | None = None


def get_videos_dir() -> Path:
    """Render output root, probed so native ffmpeg can actually write there.

    The Microsoft-Store Python virtualises ``%LOCALAPPDATA%`` writes: Python
    "sees" a directory it created while native processes (ffmpeg, the exe)
    do not. We probe for the mismatch by having ffmpeg READ a file Python
    just wrote into the data dir (virtualised dirs fail that read); when it
    fails we fall back to ``~/Videos/A3THER`` which is real for both. The
    packaged exe (native) always passes the first probe, so behaviour is
    unchanged for end users.
    """
    global _VIDEOS_DIR
    if _VIDEOS_DIR is not None:
        return _VIDEOS_DIR
    candidates = [data_path("videos"), Path.home() / "Videos" / "A3THER"]
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".probe.png"
            probe.write_bytes(_MINI_PNG)  # valid 1-frame PNG written by Python
            # ffmpeg reads it back — fails when the dir is virtualised.
            _run(get_ffmpeg(), ["-v", "error", "-i", str(probe), "-f", "null", "-"])
            probe.unlink(missing_ok=True)
            _VIDEOS_DIR = base
            return base
        except Exception:  # noqa: BLE001 — try the next candidate
            continue
    _VIDEOS_DIR = Path.cwd()
    return _VIDEOS_DIR


def _safe_output_name(base: str, style_name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9 _\-]+", "", base).strip().replace(" ", "_") or "edit"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{base[:40]}_{style_name}_{stamp}.mp4"


def start_render(source_dir: str, style_name: str | None, title: str = "") -> RenderJob:
    """Validate inputs and start a background render. Never blocks."""
    folder = Path(source_dir or "").expanduser()
    if not folder.is_dir():
        raise ValueError(f"source folder not found: {source_dir}")
    media = _media_in(folder)
    if len(media) < _MIN_CLIPS:
        raise ValueError(
            f"need at least {_MIN_CLIPS} clips/images in the folder (found {len(media)})"
        )
    style = get_style(style_name)
    style_name = style_name or "tiktok_intense"
    job = RenderJob(uuid.uuid4().hex[:10], str(folder), style_name, title)
    with _JOB_LOCK:
        if sum(1 for j in _JOBS.values() if j.status in ("queued", "rendering")):
            raise ValueError("a render is already running — wait for it to finish")
        _JOBS[job.id] = job
    threading.Thread(target=_worker, args=(job,), daemon=True, name="video-render").start()
    return job


def _worker(job: RenderJob) -> None:
    with _RENDER_SEM:
        try:
            _render_job(job)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Render %s failed", job.id)
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()


def _render_job(job: RenderJob) -> None:
    ffmpeg = get_ffmpeg()
    get_videos_dir().mkdir(parents=True, exist_ok=True)
    work = get_videos_dir() / f"_work_{job.id}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    style = get_style(job.style_name)
    media = _media_in(Path(job.source_dir))
    job.status = "rendering"
    job.message = f"Normalising {len(media)} shots…"

    # 1) normalise every shot
    clips: list[Path] = []
    for i, (src, is_image) in enumerate(media):
        job.progress = i / len(media)
        job.message = f"Shot {i + 1}/{len(media)}: {src.name[:40]}"
        out = work / f"clip_{i:03d}.mp4"
        _normalize_clip(ffmpeg, src, out, style, is_image)
        clips.append(out)

    # 2) hard-cut concat
    job.message = "Stitching cuts…"
    list_file = work / "concat.txt"
    list_file.write_text(
        "".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8"
    )
    joined = work / "joined.mp4"
    _run(ffmpeg, ["-f", "concat", "-safe", "0", "-i", str(list_file),
                  "-c", "copy", str(joined)])

    # 3) final pass — title burn-in + optional music bed
    job.message = "Burning title + finishing…"
    out_name = _safe_output_name(job.title or "A3THER_EDIT", job.style_name)
    out_path = get_videos_dir() / out_name

    audio_inputs: list[str] = []
    audio_map = ""
    music = None
    for f in sorted(Path(job.source_dir).iterdir()):
        if f.suffix.lower() in AUDIO_EXTS:
            music = f
            break
    if music is not None:
        audio_inputs = ["-stream_loop", "-1", "-i", str(music)]
        audio_map = "-map", "0:v:0", "-map", "1:a:0"

    # drawtext paths with a colon (C:\Windows\Fonts…) break ffmpeg's filter
    # parser, so copy the font into the work dir — a clean, colon-free path.
    font = _font_path()
    font_arg = None
    if font and job.title:
        font_copy = work / "font.ttf"
        try:
            shutil.copyfile(font, font_copy)
            font_arg = font_copy.as_posix()
        except Exception:  # noqa: BLE001
            font_arg = None
    vf_parts: list[str] = []
    if font_arg:
        safe = job.title.replace("\\", "").replace("'", "").replace(":", "")
        vf_parts.append(
            f"drawtext=fontfile={font_arg}:text='{safe}':"
            f"fontsize={int(style['height'] * 0.055)}:fontcolor=white:"
            f"borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=h*0.08:enable='gte(t,0.4)'"
        )
    if style.get("vignette"):
        vf_parts.append("vignette=PI/5")

    final_vf = ",".join(vf_parts) if vf_parts else "null"
    args = ["-i", str(joined), *audio_inputs, "-vf", final_vf]
    if audio_map:
        args += list(audio_map)
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "19",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if music is not None:
        args += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    args += [str(out_path)]
    _run(ffmpeg, args)

    shutil.rmtree(work, ignore_errors=True)
    job.status = "done"
    job.progress = 1.0
    job.output_name = out_name
    job.output_path = str(out_path)
    job.message = f"Done — {out_name}"


# --------------------------------------------------------------------------- #
# Introspection
# --------------------------------------------------------------------------- #
def job_status() -> list[dict]:
    with _JOB_LOCK:
        jobs = sorted(_JOBS.values(), key=lambda j: j.created, reverse=True)
        return [j.to_dict() for j in jobs[:8]]


def list_videos() -> list[dict]:
    folder = get_videos_dir()
    if not folder.is_dir():
        return []
    out = []
    for f in sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.name.startswith("_work_"):
            continue
        out.append({
            "name": f.name,
            "size_mb": round(f.stat().st_size / 1_048_576, 2),
            "modified": f.stat().st_mtime,
            "url": f"/api/video/file/{f.name}",
        })
    return out


def get_video_path(name: str) -> Path | None:
    """Resolve a rendered filename inside the videos dir (path traversal safe)."""
    folder = get_videos_dir()
    target = (folder / Path(name).name).resolve()
    if target.parent != folder.resolve() or not target.is_file():
        return None
    return target
