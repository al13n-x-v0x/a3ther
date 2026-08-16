"""
A3THER LLM Gateway router.

The :class:`LLMGateway` owns provider construction, the fallback priority
chain, per-provider circuit breakers, and the unified ``complete`` /
``complete_text`` entry points used by the brain, the Freaky-Fix autopilot
and the extensions API.

Config schema (``config/api_keys.json``)
----------------------------------------
.. code-block:: json

    {
      "gemini_api_key": "legacy-fallback-only",
      "llm_routing": {
        "priority": ["openai", "deepseek", "gemini", "anthropic"],
        "fallback_enabled": true,
        "breaker_threshold": 3,
        "breaker_cooldown_seconds": 30,
        "request_timeout_seconds": 90
      },
      "llm_providers": {
        "openai_api_key": "", "openai_base_url": "", "openai_model": "gpt-4o-mini",
        "deepseek_api_key": "", "deepseek_model": "deepseek-chat",
        "gemini_api_key": "", "gemini_model": "gemini-3-flash-preview",
        "groq_api_key": "", "groq_model": "llama-3.3-70b-versatile",
        "anthropic_api_key": "", "anthropic_model": "claude-3-5-haiku-latest"
      }
    }

Keys are resolved **environment first** (``A3THER_OPENAI_API_KEY`` etc.,
including a root ``.env`` file) and fall back to the JSON above. The
legacy top-level ``gemini_api_key`` remains a last-resort fallback for
Gemini.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from config import get_config, get_env, load_env_file

from .providers import (
    AnthropicProvider,
    BaseProvider,
    ChatResult,
    DeepSeekProvider,
    GeminiProvider,
    GroqProvider,
    OpenAIProvider,
    ProviderError,
)

DEFAULT_PRIORITY = ["openai", "deepseek", "gemini", "groq", "anthropic"]


class AllProvidersFailed(RuntimeError):
    """Raised when every provider in the fallback chain fails."""


class CircuitBreaker:
    """Simple per-provider circuit breaker.

    After ``failure_threshold`` consecutive failures the breaker opens for
    ``cooldown_seconds``; the router skips the provider while it is open
    and any success resets the counter.
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._open_until = time.time() + self.cooldown_seconds

    def is_open(self) -> bool:
        with self._lock:
            if self._open_until and time.time() >= self._open_until:
                self._open_until = 0.0
                self._failures = 0
            return self._open_until > 0.0


