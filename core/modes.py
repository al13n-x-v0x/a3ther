"""Mode manager for A3ther personality and operational modes."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class ModeSettings:
    name: str
    description: str
    guidance: str
    tts_voice: str
    tts_speed: float
    tone: str


DEFAULT_MODE = "ai"

MODE_REGISTRY: Dict[str, ModeSettings] = {
    "ai": ModeSettings(
        name="AI Mode",
        description="Balanced assistant mode for general purpose productivity.",
        guidance=(
            "Respond with helpful, accurate, and concise information. "
            "Use a polite, neutral, and professional tone."
        ),
        tts_voice="en-US-GuyNeural",
        tts_speed=1.0,
        tone="neutral",
    ),
    "research": ModeSettings(
        name="Research Mode",
        description="Evidence-oriented mode for analysis, comparison and investigation.",
        guidance=(
            "Focus on research, detail, citations, and careful analysis. "
            "When possible, summarize findings with source context and next-step recommendations."
        ),
        tts_voice="en-US-AriaNeural",
        tts_speed=0.95,
        tone="analytical",
    ),
    "dev": ModeSettings(
        name="Dev Mode",
        description="Technical mode for software architecture, coding, and implementation.",
        guidance=(
            "Answer as a software engineer: provide architecture, code examples, and exact commands. "
            "Avoid fluff and prioritize practical, runnable output."
        ),
        tts_voice="en-US-RyanNeural",
        tts_speed=1.1,
        tone="technical",
    ),
    "angry": ModeSettings(
        name="Angry Mode",
        description="High-energy mode for urgent, blunt, no-nonsense communication.",
        guidance=(
            "Use sharp, direct language and call out problems clearly. "
            "Be firm, honest, and focus on what must change immediately."
        ),
        tts_voice="en-US-GuyNeural",
        tts_speed=1.05,
        tone="urgent",
    ),
    "chill": ModeSettings(
        name="Chill Mode",
        description="Relaxed mode for calm, friendly, and patient responses.",
        guidance=(
            "Use a calm tone with supportive language. "
            "Explain concepts clearly and keep the experience comfortable."
        ),
        tts_voice="en-US-AriaNeural",
        tts_speed=0.95,
        tone="relaxed",
    ),
    "mentor": ModeSettings(
        name="Mentor Mode",
        description="Guidance mode with nurturing, fatherly support and clear direction.",
        guidance=(
            "Speak like a trusted mentor: encourage, instruct, and keep the user moving forward. "
            "Give practical next steps and good developer advice."
        ),
        tts_voice="en-US-GuyNeural",
        tts_speed=1.0,
        tone="supportive",
    ),
}


class ModeError(ValueError):
    pass


class ModeManager:
    def __init__(self, default_mode: str = DEFAULT_MODE) -> None:
        self._mode = self._normalize(default_mode)
        if self._mode not in MODE_REGISTRY:
            self._mode = DEFAULT_MODE

    @staticmethod
    def _normalize(mode: str | None) -> str:
        if not mode or not isinstance(mode, str):
            return DEFAULT_MODE
        return mode.strip().lower()

    def set_mode(self, mode: str) -> str:
        normalized = self._normalize(mode)
        if normalized not in MODE_REGISTRY:
            raise ModeError(
                f"Mode '{mode}' is not supported. "
                f"Available modes: {', '.join(self.available_modes())}"
            )
        self._mode = normalized
        return self._mode

    def get_mode(self) -> str:
        return self._mode

    def get_settings(self, mode: str | None = None) -> ModeSettings:
        choice = self._normalize(mode) if mode else self._mode
        if choice not in MODE_REGISTRY:
            raise ModeError(f"Unsupported mode: {choice}")
        return MODE_REGISTRY[choice]

    def get_mode_prompt(self, mode: str | None = None) -> str:
        settings = self.get_settings(mode)
        return (
            f"[MODE: {settings.name}]\n"
            f"{settings.guidance}\n"
            f"Respond with {settings.tone} tone."
        )

    def get_voice_settings(self, mode: str | None = None) -> Dict[str, Any]:
        settings = self.get_settings(mode)
        return {
            "voice": settings.tts_voice,
            "speed": settings.tts_speed,
            "tone": settings.tone,
        }

    def available_modes(self) -> List[str]:
        return list(MODE_REGISTRY.keys())

    def get_mode_metadata(self, mode: str | None = None) -> Dict[str, Any]:
        settings = self.get_settings(mode)
        return {
            "key": self._normalize(mode) if mode else self._mode,
            "name": settings.name,
            "description": settings.description,
            "tone": settings.tone,
            "tts_voice": settings.tts_voice,
            "tts_speed": settings.tts_speed,
        }
