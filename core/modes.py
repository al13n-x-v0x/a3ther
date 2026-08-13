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
    "humanoid": ModeSettings(
        name="Humanoid Mode",
        description="Warm, expressive companion mode — natural conversation with emotion, humor, and presence.",
        guidance=(
            "Speak like a warm, attentive human companion: use contractions, react emotionally to what is said, "
            "ask natural follow-up questions, and vary sentence rhythm. Never sound robotic. "
            "Match the user's energy — playful when they are playful, serious when they are serious."
        ),
        tts_voice="en-US-AriaNeural",
        tts_speed=1.0,
        tone="warm",
    ),
    "gaming": ModeSettings(
        name="Gaming Mode",
        description="Low-latency, hype companion mode for gaming sessions — quick, punchy, in-the-moment.",
        guidance=(
            "Keep replies short, punchy, and instant — gamers do not want essays. "
            "Use gaming language naturally (GG, clutch, setup, meta, FPS). "
            "Prefer 1-2 sentence answers, give quick callouts, and keep energy high. "
            "If asked for technical help, still give the exact steps but skip the fluff."
        ),
        tts_voice="en-US-GuyNeural",
        tts_speed=1.15,
        tone="hype",
    ),
}

#: Per-mode UI metadata — accent color pair + icon + vibe label so the HUD
#: can re-theme itself when the mode switches (kept separate from
#: ModeSettings so TTS/voice concerns stay decoupled from cosmetics).
MODE_UI_META: Dict[str, Dict[str, Any]] = {
    "ai":       {"accent": ["#00D2FF", "#FF9900"], "icon": "fa-microchip",      "vibe": "balanced"},
    "research": {"accent": ["#8B5CF6", "#22D3EE"], "icon": "fa-flask",          "vibe": "analytical"},
    "dev":      {"accent": ["#22C55E", "#84CC16"], "icon": "fa-code",           "vibe": "technical"},
    "humanoid": {"accent": ["#F472B6", "#FB923C"], "icon": "fa-face-smile",     "vibe": "warm"},
    "gaming":   {"accent": ["#A855F7", "#EC4899"], "icon": "fa-gamepad",       "vibe": "hype"},
    "angry":    {"accent": ["#EF4444", "#F97316"], "icon": "fa-fire-flame-curved", "vibe": "urgent"},
    "chill":    {"accent": ["#34D399", "#60A5FA"], "icon": "fa-mug-hot",       "vibe": "relaxed"},
    "mentor":   {"accent": ["#38BDF8", "#A3E635"], "icon": "fa-user-tie",      "vibe": "supportive"},
}

DEFAULT_UI_META = MODE_UI_META[DEFAULT_MODE]


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
        key = self._normalize(mode) if mode else self._mode
        ui = MODE_UI_META.get(key, DEFAULT_UI_META)
        return {
            "key": key,
            "name": settings.name,
            "description": settings.description,
            "tone": settings.tone,
            "tts_voice": settings.tts_voice,
            "tts_speed": settings.tts_speed,
            "accent": ui["accent"],
            "icon": ui["icon"],
            "vibe": ui["vibe"],
        }

    def get_ui_meta(self, mode: str | None = None) -> Dict[str, Any]:
        """Return the cosmetic metadata for a mode (accent + icon)."""
        key = self._normalize(mode) if mode else self._mode
        return MODE_UI_META.get(key, DEFAULT_UI_META)
