"""
actions/youtube_video.py — YOUTUBE intent.

Opens YouTube for a play/search request in the default browser.
"""

from __future__ import annotations

import urllib.parse
import webbrowser
from typing import Any, Dict


def youtube_video(params: Dict[str, Any] | None = None) -> str:
    params = params or {}
    query = (params.get("query") or "").strip()
    action = (params.get("action") or "play").lower()
    if not query:
        return "No video/query provided — tell me what to play."
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        return f"Could not open YouTube for '{query}'."
    return f"Playing '{query}' on YouTube…"


if __name__ == "__main__":  # pragma: no cover
    print(youtube_video({"action": "play", "query": "lofi beats"}))
