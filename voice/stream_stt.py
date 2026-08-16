"""
stream_stt.py — streaming speech-to-text for the voice pipeline.

Two modes, reusing the project's existing engines in ``core/stt.py``:

- ``vosk`` (default): true streaming — feed int16 chunks, get partial +
  final transcripts as they arrive.
- ``whisper``: faster-whisper utterance transcription with VAD gating —
  audio is buffered while speech is present, then transcribed when the
  utterance ends (silence detected), giving accurate one-shot results.
"""
from __future__ import annotations

import logging

import numpy as np

from .audio_io import frames_to_bytes, frames_to_float32

LOGGER = logging.getLogger("a3ther.voice")

# Above this RMS (relative to full-scale int16) we consider speech present.
_VAD_THRESHOLD = 0.004
_VAD_SILENCE_MS = 700          # silence of this length ends an utterance
_UTTERANCE_MAX_MS = 15_000     # hard cap so a single utterance can't hang us


class StreamingTranscriber:
    """Chunked transcription with a uniform feed()/reset() interface."""

    def __init__(self, engine: str = "vosk", sample_rate: int = 16_000, **kwargs):
        self.engine = engine
        self.sample_rate = sample_rate
        self._vosk = None
        self._whisper = None
        self._whisper_buf: list[np.ndarray] = []
        self._silence_ms = 0
        self._utterance_ms = 0
        self._kwargs = kwargs
        self.reset()

    # ------------------------------------------------------------------ #
    def _ensure(self):
        if self.engine == "vosk":
            if self._vosk is None:
                from core.stt import VoskSTT

                self._vosk = VoskSTT(
                    model_path=self._kwargs.get("vosk_model_path"),
                    language=str(self._kwargs.get("language", "en-us")),
                )
        else:
            if self._whisper is None:
                from core.stt import WhisperSTT

                self._whisper = WhisperSTT(
                    model_name=str(self._kwargs.get("whisper_model", "base")),
                    language=self._kwargs.get("whisper_language"),
                )

    # ------------------------------------------------------------------ #
    def feed(self, frame: np.ndarray) -> tuple[str, bool]:
        """Feed one 16 kHz int16 frame. Returns (text, is_final)."""
        self._ensure()
        if self.engine == "vosk":
            return self._feed_vosk(frame)
        return self._feed_whisper(frame)

    def _feed_vosk(self, frame: np.ndarray) -> tuple[str, bool]:
        assert self._vosk is not None
        return self._vosk.process_chunk(frames_to_bytes([frame]))

    def _feed_whisper(self, frame: np.ndarray) -> tuple[str, bool]:
        assert self._whisper is not None
        audio = np.asarray(frame, dtype=np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio ** 2) + 1e-12))

        if rms >= _VAD_THRESHOLD:
            self._silence_ms = 0
            self._whisper_buf.append(audio)
            self._utterance_ms += 30
            # Hard cap — transcribe what we have rather than buffer forever.
            if self._utterance_ms >= _UTTERANCE_MAX_MS:
                return self._flush_whisper(), True
            return "", False

        if self._whisper_buf:
            self._silence_ms += 30
            if self._silence_ms >= _VAD_SILENCE_MS:
                return self._flush_whisper(), True
        return "", False

    def _flush_whisper(self) -> str:
        if not self._whisper_buf:
            return ""
        audio = frames_to_float32(self._whisper_buf)
        self._whisper_buf = []
        self._utterance_ms = 0
        self._silence_ms = 0
        return self._whisper.transcribe(audio)

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self._whisper_buf = []
        self._silence_ms = 0
        self._utterance_ms = 0
        if self._vosk is not None:
            try:
                self._vosk._rec.Reset()  # noqa: SLF001 — internal Vosk reset
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        self._vosk = None
        self._whisper = None
