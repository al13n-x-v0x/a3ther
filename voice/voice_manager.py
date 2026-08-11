"""
voice/voice_manager.py — A3THER's highly-resilient local voice pipeline.

A multi-tier failover system for Speech-to-Text (STT) and Text-to-Speech (TTS).
If an engine crashes, throws, or fails to initialise, the pipeline silently
drops to the next backup tier WITHOUT stopping the main program loop.

Tier order (100% free, open-source, local):

  STT
    Tier 1  faster-whisper        (small.en / base.en, offline, GPU-ready)
    Tier 2  SpeechRecognition     (local PocketSphinx CMU engine, no network)
    Tier 3  Vosk + sounddevice    (offline Kaldi-style model, no network)

  TTS
    Tier 1  Kokoro-ONNX           (kokoro-v0_19.onnx, IndicVoice-82M if present)
    Tier 2  Piper                 (local piper.exe binary, streamed WAV)
    Tier 3  pyttsx3               (native OS engine wrapper — never crashes)

  BRAIN
    requests -> local Ollama (llama3.1) for the reply layer (streamless).

Every fallback prints a prominent [WARNING] line to the terminal, so you always
know which engine is live. Audio playback uses an asynchronous block
(sounddevice / pygame.mixer) so playback never freezes the terminal process.

--------------------------------------------------------------------------------
TERMINAL PIP INSTALL REQUIREMENTS
--------------------------------------------------------------------------------
Core (required):
    pip install requests sounddevice numpy

STT tiers:
    pip install faster-whisper            # Tier 1 (downloads model on first run)
    pip install SpeechRecognition pocketsphinx   # Tier 2 (local CMU engine)
    pip install vosk                      # Tier 3 (model: https://alphacephei.com/vosk/models)

TTS tiers:
    pip install kokoro onnxruntime soundfile   # Tier 1 (Kokoro-ONNX)
    #   Kokoro-ONNX model: https://huggingface.co/hexgrad/Kokoro-82M (kokoro-v0_19.onnx)
    #   IndicVoice-82M (optional Indian voices): https://huggingface.co/hexgrad/IndicVoice
    #   Piper: download piper.exe + a voice from https://github.com/rhasspy/piper/releases
    #          then point PIPER_BIN / PIPER_MODEL env vars at them.
    pip install pyttsx3                   # Tier 3 (never crashes, zero GPU)

Brain:
    pip install requests                  # already core — just run: ollama run llama3.1

--------------------------------------------------------------------------------
QUICKSTART
--------------------------------------------------------------------------------
    from voice.voice_manager import JarvisVoiceManager

    jarvis = JarvisVoiceManager()                 # engines probed lazily
    text   = jarvis.transcribe("listen.wav")      # or transcribe_mic(seconds=5)
    reply  = jarvis.ask_brain(text)               # Ollama llama3.1
    jarvis.speak(reply)                           # non-blocking playback

Engines are only imported the first time they are needed, so a missing optional
dependency costs nothing until its tier is actually required.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import wave
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Configuration (env-first, so it works in dev, the exe and Docker alike)
# ---------------------------------------------------------------------------

_CFG = {
    # Whisper model name for Tier-1 STT ("small.en" / "base.en" / "small").
    "whisper_model": os.environ.get("A3THER_WHISPER_MODEL", "small.en"),
    # Vosk model directory for Tier-3 STT.
    "vosk_model": os.environ.get("A3THER_VOSK_MODEL", "models/vosk-small"),
    # Kokoro-ONNX model + optional IndicVoice weights.
    "kokoro_onnx": os.environ.get("A3THER_KOKORO_ONNX", "models/kokoro-v0_19.onnx"),
    "indic_voice": os.environ.get("A3THER_INDIC_VOICE", "models/IndicVoice-82M.onnx"),
    "kokoro_voice": os.environ.get("A3THER_KOKORO_VOICE", "af_heart"),
    # Piper binary + model (Tier-2 TTS).
    "piper_bin": os.environ.get("PIPER_BIN", "piper/piper.exe"),
    "piper_model": os.environ.get("PIPER_MODEL", "piper/en_US-lessac-medium.onnx"),
    # Ollama brain.
    "ollama_url": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
    "brain_model": os.environ.get("A3THER_BRAIN_MODEL", "llama3.1"),
    # Audio.
    "sample_rate": int(os.environ.get("A3THER_SAMPLE_RATE", "16000")),
    "playback_backend": os.environ.get("A3THER_PLAYBACK", "auto"),  # auto|sounddevice|pygame
}


def _warn(tier: str, engine: str, exc: BaseException) -> None:
    """Prominent, impossible-to-miss fallback warning."""
    print(
        f"[WARNING]: Primary {tier} failed ({type(exc).__name__}: {exc}). "
        f"Falling back to Tier {tier} ({engine})..."
    )


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

class _AsyncPlayer:
    """Non-blocking playback so speech never freezes the main loop.

    Two backends are supported: ``sounddevice`` (fast, low-level) and
    ``pygame.mixer`` (battle-tested). The player runs playback on a daemon
    thread; :meth:`play` returns immediately.
    """

    def __init__(self) -> None:
        self._backend: str | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        """Initialise the chosen backend exactly once (first successful)."""
        if self._backend is not None:
            return
        choice = _CFG["playback_backend"]
        if choice in ("auto", "sounddevice"):
            try:
                import sounddevice as sd  # noqa: PLC0415

                sd  # touch
                self._backend = "sounddevice"
                return
            except Exception:  # noqa: BLE001
                if choice == "sounddevice":
                    print("[WARNING]: sounddevice playback unavailable — trying pygame…")
        if choice in ("auto", "pygame"):
            try:
                import pygame  # noqa: PLC0415

                pygame.mixer.init(frequency=22050, channels=1)
                self._backend = "pygame"
                return
            except Exception as exc:  # noqa: BLE001
                print(f"[WARNING]: pygame.mixer unavailable ({exc}) — playback disabled.")

    def play_wav(self, path: str | Path) -> None:
        """Play a WAV file on a daemon thread; returns immediately."""
        self._ensure()
        if self._backend is None:
            return
        threading.Thread(target=self._play_sync, args=(str(path),), daemon=True).start()

    def _play_sync(self, path: str) -> None:
        try:
            if self._backend == "sounddevice":
                import sounddevice as sd  # noqa: PLC0415
                import soundfile as sf  # noqa: PLC0415

                data, rate = sf.read(path, dtype="float32")
                sd.play(data, rate)
                sd.wait()
            elif self._backend == "pygame":
                import pygame  # noqa: PLC0415

                with self._lock:
                    pygame.mixer.music.load(path)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARNING]: playback failed ({type(exc).__name__}: {exc})")

    def play_raw(self, audio: np.ndarray, rate: int) -> None:
        """Play raw float32 numpy audio on a daemon thread."""
        self._ensure()
        if self._backend is None:
            return
        threading.Thread(target=self._play_raw_sync, args=(audio, rate), daemon=True).start()

    def _play_raw_sync(self, audio: np.ndarray, rate: int) -> None:
        try:
            if self._backend == "sounddevice":
                import sounddevice as sd  # noqa: PLC0415

                sd.play(audio, rate)
                sd.wait()
        except Exception as exc:  # noqa: BLE001
            print(f"[WARNING]: raw playback failed ({type(exc).__name__}: {exc})")


# ---------------------------------------------------------------------------
# STT tiers
# ---------------------------------------------------------------------------

class FasterWhisperSTT:
    """Tier 1 — faster-whisper (CTranslate2, offline, GPU-capable)."""

    name = "faster-whisper"

    def __init__(self) -> None:
        self._model = None

    def _load(self) -> None:
        if self._model is None:
            from faster_whisper import WhisperModel  # noqa: PLC0415

            self._model = WhisperModel(
                _CFG["whisper_model"], device="auto", compute_type="auto"
            )

    def transcribe(self, wav_path: str, lang: str | None = None) -> str:
        self._load()
        segments, _ = self._model.transcribe(
            wav_path, language=lang, beam_size=1, vad_filter=True
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


class PocketSphinxSTT:
    """Tier 2 — SpeechRecognition + PocketSphinx (pure local CMU engine)."""

    name = "PocketSphinx"

    def transcribe(self, wav_path: str, lang: str | None = None) -> str:
        import speech_recognition as sr  # noqa: PLC0415

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)
        # language is ignored by PocketSphinx (en-US only) — kept for API parity.
        return recognizer.recognize_sphinx(audio).strip()


class VoskSTT:
    """Tier 3 — Vosk + sounddevice live capture (offline, tiny footprint)."""

    name = "Vosk"

    def __init__(self) -> None:
        self._model = None

    def _load(self) -> None:
        if self._model is None:
            from vosk import Model, KaldiRecognizer  # noqa: PLC0415

            self._model = Model(_CFG["vosk_model"])

    def transcribe(self, wav_path: str, lang: str | None = None) -> str:
        self._load()
        from vosk import KaldiRecognizer  # noqa: PLC0415

        rec = KaldiRecognizer(self._model, _CFG["sample_rate"])
        rec.SetWords(False)
        with wave.open(wav_path, "rb") as wf:
            while True:
                chunk = wf.readframes(4000)
                if not chunk:
                    break
                rec.AcceptWaveform(chunk)
        result = rec.FinalResult()
        import json  # noqa: PLC0415

        try:
            return json.loads(result).get("text", "").strip()
        except Exception:  # noqa: BLE001
            return ""


# ---------------------------------------------------------------------------
# TTS tiers
# ---------------------------------------------------------------------------

class KokoroOnnxTTS:
    """Tier 1 — Kokoro-ONNX (v0.19 graph, IndicVoice-82M Indian voices optional)."""

    name = "Kokoro-ONNX"

    def __init__(self) -> None:
        self._session = None
        self._loaded_path: str | None = None

    def _pick_model(self) -> str:
        """Prefer IndicVoice-82M (Indian accents) when present, else v0.19."""
        if Path(_CFG["indic_voice"]).is_file():
            return _CFG["indic_voice"]
        return _CFG["kokoro_onnx"]

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort  # noqa: PLC0415

        path = self._pick_model()
        self._session = ort.InferenceSession(
            path, providers=["CPUExecutionProvider"]
        )
        self._loaded_path = path

    def speak_to_file(self, text: str, out_wav: str) -> str | None:
        """Synthesise to a WAV file; returns the path or None on failure."""
        try:
            self._load()
        except Exception as exc:  # noqa: BLE001
            print(f"        (Kokoro init failed: {type(exc).__name__}: {exc})")
            return None
        try:
            from kokoro_onnx import Kokoro  # noqa: PLC0415

            kokoro = Kokoro(self._loaded_path, self._session)
            samples, sample_rate = kokoro.create(
                text, voice=_CFG["kokoro_voice"], speed=1.0, lang="en-us"
            )
            import soundfile as sf  # noqa: PLC0415

            sf.write(out_wav, samples, sample_rate)
            return out_wav
        except Exception as exc:  # noqa: BLE001
            print(f"        (Kokoro synth failed: {type(exc).__name__}: {exc})")
            return None


class PiperTTS:
    """Tier 2 — Piper binary streamed straight into a WAV file."""

    name = "Piper"

    def speak_to_file(self, text: str, out_wav: str) -> str | None:
        if not shutil.which(_CFG["piper_bin"]) and not Path(_CFG["piper_bin"]).is_file():
            return None
        try:
            import subprocess  # noqa: PLC0415

            with open(out_wav, "wb") as fh:
                proc = subprocess.run(
                    [_CFG["piper_bin"], "--model", _CFG["piper_model"],
                     "--output_file", out_wav],
                    input=text.encode("utf-8"),
                    stdout=fh,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )
            if proc.returncode != 0:
                return None
            return out_wav
        except Exception as exc:  # noqa: BLE001
            print(f"        (Piper failed: {type(exc).__name__}: {exc})")
            return None


class Pyttsx3TTS:
    """Tier 3 — pyttsx3, the native OS engine wrapper. Never crashes."""

    name = "pyttsx3"

    def speak(self, text: str) -> bool:
        try:
            import pyttsx3  # noqa: PLC0415

            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"        (pyttsx3 failed: {type(exc).__name__}: {exc})")
            return False


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------

class JarvisVoiceManager:
    """OOP facade over the whole multi-tier STT/TTS pipeline + Ollama brain.

    Usage::

        jarvis = JarvisVoiceManager()
        transcript = jarvis.transcribe("in.wav")     # auto-fails over 3 STT tiers
        reply = jarvis.ask_brain(transcript)         # Ollama llama3.1
        jarvis.speak(reply)                          # auto-fails over 3 TTS tiers
    """

    def __init__(self) -> None:
        self.stt_tiers = [FasterWhisperSTT(), PocketSphinxSTT(), VoskSTT()]
        self.tts_tiers = [KokoroOnnxTTS(), PiperTTS(), Pyttsx3TTS()]
        self.player = _AsyncPlayer()
        self._tmpdir = Path(os.environ.get("TEMP", Path.home() / ".a3ther_tmp"))
        self._tmpdir.mkdir(parents=True, exist_ok=True)

    # ---------------- STT ----------------

    def transcribe(self, wav_path: str, lang: str | None = None) -> str:
        """Run every STT tier in order; return the first transcript that works."""
        last_err: BaseException | None = None
        for idx, tier in enumerate(self.stt_tiers, start=1):
            try:
                text = tier.transcribe(wav_path, lang=lang)
                if text:
                    return text
                raise RuntimeError("empty transcript")
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if idx < len(self.stt_tiers):
                    _warn(idx, self.stt_tiers[idx].name, exc)
        print(f"[ERROR]: all STT tiers failed — last error: {last_err}")
        return ""

    def transcribe_mic(self, seconds: float = 5.0) -> str:
        """Capture from the default microphone and transcribe it."""
        import sounddevice as sd  # noqa: PLC0415

        rate = _CFG["sample_rate"]
        audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16")
        sd.wait()
        path = self._tmpdir / f"mic_{int(time.time())}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(audio.tobytes())
        return self.transcribe(str(path))

    # ---------------- TTS ----------------

    def speak(self, text: str) -> bool:
        """Speak with multi-tier failover. Playback is asynchronous."""
        if not text:
            return False
        # Tiers 1-2 render to WAV and stream through the async player.
        for idx in (1, 2):
            tier = self.tts_tiers[idx - 1]
            try:
                out = self._tmpdir / f"tts_{int(time.time() * 1000)}.wav"
                if isinstance(tier, (KokoroOnnxTTS, PiperTTS)):
                    result = tier.speak_to_file(text, str(out))
                    if result:
                        self.player.play_wav(result)
                        return True
                raise RuntimeError("tier failed")
            except Exception as exc:  # noqa: BLE001
                _warn(idx, tier.name, exc)
        # Tier 3 — pyttsx3 speaks synchronously but never crashes.
        if self.tts_tiers[2].speak(text):
            return True
        print("[ERROR]: all TTS tiers failed.")
        return False

    # ---------------- BRAIN (Ollama) ----------------

    def ask_brain(self, prompt: str, system: str | None = None) -> str:
        """Send the transcript to a local Ollama llama3.1 instance.

        Streamless for simplicity — one POST, one JSON reply.
        """
        import requests  # noqa: PLC0415

        payload = {
            "model": _CFG["brain_model"],
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.7},
        }
        if system:
            payload["system"] = system
        resp = requests.post(
            f"{_CFG['ollama_url']}/api/generate", json=payload, timeout=120
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

    # ---------------- CONVENIENCE ----------------

    def hear_and_reply(self, seconds: float = 5.0, system: str | None = None) -> str:
        """One-shot: listen -> transcribe -> Ollama -> speak. Returns the reply."""
        text = self.transcribe_mic(seconds)
        if not text:
            self.speak("I could not hear you clearly.")
            return ""
        print(f"[YOU]: {text}")
        reply = self.ask_brain(text, system=system)
        print(f"[A3THER]: {reply}")
        self.speak(reply)
        return reply


# ---------------------------------------------------------------------------
# CLI smoke test:  python -m voice.voice_manager  "say this out loud"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    jarvis = JarvisVoiceManager()
    if len(sys.argv) > 1:
        print(f"[A3THER]: {sys.argv[1]}")
        jarvis.speak(sys.argv[1])
    else:
        jarvis.hear_and_reply(seconds=5)
