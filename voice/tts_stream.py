"""
tts_stream.py — low-latency streaming speech output.

Strategy for sub-500 ms perceived latency:

1. Responses are split into sentences as soon as they are known.
2. A producer thread hands each sentence to the underlying TTS engine
   (Kokoro streams internally; EdgeTTS is called per sentence so the
   first sentence starts playing while later ones are still synthesising).
3. A consumer thread plays each audio chunk the moment it arrives.

``interrupt()`` cuts playback instantly (new command / user spoke again).
"""
from __future__ import annotations

import logging
import queue
import re
import threading

from core.tts import create_tts_player

LOGGER = logging.getLogger("a3ther.voice")

# Split on sentence boundaries — keep acronyms like "A.I." intact.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class StreamingSpeaker:
    """Queue-based streaming TTS with interrupt support."""

    def __init__(self, player=None, config: dict | None = None):
        self._player = player or create_tts_player(config or {})
        self._queue: "queue.Queue[str | None]" = queue.Queue(maxsize=16)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._speaking = False

    # ------------------------------------------------------------------ #
    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._consume_loop, name="voice-tts", daemon=True
        )
        self._thread.start()

    def say(self, text: str) -> None:
        """Queue a full response; the consumer speaks it sentence by sentence."""
        if not text or not text.strip():
            return
        self.start()
        for sentence in _split_sentences(text):
            if not sentence:
                continue
            try:
                self._queue.put_nowait(sentence)
            except queue.Full:
                break

    def say_now(self, text: str) -> None:
        """Interrupt current speech and speak ``text`` immediately."""
        self.interrupt()
        self.say(text)

    def interrupt(self) -> None:
        """Stop playback and drop queued sentences."""
        self._player.stop()
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self._speaking = False

    def stop(self) -> None:
        self.interrupt()

    # ------------------------------------------------------------------ #
    def _consume_loop(self) -> None:
        while True:
            sentence = self._queue.get()
            if sentence is None:
                break
            self._speaking = True
            try:
                self._player.speak(sentence)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("TTS sentence failed: %s", exc)
            finally:
                self._speaking = False

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)  # sentinel
        except queue.Full:
            pass
        self._player.stop()


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text or "")]
    return [p for p in parts if p]


def create_speaker(config: dict | None = None) -> StreamingSpeaker:
    """Factory: build a StreamingSpeaker from the same config the project uses."""
    return StreamingSpeaker(player=None, config=config or {})
