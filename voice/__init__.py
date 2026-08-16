"""
A3THER Native Voice Interface.

Continuous, local wake-word listening ("Hey Aether"), streaming speech
recognition, and low-latency streaming speech output — orchestrated by an
event-driven :class:`voice.pipeline.VoicePipeline`.

Architecture
------------
- :mod:`voice.audio_io`       — microphone input with device-loss recovery
- :mod:`voice.wake_word`      — Porcupine / Vosk wake-word backends
- :mod:`voice.stream_stt`     — streaming transcription (Vosk / Whisper)
- :mod:`voice.tts_stream`     — sentence-streamed TTS with interrupt
- :mod:`voice.pipeline`       — the event-driven state machine

Typical wiring::

    from voice.pipeline import get_voice_pipeline

    pipeline = get_voice_pipeline()
    pipeline.set_process_command(brain.GenerateResponse)   # LLM callback
    pipeline.start()                                        # non-blocking
"""
from .pipeline import VoicePipeline, get_voice_pipeline

__all__ = ["VoicePipeline", "get_voice_pipeline"]
