"""
Text-to-Speech engines for MARK XL.

EdgeTTS     – free Microsoft TTS (internet required, no API key)
Kokoro      – fully offline neural TTS (~330 MB model)
ElevenLabs  – cloud API (API key required, best quality)
"""
from __future__ import annotations

import asyncio
import os
import queue as _queue
import re
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd



# USE_TF=0 stops transformers from importing TensorFlow (saves 4-8 s startup).
# Do NOT set USE_TORCH or USE_JAX explicitly — forcing those values breaks
# transformers' lazy-loader on certain versions, causing AutoModel and other
# classes to vanish from the public namespace.  Auto-detection is reliable.
os.environ.setdefault("USE_TF",                 "0")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


# ---------------------------------------------------------------------------
# Text cleanup for speech
# ---------------------------------------------------------------------------
# LLM output is full of markdown, URLs, emojis and repeated punctuation that
# speech engines read out verbatim ("asterisk asterisk bold asterisk").
# clean_for_speech() strips those artifacts and turns the rest into plain
# natural language so TTS sounds like a person, not a markdown reader.

_CODE_BLOCK_RE   = re.compile(r"```.*?```", re.DOTALL)
_IMAGE_ALT_RE    = re.compile(r"!\[([^\]]*)\]\((?:[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_URL_RE          = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
_EMOJI_RANGE_RE  = re.compile(
    "[\U0001F000-\U0001FAFF\U0001F900-\U0001F9FF\U0001F1E6-\U0001F1FF"
    "\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)

# Common emojis -> spoken words (unknown ones are simply dropped).
_EMOJI_WORDS = {
    "😂": "laughing face",
    "😅": "awkward smile",
    "😀": "smiling face",
    "😊": "smiling face",
    "😍": "heart eyes",
    "🥰": "loving face",
    "😎": "cool face",
    "🤔": "thinking face",
    "🙄": "eye roll",
    "👍": "thumbs up",
    "👎": "thumbs down",
    "🙏": "thank you",
    "👏": "applause",
    "🎉": "celebration",
    "🎊": "celebration",
    "❤️": "heart",
    "❤": "heart",
    "💖": "heart",
    "💯": "one hundred",
    "✅": "checkmark",
    "✔": "checkmark",
    "❌": "cross mark",
    "⚠️": "warning",
    "⚠": "warning",
    "🚨": "alert",
    "📢": "announcement",
    "🔥": "fire",
    "💡": "idea",
    "📌": "pin",
    "🔴": "red circle",
    "⚡": "lightning",
    "⭐": "star",
    "🌟": "star",
    "✨": "sparkles",
    "🚀": "rocket",
    "🎯": "bullseye",
    "🧠": "brain",
    "🤖": "robot",
    "😴": "sleeping face",
    "😭": "crying face",
    "😡": "angry face",
    "😱": "screaming face",
    "🤯": "mind blown",
    "🥳": "party face",
}


def clean_for_speech(text: str) -> str:
    """Prepare text so TTS speaks it naturally instead of literally.

    Strips code blocks, markdown markers, URLs, emojis and repeated
    punctuation; converts ``[text](url)`` links to their display text.
    Idempotent — safe to run on already-cleaned text (the streaming
    speaker cleans the whole response, then each sentence again).
    """
    if not text:
        return ""
    out = str(text)
    # Code blocks -> a plain pause (reading code aloud is noise).
    out = _CODE_BLOCK_RE.sub(" ", out)
    # Markdown images / links -> their alt / display text.
    out = _IMAGE_ALT_RE.sub(r"\1", out)
    out = _MARKDOWN_LINK_RE.sub(r"\1", out)
    # Bare URLs -> the word "link".
    out = _URL_RE.sub("link", out)
    # Emojis -> a spoken word where we know one, otherwise dropped.
    for emoji in sorted(_EMOJI_WORDS, key=len, reverse=True):
        if emoji in out:
            out = out.replace(emoji, f" {_EMOJI_WORDS[emoji]} ")
    out = _EMOJI_RANGE_RE.sub("", out)
    # Markdown markers: ## headers, **bold**, *italic*, __, ~~, `code`.
    out = re.sub(r"^#{1,6}\s*", "", out, flags=re.M)
    out = out.replace("**", " ").replace("__", " ").replace("~~", " ")
    out = re.sub(r"(?<!\w)\*(?!\w)", " ", out)      # * bullets / emphasis
    out = out.replace("`", "")
    out = re.sub(r"^\s*[-+]\s+", "", out, flags=re.M)      # "- " / "+ " bullets
    out = re.sub(r"^\s*\d+[.)]\s+", "", out, flags=re.M)  # "1." list numbers
    # Collapse repeated punctuation: "!!!" -> "!", "..." -> "."
    out = re.sub(r"([.!?]){3,}", r"\1", out)
    # No stray spaces before punctuation: "rocket ." -> "rocket."
    out = re.sub(r"\s+([.,!?;:])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


# ---------------------------------------------------------------------------
# Audio playback helpers
# ---------------------------------------------------------------------------

def _to_numpy(samples) -> np.ndarray:
    """Convert samples to float32 numpy array.

    Handles both numpy arrays and PyTorch tensors (Kokoro >= 0.9).

    PyTorch built against numpy 1.x raises RuntimeError('Numpy is not available')
    when numpy 2.x is installed.  The .tolist() fallback always works regardless
    of PyTorch / numpy version pairing.
    """
    if hasattr(samples, "detach"):                  # PyTorch tensor
        t = samples.detach().cpu().float()
        try:
            return t.numpy()                        # fast path (compatible versions)
        except RuntimeError:
            # PyTorch/numpy version mismatch — convert via Python list (always safe)
            return np.asarray(t.tolist(), dtype=np.float32)
    return np.asarray(samples, dtype=np.float32)


def _compress_silence(
    arr: np.ndarray,
    sample_rate: int    = 24_000,
    max_silence_ms: int = 500,    # cap punctuation pauses — keeps natural rhythm
    threshold: float    = 0.003,  # RMS below this = silence; lower = less clipping
) -> np.ndarray:
    """
    Shorten Kokoro's very long punctuation pauses (1-2 s → ≤500 ms).
    Conservative settings preserve natural prosody; only trims extreme pauses.
    """
    max_samp  = int(max_silence_ms * sample_rate / 1000)
    frame_len = 240                   # ~10 ms at 24 kHz
    out: list[np.ndarray] = []
    silent_acc = 0

    for i in range(0, len(arr), frame_len):
        chunk = arr[i : i + frame_len]
        if np.sqrt(np.mean(chunk ** 2) + 1e-12) < threshold:
            silent_acc += len(chunk)
            if silent_acc <= max_samp:
                out.append(chunk)
        else:
            silent_acc = 0
            out.append(chunk)

    return np.concatenate(out) if out else arr


def _play_np(samples, sample_rate: int) -> None:
    """Play float32 mono (or stereo) audio via sounddevice.
    Accepts numpy arrays or PyTorch tensors.
    """
    sd.play(_to_numpy(samples), sample_rate)
    sd.wait()


def _play_audio_bytes(audio_bytes: bytes) -> None:
    """Decode MP3/WAV/OGG bytes and play via sounddevice (uses miniaudio)."""
    import miniaudio
    decoded = miniaudio.decode(
        audio_bytes,
        output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=1,
    )
    samples = np.array(decoded.samples, dtype=np.float32)
    sd.play(samples, decoded.sample_rate)
    sd.wait()


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class EdgeTTSEngine:
    """Microsoft EdgeTTS – free, requires internet.

    Auto-falls back to the Windows SAPI voice (:class:`SapiTTSEngine`) when
    the internet stream fails or the audio decoder is unavailable, so the
    assistant ALWAYS speaks — never a silent failure.
    """

    def __init__(self, voice: str = "en-US-GuyNeural"):
        self.voice = voice
        self._fallback = SapiTTSEngine()

    def speak(self, text: str) -> None:
        try:
            loop = asyncio.new_event_loop()
            try:
                audio_bytes = loop.run_until_complete(self._synth(text))
            finally:
                loop.close()
            if audio_bytes:
                _play_audio_bytes(audio_bytes)
        except Exception as exc:  # noqa: BLE001
            print(f"[TTS] EdgeTTS failed ({type(exc).__name__}: {exc}) — falling back to Windows voice…")
            self._fallback.speak(text)

    async def _synth(self, text: str) -> bytes:
        import aiohttp
        import edge_tts

        # aiohttp prefers the c-ares resolver when `aiodns` is installed; on
        # some networks c-ares cannot reach the configured DNS servers while
        # the OS resolver works fine, so Edge TTS died with "Could not contact
        # DNS servers" and the robot SAPI voice took over. Force the threaded
        # (system) resolver so natural Edge voices always connect.
        connector = aiohttp.TCPConnector(resolver=aiohttp.resolver.ThreadedResolver())
        try:
            comm = edge_tts.Communicate(text, self.voice, connector=connector)
            buf  = bytearray()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)
        finally:
            await connector.close()


class SapiTTSEngine:
    """Windows native SAPI speech (pyttsx3) — fully offline, zero deps.

    Guaranteed fallback so the voice always speaks, even with no internet
    or no audio decoder package. Selecting a voice is best-effort; unknown
    voice names silently fall back to the default system voice.
    """

    def __init__(self, voice: str | None = None):
        self.voice = voice

    def speak(self, text: str) -> None:
        try:
            import pyttsx3  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            print(f"[TTS] No fallback voice available: {exc}")
            return
        engine = pyttsx3.init()
        try:
            if self.voice:
                try:
                    engine.setProperty("voice", self.voice)
                except Exception:  # noqa: BLE001 — unknown voice → default
                    pass
            engine.say(text)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Kokoro import helper — auto-upgrades on version-mismatch errors
# ---------------------------------------------------------------------------

# Errors that indicate the installed kokoro uses old transformers classes
# (AlbertModel, AutoModel) that are no longer exported at the top level.
_KOKORO_COMPAT_ERRORS = ("AlbertModel", "AutoModel", "cannot import name")


def _import_kokoro_pipeline():
    """Import KPipeline, auto-upgrading kokoro if a version mismatch is found.

    Old kokoro (<0.9) imports AlbertModel / AutoModel from transformers.
    Newer transformers versions no longer export these at the top level,
    causing an ImportError.  kokoro>=0.9 removed these dependencies.

    When the error is detected we:
      1. Upgrade kokoro to >=0.9 via pip (silent, background)
      2. Flush stale kokoro entries from sys.modules
      3. Re-import — this time it should succeed
    """
    import sys

    def _try_import():
        from kokoro import KPipeline  # noqa: PLC0415
        return KPipeline

    try:
        return _try_import()
    except Exception as first_err:
        err_msg = str(first_err)
        if not any(marker in err_msg for marker in _KOKORO_COMPAT_ERRORS):
            # Unrelated error (kokoro not installed, etc.)
            raise RuntimeError(
                f"Kokoro import failed: {first_err}\n"
                "Run: pip install kokoro>=0.9 soundfile"
            ) from first_err

        # ── Version mismatch: upgrade kokoro silently and retry ──────────
        print("[TTS] Kokoro/transformers version mismatch detected — upgrading kokoro…")
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "kokoro>=0.9",
             "--upgrade", "--quiet", "--disable-pip-version-check"],
            capture_output=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"Kokoro auto-upgrade failed: {stderr[:200]}\n"
                "Run manually: pip install kokoro>=0.9 soundfile"
            ) from first_err

        # Flush any stale kokoro submodules from the import cache
        stale = [k for k in sys.modules if k == "kokoro" or k.startswith("kokoro.")]
        for key in stale:
            del sys.modules[key]

        print("[TTS] Kokoro upgraded — retrying import…")
        try:
            return _try_import()
        except Exception as retry_err:
            raise RuntimeError(
                f"Kokoro still broken after upgrade: {retry_err}\n"
                "Run manually: pip install --upgrade kokoro transformers"
            ) from retry_err


