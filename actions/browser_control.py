"""
actions/browser_control.py — BROWSER intent.

Lightweight browser helpers: open a URL, search, or control tab focus.
Deliberately dependency-free at import time (playwright is optional and
loaded lazily); the brain imports this module at startup, so it must never
raise on import.
"""

from __future__ import annotations

import urllib.parse
import webbrowser
from typing import Any, Dict


def open_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "No URL provided."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        webbrowser.open(url)
        return f"Opened {url}"
    except Exception as exc:  # noqa: BLE001
        return f"Could not open {url}: {exc}"


def search(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "No search query provided."
    url = "https://duckduckgo.com/?q=" + urllib.parse.quote(query)
    try:
        webbrowser.open(url)
        return f"Searched for: {query}"
    except Exception as exc:  # noqa: BLE001
        return f"Could not search: {exc}"


def browser_control(params: Dict[str, Any] | None = None) -> str:
    params = params or {}
    action = (params.get("action") or "open").lower()
    if action == "search":
        return search(params.get("query") or params.get("text") or "")
    return open_url(params.get("url") or params.get("text") or params.get("site") or "")


if __name__ == "__main__":  # pragma: no cover
    print(browser_control({"action": "open", "url": "example.com"}))
