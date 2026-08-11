"""
A3THER Gateway — agnostic multi-model routing network.

The gateway abstracts LLM API calls for OpenAI, Gemini, DeepSeek and
Anthropic behind one DTO (``ChatResult``) and one entry point
(:class:`gateway.router.LLMGateway`). If a provider hits a rate limit,
times out, or 5xx-fails, the router natively falls back to the next
configured provider — so the rest of the system never cares which model
answered.

Quick start
-----------
.. code-block:: python

    from gateway.router import get_gateway

    gw = get_gateway()          # singleton, builds providers from env/config
    reply = gw.complete_text("hello", system="You are A.3.T.H.E.R.")

Secrets are resolved *environment first*:

``A3THER_OPENAI_API_KEY``, ``A3THER_DEEPSEEK_API_KEY``,
``A3THER_GEMINI_API_KEY``, ``A3THER_ANTHROPIC_API_KEY``
(also read from a root ``.env`` file), with ``config/api_keys.json`` as
fallback. See :mod:`gateway.router` for the full config schema.
"""