# Kokoro voice prefix → KPipeline lang_code mapping
_KOKORO_LANG_CODES = {
    "a": "a",   # American English  (af_*, am_*)
    "b": "b",   # British English   (bf_*, bm_*)
    "j": "j",   # Japanese          (jf_*, jm_*)
    "z": "z",   # Mandarin Chinese  (zf_*, zm_*)
    "s": "s",   # Spanish           (sf_*, sm_*)
    "f": "f",   # French            (ff_*, fm_*)
    "h": "h",   # Hindi             (hf_*, hm_*)
    "i": "i",   # Italian           (if_*, im_*)
    "p": "p",   # Brazilian Portuguese
    "r": "r",   # Russian           (rf_*, rm_*)
    "e": "e",   # German            (ef_*, em_*)
}


class KokoroTTSEngine:
    """Fully offline Kokoro neural TTS.

    Model (~330 MB) is downloaded from HuggingFace on first use,
    then cached locally — subsequent starts load from disk.

    Warmup strategy: _init() runs synchronously in the background
    _do_tts() thread (not the UI thread).  After the pipeline loads,
    a dummy inference compiles the PyTorch JIT graph immediately so
    the first real speak() call has zero compilation overhead.
    """

    def __init__(self, voice: str = "af_heart", speed: float = 1.0):
        self.voice     = voice
        self.speed     = speed
        self._pipeline = None
        self._lock     = threading.Lock()
        self._init()   # blocking, but called from background thread

    @property
    def _lang_code(self) -> str:
        prefix = self.voice[0].lower() if self.voice else "a"
        return _KOKORO_LANG_CODES.get(prefix, "a")

    def _init(self) -> None:
        if self._pipeline is not None:
            return

        lang = self._lang_code

        # Prefer GPU — Kokoro on CUDA is ~10x faster than CPU.
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            if device == "cpu":
                import os as _os
                n_threads = max(1, min(4, (_os.cpu_count() or 4) // 2))
                try:
                    torch.set_num_threads(n_threads)
                    torch.set_num_interop_threads(2)
                except RuntimeError:
                    pass
                print(
                    f"[TTS] Kokoro on CPU — for faster speech install CUDA PyTorch:\n"
                    "      pip install torch --index-url https://download.pytorch.org/whl/cu118"
                )
        except Exception:
            device = "cpu"

        print(f"[TTS] Kokoro — loading (lang='{lang}', device='{device}')…")

        KPipeline = _import_kokoro_pipeline()

        def _create_pipeline():
            try:
                return KPipeline(lang_code=lang, device=device)
            except TypeError:
                return KPipeline(lang_code=lang)   # older build — no device param

        try:
            self._pipeline = _create_pipeline()
        except Exception as _first_err:
            # Offline flag set but model not cached yet → clear flags and download once.
            # Keywords cover multiple huggingface_hub error message variants across versions.
            _e = str(_first_err).lower()
            _offline_keywords = (
                "offline", "not found", "cache", "localentry",
                "does not exist", "outgoing", "local_files_only",
            )
            if any(k in _e for k in _offline_keywords):
                print("[TTS] Kokoro model not in local cache — downloading (one-time, internet required)…")
                os.environ.pop("HF_HUB_OFFLINE",      None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                os.environ.pop("HF_DATASETS_OFFLINE",  None)
                try:
                    self._pipeline = _create_pipeline()
                except Exception as _dl_err:
                    raise RuntimeError(
                        f"Kokoro model download failed.\n"
                        f"Internet access is required the first time to download the voice model (~330 MB).\n"
                        f"After the first download it runs fully offline.\n"
                        f"Tip: Switch to EdgeTTS (free, no download) in the Configure panel if offline.\n"
                        f"Details: {_dl_err}"
                    ) from _dl_err
            else:
                raise

        print("[TTS] Kokoro compiling (first-time only)…")
        # Warmup: compiles PyTorch JIT graph so first real speak() call is instant.
        try:
            for _ in self._pipeline("hello", voice=self.voice, speed=self.speed):
                pass
            print("[TTS] Kokoro ready.")
        except Exception as e:
            print(f"[TTS] Kokoro warmup warning: {e}")

    def speak(self, text: str) -> None:
        with self._lock:
            if self._pipeline is None:
                self._init()

        # ── Concurrent synthesise + playback ────────────────────────────────
        # Kokoro generates audio chunks lazily.  Without threading, we:
        #   synthesise chunk N → play N → synthesise N+1 → play N+1 …
        # With a producer/consumer pair, chunk N+1 synthesises WHILE chunk N
        # plays, cutting perceived latency by the playback duration of all but
        # the last chunk (typically 1-3 s on multi-sentence responses).
        audio_q: "_queue.Queue[np.ndarray | None]" = _queue.Queue(maxsize=4)
        synth_error: list[Exception] = []

        def _synth():
            try:
                for _, _, audio in self._pipeline(text, voice=self.voice, speed=self.speed):
                    if audio is not None:
                        arr = _to_numpy(audio)
                        arr = _compress_silence(arr)
                        if arr.size > 0:
                            audio_q.put(arr)          # blocks if player is slow (backpressure)
            except Exception as exc:
                synth_error.append(exc)
            finally:
                audio_q.put(None)                     # sentinel → player exits

        synth_thread = threading.Thread(target=_synth, daemon=True)
        synth_thread.start()

        # Player runs in this thread so sd.wait() doesn't block the synth thread.
        while True:
            arr = audio_q.get()
            if arr is None:
                break
            _play_np(arr, 24000)

        synth_thread.join()

        if synth_error:
            raise synth_error[0]


class ElevenLabsTTSEngine:
    """ElevenLabs cloud TTS – API key required."""

    def __init__(self, api_key: str, voice_id: str = "pNInz6obpgDQGcFmaJgB"):
        self.api_key  = api_key
        self.voice_id = voice_id

    def speak(self, text: str) -> None:
        import requests
        headers = {
            "xi-api-key":   self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text":     text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
            json=payload, headers=headers, timeout=30,
        )
        resp.raise_for_status()
        _play_audio_bytes(resp.content)


# ---------------------------------------------------------------------------
# Thread-safe player wrapper
# ---------------------------------------------------------------------------

class TTSPlayer:
    """
    Wraps any *Engine. Exposes a blocking speak() method
    meant to be called from a dedicated background thread.
    """

    def __init__(self, engine):
        self._engine  = engine
        self._playing = False
        self._lock    = threading.Lock()

    @property
    def is_playing(self) -> bool:
        return self._playing

    def speak(
        self,
        text:     str,
        on_start: Optional[Callable] = None,
        on_done:  Optional[Callable] = None,
    ) -> None:
        """Synthesise and play text. BLOCKING – call from a dedicated thread."""
        text = clean_for_speech(text)
        if not text:
            if on_done:
                on_done()
            return
        try:
            with self._lock:
                self._playing = True
            if on_start:
                on_start()
            self._notify_popup(text, True)
            self._engine.speak(text)
        except Exception as e:
            print(f"[TTS] Error: {e}")
        finally:
            with self._lock:
                self._playing = False
            if on_done:
                on_done()
            self._notify_popup(text, False)

    @staticmethod
    def _notify_popup(text: str, speaking: bool) -> None:
        """Drive the talking popup overlay (logo + voice rings + transcript)."""
        try:
            from core import popup  # noqa: PLC0415

            if speaking:
                popup.say(text)
            else:
                popup.stop_speaking()
        except Exception:  # noqa: BLE001 — the popup must never break speech
            pass

    def stop(self) -> None:
        sd.stop()
        with self._lock:
            self._playing = False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_tts_player(config: dict, overrides: dict | None = None) -> TTSPlayer:
    merged = {**(config or {}), **(overrides or {})}
    engine_name = str(merged.get("tts_engine", "edgetts")).lower()
    voice = str(merged.get("tts_voice", "en-US-GuyNeural"))
    speed = float(merged.get("tts_speed", 1.0))
    if engine_name == "kokoro":
        engine = KokoroTTSEngine(voice=voice, speed=speed)
    elif engine_name == "elevenlabs":
        api_key = str(merged.get("elevenlabs_api_key", ""))
        voice_id = str(merged.get("tts_voice", "pNInz6obpgDQGcFmaJgB"))
        engine = ElevenLabsTTSEngine(api_key=api_key, voice_id=voice_id)
    else:   # edgetts (default)
        engine = EdgeTTSEngine(voice=voice)
    return TTSPlayer(engine)
