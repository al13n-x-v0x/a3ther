"""
video_editor/engine.py — the render pipeline (real, OpenCV-backed).

    start_render(source_dir, style, title)  -> job dict (runs in background)
    job_status()                            -> all jobs + their state
    list_videos()                           -> rendered videos (name/size/url)
    get_video_path(name)                    -> safe on-disk path for serving

Rendering: every image/video in ``source_dir`` is read, color-graded with
the requested style, and written to a montage MP4 (with a title card) in
``Output/videos/``. Requires OpenCV (``pip install opencv-python``) — when
missing, calls raise a clear ``ValueError`` so the API returns a helpful
400 instead of a 500 crash.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from .styles import apply_style, style_names

_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "Output" / "videos"
_JOBS_FILE = Path(__file__).resolve().parent.parent / "Output" / "videos" / "_jobs.json"

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
_FRAME_DURATION = 1.6  # seconds per still image in the montage
_TARGET = (1280, 720)
_FPS = 24

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _require_cv2() -> None:
    try:
        import cv2  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            "The video editor needs OpenCV. Install it with:  pip install opencv-python"
        ) from exc


def _load_jobs() -> None:
    global _jobs
    try:
        if _JOBS_FILE.exists():
            with open(_JOBS_FILE, "r", encoding="utf-8") as fh:
                _jobs = json.load(fh)
    except Exception:  # noqa: BLE001
        _jobs = {}


def _save_jobs() -> None:
    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(_JOBS_FILE, "w", encoding="utf-8") as fh:
            json.dump(_jobs, fh, indent=2)
    except Exception:  # noqa: BLE001
        pass


def _title_card(frame, title: str):
    """Overlay the title + A3THER branding on the first frame."""
    import cv2  # type: ignore

    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - 90), (w, h), (0, 0, 0), -1)
    frame = cv2.addWeighted(overlay, 0.65, frame, 0.35, 0)
    if title:
        cv2.putText(frame, title[:48], (28, h - 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 210, 255), 2, cv2.LINE_AA)
    cv2.putText(frame, "A.3.T.H.E.R.", (28, h - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (160, 220, 255), 1, cv2.LINE_AA)
    return frame


def _iter_sources(source_dir: Path):
    """Yield (path, is_video) for every usable file in the folder, sorted."""
    for path in sorted(source_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in _IMAGE_EXTS:
            yield path, False
        elif path.suffix.lower() in _VIDEO_EXTS:
            yield path, True


def _read_first_frames(path: Path, is_video: bool, count: int):
    """Read up to ``count`` BGR frames from one source file (video: sampled)."""
    import cv2  # type: ignore

    frames: list = []
    if is_video:
        cap = cv2.VideoCapture(str(path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        if total > 0:
            step = max(1, total // count)
            idx = 0
            while idx < total and len(frames) < count:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if ok:
                    frames.append(frame)
                idx += step
        cap.release()
    else:
        img = cv2.imread(str(path))
        if img is not None:
            frames.append(img)
    return frames


def _render_job(job_id: str, source_dir: str, style: str | None, title: str) -> None:
    import cv2  # type: ignore

    with _jobs_lock:
        _jobs[job_id]["state"] = "rendering"
        _save_jobs()
    try:
        src = Path(source_dir)
        if not src.is_dir():
            raise ValueError(f"source directory not found: {source_dir}")
        sources = list(_iter_sources(src))
        if not sources:
            raise ValueError(f"no images/videos found in {source_dir}")

        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_name = f"{job_id[:8]}-{uuid.uuid4().hex[:6]}.mp4"
        out_path = _OUTPUT_DIR / out_name
        writer = None
        frame_count = 0

        for path, is_video in sources:
            frames = _read_first_frames(path, is_video, count=6)
            for raw in frames:
                frame = cv2.resize(raw, _TARGET)
                frame = apply_style(frame, style)
                if frame_count == 0:
                    frame = _title_card(frame, title)
                if writer is None:
                    writer = cv2.VideoWriter(
                        str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), _FPS, _TARGET
                    )
                writer.write(frame)
                frame_count += 1
            with _jobs_lock:
                _jobs[job_id]["progress"] = f"{frame_count} frames"
                _save_jobs()

        if writer is None:
            raise ValueError("nothing was rendered (no readable frames)")
        writer.release()
        size_mb = round(out_path.stat().st_size / (1024 * 1024), 1)

        with _jobs_lock:
            _jobs[job_id].update(
                state="done",
                output=out_name,
                size_mb=size_mb,
                frames=frame_count,
                progress="complete",
            )
            _save_jobs()
    except Exception as exc:  # noqa: BLE001
        with _jobs_lock:
            _jobs[job_id].update(state="failed", error=str(exc))
            _save_jobs()


def start_render(source_dir: str, style: str | None = None, title: str = "") -> dict:
    """Start a background render; returns the job dict immediately."""
    _require_cv2()
    if style and style not in style_names():
        raise ValueError(f"unknown style '{style}'; use {style_names()}")
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "source_dir": source_dir,
        "style": style,
        "title": title,
        "state": "queued",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "progress": "queued",
    }
    with _jobs_lock:
        _jobs[job_id] = job
        _save_jobs()
    threading.Thread(
        target=_render_job, args=(job_id, source_dir, style, title), daemon=True, name="a3ther-video"
    ).start()
    return job


def job_status() -> dict:
    _load_jobs()
    with _jobs_lock:
        return dict(_jobs)


def list_videos() -> list[dict]:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for path in sorted(_OUTPUT_DIR.glob("*.mp4")):
        out.append(
            {
                "name": path.name,
                "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
                "url": f"/api/video/file/{path.name}",
            }
        )
    return out


def get_video_path(name: str) -> Path | None:
    """Safe lookup — rejects traversal (only the bare filename is used)."""
    safe = Path(name).name
    path = _OUTPUT_DIR / safe
    return path if path.is_file() else None
