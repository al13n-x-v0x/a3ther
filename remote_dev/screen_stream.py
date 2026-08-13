"""
remote_dev/screen_stream.py — continuous screen capture for streaming.

A background thread grabs the primary monitor with mss at a target FPS,
downscales + JPEG-encodes each frame, and publishes it to a thread-safe
slot. Stream handlers (the /remote/stream MJPEG endpoint) read the latest
frame — no per-client capture cost.

The slot also carries the REAL monitor size (width/height) so remote input
can scale normalized coordinates correctly even though streamed frames are
downscaled.

Graceful degradation: if mss is missing, the stream endpoint reports an
honest error instead of pretending to stream.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("a3ther.remote.stream")

DEFAULT_FPS = 12
DEFAULT_MAX_WIDTH = 960  # px — keeps the stream light for phone/Wi-Fi


class FrameSlot:
    """Latest JPEG frame + real monitor size, protected by a lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.jpeg: bytes | None = None
        self.monitor: tuple[int, int] = (0, 0)
        self.generation = 0
        self.error: str | None = None

    def publish(self, jpeg: bytes, monitor: tuple[int, int]) -> None:
        with self._lock:
            self.jpeg = jpeg
            self.monitor = monitor
            self.generation += 1
            self.error = None

    def fail(self, message: str) -> None:
        with self._lock:
            self.error = message

    def latest(self) -> tuple[bytes | None, tuple[int, int], int, str | None]:
        with self._lock:
            return self.jpeg, self.monitor, self.generation, self.error


def _capture_once(max_width: int) -> tuple[bytes | None, tuple[int, int]]:
    """Grab + downscale + JPEG-encode one frame. Returns (jpeg, real size)."""
    import io

    import mss  # type: ignore
    from PIL import Image  # type: ignore

    with mss.mss() as sct:
        mon = sct.monitors[1]  # primary monitor
        shot = sct.grab(mon)
    width, height = shot.size
    scale = 1.0
    if max_width and width > max_width:
        scale = max_width / width
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    if scale < 1.0:
        img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=58, optimize=True)
    return buf.getvalue(), (width, height)


class ScreenStreamer:
    """Background capture thread publishing to a FrameSlot."""

    def __init__(self, fps: int = DEFAULT_FPS, max_width: int = DEFAULT_MAX_WIDTH) -> None:
        self.fps = max(1, min(fps, 30))
        self.max_width = max_width
        self.slot = FrameSlot()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> "ScreenStreamer":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="a3ther-screen-capture", daemon=True)
        self._thread.start()
        log.info("[stream] capture thread started (%s fps, max %spx)", self.fps, self.max_width)
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        interval = 1.0 / self.fps
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                jpeg, monitor = _capture_once(self.max_width)
                if jpeg:
                    self.slot.publish(jpeg, monitor)
            except Exception as exc:  # noqa: BLE001
                self.slot.fail(f"screen capture unavailable: {exc}")
                break  # don't spin on a broken capture (e.g. mss missing)
            elapsed = time.monotonic() - started
            if elapsed < interval:
                time.sleep(interval - elapsed)


_streamer: ScreenStreamer | None = None
_streamer_lock = threading.Lock()


def get_streamer(fps: int | None = None, max_width: int | None = None) -> ScreenStreamer:
    """Singleton streamer — one capture thread shared by all clients."""
    global _streamer
    with _streamer_lock:
        if _streamer is None or not _streamer._thread or not _streamer._thread.is_alive():
            _streamer = ScreenStreamer(fps=fps or DEFAULT_FPS, max_width=max_width or DEFAULT_MAX_WIDTH)
            _streamer.start()
        elif fps or max_width:
            # Keep the running instance — settings apply on next start.
            pass
        return _streamer


def stop_streamer() -> None:
    global _streamer
    with _streamer_lock:
        if _streamer:
            _streamer.stop()
            _streamer = None
