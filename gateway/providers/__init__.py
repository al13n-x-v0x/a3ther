"""
A3THER Gateway providers.

Each provider wraps one model vendor and normalises its output to the
shared :class:`gateway.providers.base.ChatResult` DTO and the
Ollama-style tool-call format used across the project.

NOTE: third-party SDKs (``openai``, ``google.genai``, ``anthropic``) are
imported lazily *inside* each provider so the package tree imports cleanly
even when optional dependencies are not installed.
"""
from .base import BaseProvider, ChatResult, ProviderError, is_retryable_status
from .openai_provider import OpenAIProvider
from .deepseek_provider import DeepSeekProvider
from .gemini_provider import GeminiProvider
from .anthropic_provider import AnthropicProvider
from .groq_provider import GroqProvider

__all__ = [
    "BaseProvider",
    "ChatResult",
    "ProviderError",
    "is_retryable_status",
    "OpenAIProvider",
    "DeepSeekProvider",
    "GeminiProvider",
    "AnthropicProvider",
    "GroqProvider",
]