class LLMGateway:
    """Agnostic multi-model router with native provider fallback."""

    def __init__(self, config: dict | None = None):
        load_env_file()
        self.config = config or get_config()
        routing = self.config.get("llm_routing") or {}
        providers_cfg = self.config.get("llm_providers") or {}

        self.fallback_enabled = bool(routing.get("fallback_enabled", True))
        self.request_timeout = int(routing.get("request_timeout_seconds", 90) or 90)
        self.breaker_threshold = int(routing.get("breaker_threshold", 3) or 3)
        self.breaker_cooldown = float(routing.get("breaker_cooldown_seconds", 30) or 30)
        # Model shuffling: when enabled, each request starts at a different
        # provider (round-robin) so no single model carries the load and
        # responses vary across the configured models.
        self.shuffle_enabled = bool(routing.get("shuffle_models", False))
        self._shuffle_index = 0

        self.providers: dict[str, BaseProvider] = {}
        self.order: list[str] = []
        self.breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()

        self._build_providers(providers_cfg, routing.get("priority"))

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _resolve_key(self, env_name: str, section_key: str, legacy_key: str | None = None) -> str | None:
        value = get_env(env_name)
        if not value:
            section = self.config.get("llm_providers") or {}
            value = section.get(section_key)
        if not value and legacy_key:
            value = self.config.get(legacy_key)
        return value or None

    def _build_providers(self, providers_cfg: dict, priority: Any) -> None:
        requested = priority or DEFAULT_PRIORITY
        self.order = [name for name in requested if name in DEFAULT_PRIORITY] or list(DEFAULT_PRIORITY)
        # De-duplicate while preserving order.
        seen: set[str] = set()
        self.order = [n for n in self.order if not (n in seen or seen.add(n))]

        available: dict[str, BaseProvider] = {}

        openai_key = self._resolve_key("A3THER_OPENAI_API_KEY", "openai_api_key")
        if openai_key:
            available["openai"] = OpenAIProvider(
                api_key=openai_key,
                base_url=providers_cfg.get("openai_base_url") or None,
                default_model=providers_cfg.get("openai_model") or "gpt-4o-mini",
            )

        deepseek_key = self._resolve_key("A3THER_DEEPSEEK_API_KEY", "deepseek_api_key")
        if deepseek_key:
            available["deepseek"] = DeepSeekProvider(
                api_key=deepseek_key,
                default_model=providers_cfg.get("deepseek_model") or "deepseek-chat",
            )

        gemini_key = self._resolve_key("A3THER_GEMINI_API_KEY", "gemini_api_key", legacy_key="gemini_api_key")
        if gemini_key:
            available["gemini"] = GeminiProvider(
                api_key=gemini_key,
                base_url=providers_cfg.get("gemini_base_url") or None,
                default_model=providers_cfg.get("gemini_model") or "gemini-3-flash-preview",
            )

        groq_key = self._resolve_key("A3THER_GROQ_API_KEY", "groq_api_key")
        if groq_key:
            available["groq"] = GroqProvider(
                api_key=groq_key,
                base_url=providers_cfg.get("groq_base_url") or None,
                default_model=providers_cfg.get("groq_model") or "llama-3.3-70b-versatile",
            )

        anthropic_key = self._resolve_key("A3THER_ANTHROPIC_API_KEY", "anthropic_api_key")
        if anthropic_key:
            available["anthropic"] = AnthropicProvider(
                api_key=anthropic_key,
                default_model=providers_cfg.get("anthropic_model") or "claude-3-5-haiku-latest",
            )

        self.providers = {name: available[name] for name in self.order if name in available}
        self.breakers = {
            name: CircuitBreaker(self.breaker_threshold, self.breaker_cooldown)
            for name in self.order
        }

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def any_available(self) -> bool:
        """True when at least one provider has credentials configured."""
        return any(p.available() for p in self.providers.values())

    def best_provider(self) -> str | None:
        """First available (and not open-breakered) provider in priority order."""
        for name in self.order:
            provider = self.providers.get(name)
            if provider and provider.available() and not self.breakers[name].is_open():
                return name
        for name in self.order:
            if name in self.providers and self.providers[name].available():
                return name
        return None

    def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int | None = None,
        preference: str | None = None,
    ) -> ChatResult:
        """Run a chat completion, falling back across providers.

        ``preference`` names a provider to try first (e.g. the strongest
        available model for autonomous repair); the configured priority
        chain is used otherwise.
        """
        timeout = timeout or self.request_timeout
        candidates = self._candidate_order(preference)

        last_error: Exception | None = None
        for name in candidates:
            provider = self.providers.get(name)
            if not provider or not provider.available():
                continue
            if self.breakers[name].is_open():
                continue

            try:
                result = provider.complete(
                    messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                result.provider = name
                self.breakers[name].record_success()
                return result
            except ProviderError as exc:
                last_error = exc
                self.breakers[name].record_failure()
                if exc.rate_limited and self.fallback_enabled:
                    time.sleep(1.0)  # brief backoff before next provider
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.breakers[name].record_failure()

            if not self.fallback_enabled:
                break

        if last_error is not None:
            raise AllProvidersFailed(
                f"All LLM providers failed. Last error: {last_error}"
            ) from last_error
        raise AllProvidersFailed("No LLM providers are configured. Set A3THER_*_API_KEY.")

    def complete_text(
        self,
        prompt: str,
        system: str | None = None,
        preference: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        timeout: int | None = None,
    ) -> str:
        """Convenience text-only completion (no tools)."""
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            preference=preference,
        ).text()

    # ------------------------------------------------------------------ #
    # Introspection / status
    # ------------------------------------------------------------------ #
    def get_status(self) -> list[dict]:
        """Provider list for the extensions API / plugins dashboard."""
        out: list[dict] = []
        for index, name in enumerate(self.order):
            provider = self.providers.get(name)
            out.append(
                {
                    "name": name,
                    "display_name": provider.display_name if provider else name,
                    "model": provider.default_model if provider else None,
                    "configured": bool(provider and provider.available()),
                    "breaker_open": self.breakers[name].is_open() if name in self.breakers else False,
                    "order": index,
                }
            )
        return out

    # ------------------------------------------------------------------ #
    def _candidate_order(self, preference: str | None) -> list[str]:
        order = self.order
        if self.shuffle_enabled:
            # Round-robin rotation: each call starts from the next provider,
            # so the configured models get used in turn instead of the first
            # one always winning. Breakers/availability still decide below.
            with self._lock:
                available = [n for n in order if n in self.providers]
                if len(available) > 1:
                    index = self._shuffle_index % len(available)
                    self._shuffle_index += 1
                    order = available[index:] + available[:index]
        if preference and preference in order:
            order = [preference] + [n for n in order if n != preference]
        return order


# ------------------------------------------------------------------------- #
# Module-level singleton
# ------------------------------------------------------------------------- #
_GATEWAY: LLMGateway | None = None
_GATEWAY_LOCK = threading.Lock()


def get_gateway() -> LLMGateway:
    """Return the process-wide gateway singleton (lazily built)."""
    global _GATEWAY
    if _GATEWAY is None:
        with _GATEWAY_LOCK:
            if _GATEWAY is None:
                _GATEWAY = LLMGateway()
    return _GATEWAY


def reset_gateway() -> None:
    """Drop the singleton (useful for tests / config reloads)."""
    global _GATEWAY
    with _GATEWAY_LOCK:
        _GATEWAY = None
