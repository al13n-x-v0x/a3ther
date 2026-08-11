"""
DeepSeek provider for the A3THER gateway.

DeepSeek exposes an OpenAI-compatible API, so this provider subclasses
:class:`~gateway.providers.openai_provider.OpenAIProvider` and only swaps
the default base URL, model, and key source.
"""
from __future__ import annotations

from .openai_provider import OpenAIProvider

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek chat (deepseek-chat / deepseek-reasoner)."""

    name = "deepseek"
    display_name = "DeepSeek"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = DEEPSEEK_BASE_URL,
        default_model: str = "deepseek-chat",
    ):
        super().__init__(api_key=api_key, base_url=base_url, default_model=default_model)
