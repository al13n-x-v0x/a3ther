"""
stream_stt.py — streaming speech-to-text for the voice pipeline.

Two modes, reusing the project's existing engines in ``core/stt.py``:

- ``vosk`` (default): true streaming — feed int16 chunks, get partial +
  final transcripts as they arrive.
- ``whisper``: faster-whisper utterance transcription with VAD gating —
  audio is buffered while speech is present, then transcribed when the
  utterance ends (silence detected), giving accurate one-shot results.

Accuracy / quality notes (the "make STT better" layer):

- Vosk input is buffered into ~150 ms blocks before being handed to Kaldi.
  Feeding 30 ms dribbles wastes both accuracy and CPU — Kaldi recognizes
  best with 100-200 ms buffers.
- A low noise gate drops near-silent frames, so keyboard clicks and room
  tone never reach the recogniser (they otherwise surface as garbage
  partials like "uh" / "a").
- Final transcripts are cleaned: leading filler words ("um", "uh",
  "like"), accidental duplicate words ("the the") and capitalization.
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

# Vosk quality knobs
_VOSK_FEED_MS = 150            # Kaldi works better with 100-200 ms buffers
_NOISE_GATE_RMS = 0.0012       # frames quieter than this never reach Vosk

# Filler words that often start a dictation but carry no meaning.
_LEADING_FILLERS = {"um", "uh", "hmm", "er", "erm", "mm", "like"}

# Words whose immediate repetition is almost always an ASR duplicate
# ("the the plan") rather than real emphasis ("very very good").
_COLLAPSE_DUPS = {
    "the", "a", "and", "to", "of", "in", "is", "it", "i", "you", "we",
    "they", "he", "she", "that", "this", "for", "on", "with", "at", "by",
    "or", "so", "but", "was", "were", "are", "my", "your",
}


def _rms_int16(frame: np.ndarray) -> float:
    """RMS of an int16 frame relative to full scale (0..1)."""
    x = np.asarray(frame, dtype=np.float32) / 32768.0
    return float(np.sqrt(float(np.mean(x * x)) + 1e-12))


def _clean_transcript(text: str) -> str:
    """Light cleanup so transcripts read naturally instead of verbatim-ASR.

    - strips leading filler words: "um, like, I think..." -> "I think..."
    - collapses duplicate function words: "the the plan" -> "the plan"
    - capitalizes the first letter (nicer for the HUD / chat display)
    """
    words = str(text or "").split()
    while words and words[0].strip(".,!?;:").lower() in _LEADING_FILLERS:
        words.pop(0)
    out: list[str] = []
    for word in words:
        if (
            out
            and word.lower() == out[-1].lower()
            and word.lower() in _COLLAPSE_DUPS
        ):
            continue
        out.append(word)
    cleaned = " ".join(out).strip()
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


class StreamingTranscriber:
    """Chunked transcription with a uniform feed()/reset() interface."""

    def __init__(self, engine: str = "vosk", sample_rate: int = 16_000, **kwargs):
        self.engine = engine
        self.sample_rate = sample_rate
        self._vosk = None
        self._whisper = None
        self._whisper_buf: list[np.ndarray] = []
        self._vosk_buf: list[np.ndarray] = []
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

    def _frame_ms(self, frame: np.ndarray) -> int:
        return max(1, int(round(len(frame) * 1000 / self.sample_rate)))

    def _feed_vosk(self, frame: np.ndarray) -> tuple[str, bool]:
        assert self._vosk is not None
        # Noise gate — never feed near-silent frames to Kaldi, otherwise
        # room tone / typing produce garbage partials ("uh", "a", "the").
        if _rms_int16(frame) < _NOISE_GATE_RMS:
            return "", False
        # Buffer into ~150 ms blocks: Kaldi's accuracy (and CPU use) is
        # much better with a proper buffer than with 30 ms dribbles.
        self._vosk_buf.append(frame)
        if len(self._vosk_buf) < max(1, round(_VOSK_FEED_MS / self._frame_ms(frame))):
            return "", False
        text, is_final = self._vosk.process_chunk(frames_to_bytes(self._vosk_buf))
        self._vosk_buf = []
        if is_final and text:
            text = _clean_transcript(text)
        return text, is_final

    def _feed_whisper(self, frame: np.ndarray) -> tuple[str, bool]:
        assert self._whisper is not None
        audio = np.asarray(frame, dtype=np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio ** 2) + 1e-12))
        step_ms = self._frame_ms(frame)

        if rms >= _VAD_THRESHOLD:
            self._silence_ms = 0
            self._whisper_buf.append(audio)
            self._utterance_ms += step_ms
            # Hard cap — transcribe what we have rather than buffer forever.
            if self._utterance_ms >= _UTTERANCE_MAX_MS:
                return self._flush_whisper(), True
            return "", False

        if self._whisper_buf:
            self._silence_ms += step_ms
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
        return _clean_transcript(self._whisper.transcribe(audio))

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self._whisper_buf = []
        self._vosk_buf = []
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
