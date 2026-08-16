"""
Ollama integration helpers (lightweight wrapper).

This module is an optional, separate helper that uses the existing
`core.llm_client` functions (ensure_ollama_running, check_model_available,
call_llm_text) to provide convenience utilities for Ollama-specific tasks.

It is intentionally non-invasive: it does not replace or remove any
existing functionality in `core.llm_client` — only calls into it.
"""
from typing import Optional
import logging

import core.llm_client as llm_client


def ensure_running(timeout: int = 15) -> bool:
    """Ensure Ollama server is running. Returns True if reachable."""
    try:
        return llm_client.ensure_ollama_running(timeout=timeout)
    except Exception as e:
        logging.warning(f"[Ollama] ensure_running failed: {e}")
        return False


def check_model(model: Optional[str] = None, log: Optional[callable] = None) -> bool:
    """Check whether the configured model (or provided) is available in Ollama.

    Returns True if model is present or provider is non-Ollama.
    """
    try:
        if model is None:
            _, model = llm_client.get_llm_settings()
        return llm_client.check_model_available(log=log)
    except Exception as e:
        logging.warning(f"[Ollama] check_model failed: {e}")
        return False


def pull_model(model: str) -> bool:
    """Attempt to pull an Ollama model with system `ollama pull <model>`.

    This is a convenience; it shells out to `ollama pull` and returns True on success.
    """
    import subprocess
    import sys

    try:
        subprocess.run(["ollama", "pull", model], check=True)
        return True
    except Exception as e:
        logging.warning(f"[Ollama] pull_model failed: {e}")
        return False


def call_text(prompt: str, system: Optional[str] = None, timeout: int = 120) -> str:
    """Call the configured LLM with a text-only prompt via core.llm_client.call_llm_text."""
    return llm_client.call_llm_text(prompt=prompt, system=system, timeout=timeout)
