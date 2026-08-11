"""
Gateway provider base: shared DTOs, error taxonomy and the Provider ABC.

Design goals
------------
1. One result type (``ChatResult``) for every vendor so callers never
   branch on provider-specific response shapes.
2. One error type (``ProviderError``) carrying retry metadata so the
   router can decide *natively* whether to fall back (429/5xx/timeout)
   or surface the failure.
3. Tool calls normalised to the Ollama-style format the project already
   uses: ``[{"id": str, "function": {"name": str, "arguments": dict}}]``
"""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from typing import Any, Optional


def is_retryable_status(status_code: int | None) -> bool:
    """429 (rate limit) and all 5xx are safe to retry on another provider."""
    if status_code is None:
        return False
    return status_code == 429 or status_code >= 500


@dataclass
class ChatResult:
    """Unified, provider-agnostic completion result."""

    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    usage: dict = field(default_factory=dict)
    finish_reason: str = ""
    raw: Any = None

    def text(self) -> str:
        return self.content


class ProviderError(RuntimeError):
    """Raised when a provider call fails; carries fallback metadata.

    Attributes
    ----------
    retryable:
        True when the failure is transient (rate limit, 5xx, timeout)
        and the router should try the next provider.
    rate_limited:
        True for 429 / quota-exhaustion errors.
    status_code:
        HTTP/API status code when available.
    provider:
        Name of the provider that raised the error.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        rate_limited: bool = False,
        status_code: int | None = None,
        provider: str = "",
    ):
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited
        self.status_code = status_code
        self.provider = provider


class BaseProvider(abc.ABC):
    """Abstract LLM provider.

    Subclasses implement :meth:`complete` and provide ``name`` /
    ``display_name``. All third-party SDK imports must happen lazily
    inside methods so the package tree imports with the stdlib only.
    """

    name: str = "base"
    display_name: str = "Base"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = "",
    ):
        self.api_key = api_key or ""
        self.base_url = base_url
        self.default_model = default_model
        self._client: Any = None

    # ------------------------------------------------------------------ #
    # Lifecycle / capability helpers
    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        """True when the provider is usable (an API key is configured)."""
        return bool(self.api_key)

    def configure_model(self, model: str | None) -> str:
        """Resolve an optional per-call model against the default."""
        return model or self.default_model

    # ------------------------------------------------------------------ #
    # Shared normalisation helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def normalize_tool_calls(tool_calls: list[dict]) -> list[dict]:
        """Normalise arbitrary tool-call dicts to the Ollama-style format.

        Handles string-encoded JSON arguments (OpenAI/Anthropic style)
        and already-parsed dicts.
        """
        out: list[dict] = []
        for tc in tool_calls or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            out.append(
                {
                    "id": tc.get("id", ""),
                    "function": {"name": fn.get("name", ""), "arguments": args or {}},
                }
            )
        return out

    @staticmethod
    def convert_tools_to_openai(tools: list[dict] | None) -> list[dict]:
        """Convert Ollama-style tools to OpenAI ``tools`` schema.

        Accepts either ``{"type": "function", "function": {...}}`` or a
        bare ``{"name": ..., "description": ..., "parameters": ...}``.
        """
        converted: list[dict] = []
        for tool in tools or []:
            fn = tool.get("function") or tool
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters")
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return converted

    # ------------------------------------------------------------------ #
    # Core API
    # ------------------------------------------------------------------ #
    @abc.abstractmethod
    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int = 90,
        model: str | None = None,
    ) -> ChatResult:
        """Run a non-streaming chat completion and return a ChatResult.

        Implementations MUST wrap vendor errors in :class:`ProviderError`
        with correct ``retryable`` / ``rate_limited`` flags.
        """
        raise NotImplementedError
