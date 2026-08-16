"""
Gemini provider for the A3THER gateway.

Uses the ``google-genai`` SDK (already a project dependency) and maps
OpenAI-style messages / Ollama-style tools onto Gemini ``contents`` and
function declarations. Tool calls come back as ``functionCall`` parts and
are normalised to the project-wide Ollama-style format.
"""
from __future__ import annotations

from typing import Any

from .base import BaseProvider, ChatResult, ProviderError, is_retryable_status


class GeminiProvider(BaseProvider):
    """Google Gemini via ``google.genai``."""

    name = "gemini"
    display_name = "Gemini"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = "gemini-3-flash-preview",
    ):
        super().__init__(api_key=api_key, base_url=base_url, default_model=default_model)

    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        """True when a plausible Gemini API key is configured.

        Google issues two key shapes: classic ``AIza…`` keys and the newer
        ``AQ.``-prefixed keys. Both are accepted; only obvious non-keys
        (OAuth tokens, placeholders, too-short strings) are rejected so the
        router never wastes a request on them.
        """
        try:
            from core.first_run import is_plausible_gemini_key

            return bool(self.api_key) and is_plausible_gemini_key(str(self.api_key))
        except Exception:  # noqa: BLE001 — never let validation block routing
            return bool(self.api_key)

    # ------------------------------------------------------------------ #
    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai  # lazy: optional dependency

            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["http_options"] = genai.types.HttpOptions(base_url=self.base_url)
            self._client = genai.Client(**kwargs)
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
            from google import genai  # lazy

            client = self._get_client()
            contents = self._convert_messages(messages)

            config_kwargs: dict[str, Any] = {"temperature": temperature}
            if max_tokens:
                config_kwargs["max_output_tokens"] = max_tokens
            gen_tools = self._convert_tools(tools)
            if gen_tools:
                config_kwargs["tools"] = gen_tools
                # We execute tool calls ourselves (gateway owns the loop).
                config_kwargs["automatic_function_calling"] = {"disable": True}

            # Bound the request so a misbehaving provider cannot hang a chat
            # turn for the whole request timeout. The options API changed
            # across google-genai versions: older SDKs used
            # ``RequestOptions(timeout=...)``; newer ones (>= 1.0) moved the
            # timeout into ``HttpOptions``. Probe at runtime and fall back to
            # a plain call if neither is accepted.
            try:
                call_kwargs: dict[str, Any] = {
                    "model": model_name,
                    "contents": contents,
                    "config": genai.types.GenerateContentConfig(**config_kwargs),
                }
                req_opts = getattr(genai.types, "RequestOptions", None)
                if req_opts is not None:
                    call_kwargs["request_options"] = req_opts(timeout=timeout * 1000)
                else:
                    call_kwargs["http_options"] = genai.types.HttpOptions(timeout=timeout * 1000)
                resp = client.models.generate_content(**call_kwargs)
            except (TypeError, AttributeError):
                resp = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=genai.types.GenerateContentConfig(**config_kwargs),
                )
        except ImportError as exc:
            raise ProviderError(
                "The 'google-genai' package is not installed.",
                retryable=False,
                provider=self.name,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        return self._parse_response(resp, model_name)

    # ------------------------------------------------------------------ #
    # Message / tool conversion
    # ------------------------------------------------------------------ #
    @staticmethod
    def _convert_messages(messages: list[dict]) -> list[dict]:
        """Map OpenAI-style messages onto Gemini ``contents``.

        System prompts are prepended as a user instruction part (Gemini
        has no native system role; this matches the project's existing
        prompt-style usage). Assistant tool_calls map to ``functionCall``
        parts and tool results to ``functionResponse`` parts.
        """
        contents: list[dict] = []
        last_model_fn_names: list[str] = []

        for m in messages or []:
            role = m.get("role", "user")
            content = m.get("content") or ""

            if role == "system":
                if content:
                    contents.append(
                        {
                            "role": "user",
                            "parts": [{"text": f"[SYSTEM INSTRUCTIONS]\n{content}"}],
                        }
                    )
                continue

            if role == "assistant":
                parts: list[dict] = []
                if content:
                    parts.append({"text": content})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    fname = fn.get("name", "")
                    last_model_fn_names.append(fname)
                    parts.append(
                        {
                            "functionCall": {
                                "name": fname,
                                "args": fn.get("arguments") or {},
                            }
                        }
                    )
                contents.append({"role": "model", "parts": parts})
                continue

            if role == "tool":
                # Attach each tool result to the matching functionCall.
                # Without a name we reuse the most recent call name.
                name = (m.get("name") or "").strip() or (
                    last_model_fn_names.pop() if last_model_fn_names else "unknown"
                )
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": {"result": content},
                                }
                            }
                        ],
                    }
                )
                continue

            # user
            contents.append({"role": "user", "parts": [{"text": content or ""}]})

        return contents

    @staticmethod
    def _convert_tools(tools: list[dict] | None) -> list[dict]:
        """Ollama-style tools -> Gemini FunctionDeclaration list."""
        converted: list[dict] = []
        for tool in tools or []:
            fn = tool.get("function") or tool
            converted.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return converted

    # ------------------------------------------------------------------ #
    # Response parsing
    # ------------------------------------------------------------------ #
    def _parse_response(self, resp: Any, model_name: str) -> ChatResult:
        text_parts: list[str] = []
        tool_calls: list[dict] = []

        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            parts = getattr(candidates[0].content, "parts", []) or []
            for part in parts:
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    name = fc.name
                    args = fc.args
                    if hasattr(args, "items"):  # pydantic/protobuf dict-like
                        try:
                            args = dict(args.items())
                        except Exception:
                            args = {}
                    elif not isinstance(args, dict):
                        try:
                            args = dict(args)
                        except Exception:
                            args = {"value": str(args)}
                    tool_calls.append(
                        {"id": "", "function": {"name": name, "arguments": args or {}}}
                    )

        usage = {}
        try:
            usage = resp.usage_metadata.model_dump()
        except Exception:
            pass

        return ChatResult(
            content="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            model=model_name,
            provider=self.name,
            usage=usage,
            finish_reason="tool_calls" if tool_calls else "stop",
            raw=resp,
        )

    # ------------------------------------------------------------------ #
    def _map_error(self, exc: Exception) -> ProviderError:
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            if code == 429:
                return ProviderError(
                    str(exc), retryable=True, rate_limited=True, status_code=429,
                    provider=self.name,
                )
            if code >= 500:
                return ProviderError(
                    str(exc), retryable=True, status_code=code, provider=self.name
                )
        msg = str(exc).lower()
        if any(k in msg for k in ("429", "quota", "resource_exhausted", "rate")):
            return ProviderError(
                str(exc), retryable=True, rate_limited=True, status_code=429,
                provider=self.name,
            )
        if any(k in msg for k in ("unavailable", "timeout", "deadline")):
            return ProviderError(
                str(exc), retryable=True, status_code=503, provider=self.name
            )
        if any(k in msg for k in ("permission", "unauthorized", "api key")):
            return ProviderError(
                str(exc), retryable=False, status_code=401, provider=self.name
            )
        return ProviderError(str(exc), retryable=True, provider=self.name)
