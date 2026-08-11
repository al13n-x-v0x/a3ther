"""
Groq provider for the A3THER gateway.

Groq serves open-weight models (Llama, DeepSeek, Qwen, …) at very high
speed through an OpenAI-compatible API, so this provider subclasses
:class:`~gateway.providers.openai_provider.OpenAIProvider` and only
overrides the endpoint + defaults.

Free tier: https://console.groq.com — one API key unlocks a fast
Llama/DeepSeek chain that is ideal as a low-latency voice brain.
"""
from __future__ import annotations

from .openai_provider import OpenAIProvider

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"


class GroqProvider(OpenAIProvider):
    """Chat Completions via Groq's OpenAI-compatible endpoint."""

    name = "groq"
    display_name = "Groq"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = GROQ_DEFAULT_MODEL,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url or GROQ_BASE_URL,
            default_model=default_model,
        )
