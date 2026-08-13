"""
actions/web_search.py — WEB_SEARCH intent.

Opens a search in the default browser. Kept dependency-free — a real
per-query answer pipeline lives in the gateway; this covers the browser
open intent the brain routes here.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.parse
import webbrowser
from typing import Any, Dict


def web_search(params: Dict[str, Any] | None = None) -> str:
    query = ((params or {}).get("query") or "").strip()
    if not query:
        return "No search query provided."
    url = "https://duckduckgo.com/?q=" + urllib.parse.quote(query)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    ["rundll32", "url.dll,FileProtocolHandler", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:  # noqa: BLE001
            return f"Could not open the browser: {exc}"
    return f"Searched the web for: {query}"


if __name__ == "__main__":  # pragma: no cover
    print(web_search({"query": "hello world"}))
