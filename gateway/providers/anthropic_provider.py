"""
Anthropic provider for the A3THER gateway.

Wraps the ``anthropic`` SDK ``messages.create`` endpoint. System prompts
are extracted into the dedicated ``system`` field; tool use blocks are
normalised to the project-wide Ollama-style tool-call format.
"""
from __future__ import annotations

from typing import Any

from .base import BaseProvider, ChatResult, ProviderError, is_retryable_status


class AnthropicProvider(BaseProvider):
    """Anthropic Claude via the official ``anthropic`` Python SDK."""

    name = "anthropic"
    display_name = "Anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = "claude-3-5-haiku-latest",
    ):
        super().__init__(api_key=api_key, base_url=base_url, default_model=default_model)

    # ------------------------------------------------------------------ #
    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # lazy: optional dependency

            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)
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
            import anthropic  # lazy

            client = self._get_client()
            system = "\n\n".join(
                m.get("content", "") for m in (messages or []) if m.get("role") == "system"
            )
            convo = [
                {"role": m["role"], "content": m.get("content", "")}
                for m in (messages or [])
                if m.get("role") in ("user", "assistant")
            ]
            kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": convo,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout": timeout,
            }
            if system:
                kwargs["system"] = system
            else:
                kwargs["system"] = anthropic.NOT_GIVEN

            if tools:
                kwargs["tools"] = self._convert_tools(tools)

            resp = client.messages.create(**kwargs)
        except ImportError as exc:
            raise ProviderError(
                "The 'anthropic' package is not installed. Run: pip install anthropic",
                retryable=False,
                provider=self.name,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        parts = list(getattr(resp, "content", None) or [])
        text = "".join(p.text for p in parts if getattr(p, "type", "") == "text")
        tool_calls = [
            {
                "id": p.id,
                "function": {"name": p.name, "arguments": dict(p.input or {})},
            }
            for p in parts
            if getattr(p, "type", "") == "tool_use"
        ]

        usage = {}
        if getattr(resp, "usage", None) is not None:
            try:
                usage = resp.usage.model_dump()
            except Exception:
                usage = {}

        return ChatResult(
            content=text.strip(),
            tool_calls=tool_calls,
            model=model_name,
            provider=self.name,
            usage=usage,
            finish_reason=getattr(resp, "stop_reason", "") or "",
            raw=resp,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _convert_tools(tools: list[dict] | None) -> list[dict]:
        """Ollama-style tools -> Anthropic tool schema."""
        converted: list[dict] = []
        for tool in tools or []:
            fn = tool.get("function") or tool
            converted.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return converted

    # ------------------------------------------------------------------ #
    def _map_error(self, exc: Exception) -> ProviderError:
        import anthropic  # lazy

        if isinstance(exc, anthropic.RateLimitError):
            return ProviderError(
                str(exc), retryable=True, rate_limited=True, status_code=429,
                provider=self.name,
            )
        if isinstance(exc, anthropic.APITimeoutError):
            return ProviderError(
                str(exc), retryable=True, status_code=504, provider=self.name
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderError(
                str(exc), retryable=True, status_code=502, provider=self.name
            )
        if isinstance(exc, anthropic.APIStatusError):
            status = exc.status_code
            return ProviderError(
                str(exc), retryable=is_retryable_status(status), status_code=status,
                provider=self.name,
            )
        if isinstance(exc, anthropic.AuthenticationError):
            return ProviderError(
                str(exc), retryable=False, status_code=401, provider=self.name
            )
        return ProviderError(str(exc), retryable=True, provider=self.name)
