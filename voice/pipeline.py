"""
pipeline.py — the event-driven native voice interface.

State machine::

    idle ──start──▶ wake_listening ──wake word──▶ listening
    listening ──final transcript──▶ transcribing ──▶ thinking
    thinking ──response ready──▶ speaking ──done──▶ wake_listening

Everything runs on background threads (capture, STT, TTS, LLM) so the UI
is never blocked. State transitions are pushed through ``on_state`` so the
dashboard can render a Listen/Mute UI live.

Robustness: if the microphone is unplugged mid-session, AudioIO reports
the device loss and the capture loop re-initialises with backoff,
emitting an "audio_error" state so the UI can warn the user.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .audio_io import AudioIO, AudioDeviceError
from .stream_stt import StreamingTranscriber
from .tts_stream import StreamingSpeaker
from .wake_word import BaseWakeWord, create_wake_word

LOGGER = logging.getLogger("a3ther.voice")

_VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"


def _ensure_vosk_model() -> str | None:
    """Return a usable Vosk model path (downloading the small model once).

    Looks in config first, then the A3THER data folder, then downloads.
    Never raises — the voice loop simply stays off if the model can't be
    obtained (honest degradation).
    """
    try:
        import io
        import urllib.request
        import zipfile
        from pathlib import Path

        from config import get_config
        from config.paths import get_data_dir

        cfg = get_config()
        configured = cfg.get("vosk_model_path")
        if configured and Path(configured).exists():
            return str(configured)

        models_dir = get_data_dir() / "models" / "vosk"
        models_dir.mkdir(parents=True, exist_ok=True)
        extracted = next(models_dir.glob("vosk-model-small-en-us-*"), None)
        if extracted:
            return str(extracted)

        print("[VOICE] Downloading the Vosk model (one-time, ~40 MB)…")
        data = urllib.request.urlopen(_VOSK_MODEL_URL, timeout=120).read()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(models_dir)
        extracted = next(models_dir.glob("vosk-model-small-en-us-*"), None)
        return str(extracted) if extracted else None
    except Exception as exc:  # noqa: BLE001
        print(f"[VOICE] Vosk model unavailable: {exc}")
        return None

STATES = ("idle", "wake_listening", "listening", "transcribing", "thinking", "speaking")

# Stop listening after this much silence in the "listening" state.
_LISTEN_SILENCE_MS = 2000
_LISTEN_MAX_MS = 20_000


class VoicePipeline:
    """Native wake-word → transcribe → LLM → streamed speech interface."""

    def __init__(
        self,
        on_state: Callable[[str, dict], None] | None = None,
        process_command: Callable[[str], str] | None = None,
        config: dict | None = None,
        audio: AudioIO | None = None,
        wake: BaseWakeWord | None = None,
        transcriber: StreamingTranscriber | None = None,
        speaker: StreamingSpeaker | None = None,
    ):
        if config is None:
            # Read the real A3THER config (vosk model path, TTS engine, …)
            # so the voice loop respects the saved voice settings.
            try:
                from config import get_config

                config = get_config()
            except Exception:  # noqa: BLE001
                config = {}
        self.config = config or {}
        # If a Vosk engine is selected, make sure a model is actually
        # available — downloading the small model on first use so a fresh
        # install (or the exe) works out of the box.
        if (
            str(self.config.get("stt_engine", "vosk")).lower() == "vosk"
            or str(self.config.get("wake_word_engine", "vosk")).lower() == "vosk"
        ):
            model = _ensure_vosk_model()
            if model:
                self.config["vosk_model_path"] = model
        self.on_state = on_state
        self._process_command = process_command

        self.audio = audio or AudioIO()
        try:
            self.wake = wake or create_wake_word(self.config)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Wake-word init failed (%s) — using no-op backend", exc)
            self.wake = _NoopWakeWord()
        self.transcriber = transcriber or StreamingTranscriber(
            engine=str(self.config.get("stt_engine", "vosk")),
            language=str(self.config.get("vosk_language", "en-us")),
            whisper_model=str(self.config.get("whisper_model", "base")),
        )
        self.speaker = speaker or StreamingSpeaker(config=self.config)

        self._state = "idle"
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture: threading.Thread | None = None
        self._speech_buf: list = []
        self._silence_ms = 0
        self._listen_start = 0.0
        # Gemini-Live style mode: after a turn the loop keeps listening for the
        # next utterance instead of dropping back to the wake word.
        self._live_mode = False

    # ------------------------------------------------------------------ #
    # Public control
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start continuous listening (non-blocking)."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name="voice-pipeline", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self.audio.stop()
        except Exception:  # noqa: BLE001
            pass
        self.speaker.stop()
        self._set_state("idle", {})

    def set_process_command(self, fn: Callable[[str], str]) -> None:
        self._process_command = fn

    def set_live_mode(self, enabled: bool) -> None:
        """Toggle Gemini-Live style continuous conversation.

        When enabled the loop keeps listening after each turn (no need to
        re-say the wake word); when disabled it returns to wake listening.
        """
        self._live_mode = bool(enabled)

    @property
    def live_mode(self) -> bool:
        return self._live_mode

    @property
    def state(self) -> str:
        return self._state

    # ------------------------------------------------------------------ #
    # Internal loop
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        self.speaker.start()
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                self.audio.start()
                self.wake.reset()
                self._capture_loop()
                backoff = 1.0
            except AudioDeviceError as exc:
                LOGGER.warning("Audio error: %s — retrying in %.0fs", exc, backoff)
                self._set_state("audio_error", {"error": str(exc)})
                self.audio.stop()
                self._set_state("wake_listening", {})
                if self._stop_event.wait(timeout=backoff):
                    break
                backoff = min(backoff * 2, 15.0)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Voice pipeline error: %s", exc)
                if self._stop_event.wait(timeout=2.0):
                    break
        self.audio.stop()

    def _capture_loop(self) -> None:
        """The wake/listen/transcribe state machine, driven by audio blocks."""
        self._set_state("listening" if self._live_mode else "wake_listening", {})
        self._speech_buf = []
        self._silence_ms = 0
        self._listen_start = 0.0

        while not self._stop_event.is_set() and not self.audio.is_dead():
            block = self.audio.read_block(timeout=0.25)
            if block is None:
                continue

            if self._state in ("wake_listening", "idle"):
                if self.wake.consume(block):
                    LOGGER.info("Wake word detected")
                    self._begin_listening()
                continue

            # listening / transcribing
            text, is_final = self.transcriber.feed(block)
            self._silence_ms += 30

            if text and self._state == "listening":
                self._set_state("transcribing", {"partial": text})

            if is_final:
                if text and text.strip():
                    self._handle_utterance(text.strip())
                    if self._live_mode:
                        # Gemini-Live: keep the conversation going without a
                        # re-wake; loop back into listening immediately.
                        self._begin_listening()
                        continue
                    return  # after a full turn, loop restarts wake listening
                self._begin_listening()

            if self._silence_ms >= _LISTEN_SILENCE_MS and self._state == "listening":
                if self._live_mode:
                    # In live mode a pause just resets the listening window.
                    self.transcriber.reset()
                    self._silence_ms = 0
                    self._listen_start = time.monotonic()
                    continue
                LOGGER.info("Silence timeout — returning to wake listening")
                self.transcriber.reset()
                self._set_state("wake_listening", {})
                self._speech_buf = []

            if self._state in ("listening", "transcribing") and (
                time.monotonic() - self._listen_start
            ) > (_LISTEN_MAX_MS / 1000):
                self.transcriber.reset()
                if self._live_mode:
                    self._listen_start = time.monotonic()
                    self._silence_ms = 0
                else:
                    self._set_state("wake_listening", {})

    def _begin_listening(self) -> None:
        self.transcriber.reset()
        self._speech_buf = []
        self._silence_ms = 0
        self._listen_start = time.monotonic()
        self.speaker.interrupt()  # user is talking — cut our speech
        self._set_state("listening", {})

    def _handle_utterance(self, text: str) -> None:
        """Full turn: think → speak → return to wake listening."""
        self._set_state("thinking", {"transcript": text})
        try:
            response = self._process_command(text) if self._process_command else ""
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Command handler failed")
            response = f"I hit an error while processing that: {exc}"
        if response and response.strip():
            self._set_state("speaking", {"response": response})
            self.speaker.say(response)
        self.transcriber.reset()
        self._set_state("wake_listening", {})

    # ------------------------------------------------------------------ #
    def _set_state(self, state: str, payload: dict) -> None:
        self._state = state
        if self.on_state:
            try:
                self.on_state(state, payload)
            except Exception:  # noqa: BLE001
                pass


class _NoopWakeWord(BaseWakeWord):
    """Fallback when no wake-word backend can be initialised."""

    name = "noop"

    def consume(self, frame) -> bool:
        return False


# ------------------------------------------------------------------------- #
# Singleton
# ------------------------------------------------------------------------- #
_PIPELINE: VoicePipeline | None = None
_PIPELINE_LOCK = threading.Lock()


def get_voice_pipeline(**kwargs) -> VoicePipeline:
    """Return the process-wide voice pipeline singleton.

    The conversational brain (voice.brain.GenerateResponse) is wired in
    automatically when no other process-command callback was supplied, so a
    wake word always leads somewhere: native intents are executed instantly
    and everything else goes to the LLM gateway.
    """
    global _PIPELINE
    if _PIPELINE is None:
        with _PIPELINE_LOCK:
            if _PIPELINE is None:
                _PIPELINE = VoicePipeline(**kwargs)
                if _PIPELINE._process_command is None:  # noqa: SLF001
                    try:
                        from .brain import GenerateResponse

                        _PIPELINE.set_process_command(GenerateResponse)
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.warning("Voice brain not wired: %s", exc)
    return _PIPELINE
