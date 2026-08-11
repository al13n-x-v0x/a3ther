"""
OpenAI provider for the A3THER gateway.

Also usable against *any* OpenAI-compatible endpoint (LM Studio, LocalAI,
vLLM, etc.) by passing a custom ``base_url``.
"""
from __future__ import annotations

import json
from typing import Any

from .base import BaseProvider, ChatResult, ProviderError, is_retryable_status


class OpenAIProvider(BaseProvider):
    """Chat Completions via the official ``openai`` Python SDK."""

    name = "openai"
    display_name = "OpenAI"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = "gpt-4o-mini",
    ):
        super().__init__(api_key=api_key, base_url=base_url, default_model=default_model)

    # ------------------------------------------------------------------ #
    def _get_client(self) -> Any:
        """Lazily create the OpenAI client (SDK imported on first use)."""
        if self._client is None:
            import openai  # lazy: optional dependency

            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = openai.OpenAI(**kwargs)
        return self._client

    # ------------------------------------------------------------------ #
    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int = 90,
        model: str | None = None,
    ) -> ChatResult:
        model_name = self.configure_model(model)
        try:
            client = self._get_client()
            kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
            if tools:
                kwargs["tools"] = self.convert_tools_to_openai(tools)
                kwargs["tool_choice"] = "auto"

            resp = client.chat.completions.create(**kwargs)
        except ImportError as exc:
            raise ProviderError(
                "The 'openai' package is not installed. Run: pip install openai",
                retryable=False,
                provider=self.name,
            ) from exc
        except Exception as exc:  # noqa: BLE001 — mapped to ProviderError below
            raise self._map_error(exc) from exc

        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        msg = choice.message if choice else None

        tool_calls: list[dict] = []
        for tc in (msg.tool_calls or []) if msg else []:
            raw_args = tc.function.arguments or "{}"
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}
            tool_calls.append(
                {"id": tc.id, "function": {"name": tc.function.name, "arguments": args}}
            )

        usage = {}
        if getattr(resp, "usage", None) is not None:
            try:
                usage = resp.usage.model_dump()
            except Exception:
                usage = {}

        return ChatResult(
            content=(msg.content or "") if msg else "",
            tool_calls=tool_calls,
            model=model_name,
            provider=self.name,
            usage=usage,
            finish_reason=choice.finish_reason if choice else "",
            raw=resp,
        )

    # ------------------------------------------------------------------ #
    def _map_error(self, exc: Exception) -> ProviderError:
        """Translate openai SDK exceptions into ProviderError with retry flags."""
        import openai  # lazy

        if isinstance(exc, openai.RateLimitError):
            return ProviderError(
                str(exc), retryable=True, rate_limited=True, status_code=429,
                provider=self.name,
            )
        if isinstance(exc, openai.APITimeoutError):
            return ProviderError(
                str(exc), retryable=True, status_code=504, provider=self.name
            )
        if isinstance(exc, openai.APIConnectionError):
            return ProviderError(
                str(exc), retryable=True, status_code=502, provider=self.name
            )
        if isinstance(exc, openai.APIStatusError):
            status = exc.status_code
            return ProviderError(
                str(exc), retryable=is_retryable_status(status), status_code=status,
                provider=self.name,
            )
        if isinstance(exc, openai.AuthenticationError):
            return ProviderError(
                str(exc), retryable=False, status_code=401, provider=self.name
            )
        return ProviderError(
            str(exc), retryable=True, provider=self.name
        )
