"""
wake_word.py — local wake-word engines for "Hey Aether".

Two backends, both fully local and lightweight:

- ``porcupine``: Picovoice Porcupine (needs an AccessKey and either a
  custom trained ``.ppn`` for "hey aether" or a built-in keyword). Uses
  ~10% CPU of one core.
- ``vosk`` (default): runs a tiny Vosk recogniser continuously and fires
  when the transcript contains the configured wake phrase ("hey aether" /
  "hey ather"). Zero training, a few % CPU, works offline.

Both implement the same tiny interface: ``consume(frame) -> bool``.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from config import get_env

LOGGER = logging.getLogger("a3ther.voice")

DEFAULT_PHRASES = ("hey aether", "hey ather", "hi aether", "hello aether")


class WakeWordTriggered(RuntimeError):
    """Internal signal not used externally — kept for clarity."""


class BaseWakeWord:
    """Common contract: consume() -> True when the wake word fires."""

    name = "base"

    def consume(self, frame: np.ndarray) -> bool:  # pragma: no cover
        raise NotImplementedError

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


class PorcupineWakeWord(BaseWakeWord):
    """Picovoice Porcupine — low CPU, but needs an AccessKey + .ppn."""

    name = "porcupine"

    def __init__(
        self,
        access_key: str | None = None,
        keyword_paths: list[str] | None = None,
        model_path: str | None = None,
        sensitivities: float = 0.6,
    ):
        self.access_key = access_key or get_env("A3THER_PORCUPINE_KEY", "")
        self.keyword_paths = keyword_paths
        self.model_path = model_path
        self.sensitivities = sensitivities
        self._porcupine = None

    def _ensure(self):
        if self._porcupine is None:
            try:
                import pvporcupine
            except ImportError as exc:
                raise RuntimeError(
                    "pvporcupine is not installed (pip install pvporcupine). "
                    "Use the 'vosk' wake-word backend instead."
                ) from exc

            kwargs = {"access_key": self.access_key, "sensitivities": self.sensitivities}
            if self.keyword_paths:
                kwargs["keyword_paths"] = self.keyword_paths
            else:
                # Built-in keywords include 'jarvis' — a decent stand-in until a
                # custom 'hey aether' .ppn is trained via Picovoice Console.
                kwargs["keywords"] = ["jarvis"]
            if self.model_path:
                kwargs["model_path"] = self.model_path
            self._porcupine = pvporcupine.create(**kwargs)

    def consume(self, frame: np.ndarray) -> bool:
        self._ensure()
        # Porcupine expects int16 PCM at its native frame length (512).
        pcm = np.asarray(frame, dtype=np.int16)
        if len(pcm) < self._porcupine.frame_length:
            return False
        if len(pcm) > self._porcupine.frame_length:
            pcm = pcm[: self._porcupine.frame_length]
        return self._porcupine.process(pcm) >= 0

    def close(self) -> None:
        if self._porcupine is not None:
            try:
                self._porcupine.delete()
            except Exception:  # noqa: BLE001
                pass
            self._porcupine = None


class VoskWakeWord(BaseWakeWord):
    """Streaming Vosk recogniser that fires on the wake phrase.

    Lightweight enough to run continuously in the background; the wake
    phrase is detected from partial and final transcripts so the agent can
    respond with the lowest possible latency.
    """

    name = "vosk"

    def __init__(
        self,
        phrases: tuple[str, ...] = DEFAULT_PHRASES,
        language: str = "en-us",
        model_path: str | None = None,
        partial_trigger: bool = True,
        # Process every Nth block through Kaldi. Continuous recognition on
        # every 30ms block burns CPU for no extra accuracy — a 1-in-3 duty
        # cycle keeps wake-word latency sub-100ms while cutting the load
        # roughly 3x (the "laggy" / hot-laptop feeling).
        duty_cycle: int = 3,
    ):
        self.phrases = tuple(p.lower() for p in phrases)
        self.language = language
        self.model_path = model_path
        self.partial_trigger = partial_trigger
        self.duty_cycle = max(1, int(duty_cycle))
        self._tick = 0
        self._rec = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._rec is None:
            try:
                from vosk import KaldiRecognizer, Model
            except ImportError as exc:
                raise RuntimeError(
                    "vosk is not installed (pip install vosk). "
                    "Use the 'porcupine' wake-word backend instead."
                ) from exc
            if self.model_path:
                model = Model(self.model_path)
            else:
                model = Model(lang=self.language)
            self._rec = KaldiRecognizer(model, 16000)

    def consume(self, frame: np.ndarray) -> bool:
        with self._lock:
            self._ensure()
            self._tick += 1
            if self._tick % self.duty_cycle != 0:
                return False
            pcm = np.asarray(frame, dtype=np.int16).tobytes()
            if not pcm:
                return False
            if self._rec.AcceptWaveform(pcm):
                import json

                text = (json.loads(self._rec.Result()).get("text", "") or "").lower()
                if self._match(text):
                    self.reset()
                    return True
            elif self.partial_trigger:
                import json

                partial = (json.loads(self._rec.PartialResult()).get("partial", "") or "").lower()
                if self._match(partial):
                    self.reset()
                    return True
            return False

    def _match(self, text: str) -> bool:
        return any(phrase in text for phrase in self.phrases)

    def reset(self) -> None:
        with self._lock:
            if self._rec is not None:
                try:
                    self._rec.Reset()
                except Exception:  # noqa: BLE001
                    pass

    def close(self) -> None:
        self._rec = None


def create_wake_word(config: dict | None = None) -> BaseWakeWord:
    """Factory from config: {"wake_word_engine": "vosk" | "porcupine"}."""
    config = config or {}
    engine = str(config.get("wake_word_engine", "vosk")).lower()
    if engine == "porcupine":
        return PorcupineWakeWord(
            access_key=config.get("porcupine_access_key") or get_env("A3THER_PORCUPINE_KEY"),
            keyword_paths=config.get("porcupine_keyword_paths"),
            model_path=config.get("porcupine_model_path"),
            sensitivities=float(config.get("porcupine_sensitivity", 0.6)),
        )
    return VoskWakeWord(
        phrases=tuple(config.get("wake_phrases") or DEFAULT_PHRASES),
        language=str(config.get("wake_language", "en-us")),
        model_path=config.get("vosk_model_path"),
        duty_cycle=int(config.get("wake_duty_cycle", 3) or 3),
    )
