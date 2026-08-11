"""
audio_io.py — microphone input and speaker output with device resilience.

Uses ``sounddevice`` (already a project dependency). The input stream is
callback-driven: frames land in a thread-safe queue and the capture loop
consumes them. If the audio device disappears (USB mic unplugged, driver
reset) the stream fails with an OSError, which :class:`AudioIO` reports
and the :mod:`voice.pipeline` loop handles by re-initialising with
backoff and a device re-scan — so the agent recovers automatically
instead of crashing.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

import numpy as np

try:
    import sounddevice as sd
    _SD_OK = True
except ImportError:  # pragma: no cover
    sd = None  # type: ignore[assignment]
    _SD_OK = False

LOGGER = logging.getLogger("a3ther.voice")

SAMPLE_RATE = 16_000          # shared with Vosk / Whisper pipelines
BLOCK_SIZE = 480              # 30 ms frames @16 kHz
DTYPE = "int16"


class AudioDeviceError(RuntimeError):
    """Raised when the input device cannot be opened or was lost."""


def list_input_devices() -> list[dict]:
    """Return a dashboard-friendly list of available input devices."""
    if not _SD_OK:
        return []
    try:
        devices = sd.query_devices()
        out = []
        for index, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) > 0:
                out.append(
                    {
                        "index": index,
                        "name": dev.get("name", f"Device {index}"),
                        "channels": dev.get("max_input_channels", 0),
                        "default": index == sd.default.device[0],
                    }
                )
        return out
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("list_input_devices failed: %s", exc)
        return []


class AudioIO:
    """Callback-driven microphone input with automatic device recovery."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, block_size: int = BLOCK_SIZE):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=256)
        self._stream = None
        self._lock = threading.RLock()
        self._dead = threading.Event()
        self._device_index: int | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Open the input stream (or recover it if the device was lost)."""
        with self._lock:
            if self._stream is not None:
                return
            if not _SD_OK:
                raise AudioDeviceError(
                    "sounddevice is not installed. Run: pip install sounddevice"
                )
            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    channels=1,
                    dtype=DTYPE,
                    device=self._device_index,
                    callback=self._on_audio,
                )
                self._stream.start()
                self._dead.clear()
            except Exception as exc:  # noqa: BLE001
                self._stream = None
                raise AudioDeviceError(f"Cannot open input device: {exc}") from exc

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        # sounddevice gives shape (frames, channels) — flatten to mono.
        mono = np.asarray(indata).reshape(-1)
        try:
            self._queue.put_nowait(mono.copy())
        except queue.Full:
            # Drop the oldest block to keep latency bounded.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(mono.copy())
            except Exception:  # noqa: BLE001
                pass

    def read_block(self, timeout: float = 1.0) -> np.ndarray | None:
        """Return the next audio block or None on timeout."""
        try:
            block = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if self._stream is not None and not self._stream.active:
            # Stream died underneath us (device unplugged) — flag it.
            self._dead.set()
        return block

    def is_dead(self) -> bool:
        return self._dead.is_set()

    def stop(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------ #
    @staticmethod
    def play(samples: np.ndarray, sample_rate: int = 24_000) -> None:
        """Blocking playback of float32 audio (mono or stereo)."""
        if not _SD_OK:
            return
        sd.play(np.asarray(samples, dtype=np.float32), sample_rate)
        sd.wait()

    @staticmethod
    def stop_playback() -> None:
        if _SD_OK:
            sd.stop()


# ------------------------------------------------------------------------- #
# Convenience: frame conversion helpers shared by STT engines
# ------------------------------------------------------------------------- #
def frames_to_bytes(frames: list[np.ndarray]) -> bytes:
    """Concatenate int16 frames into the raw PCM bytes Vosk expects."""
    if not frames:
        return b""
    audio = np.concatenate(frames) if len(frames) > 1 else frames[0]
    return audio.astype(np.int16).tobytes()


def frames_to_float32(frames: list[np.ndarray]) -> np.ndarray:
    """Concatenate int16 frames into a float32 array (Whisper expects)."""
    if not frames:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(frames) if len(frames) > 1 else frames[0]
    return audio.astype(np.float32) / 32768.0
