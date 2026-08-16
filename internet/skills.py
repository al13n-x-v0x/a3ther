"""
internet/skills.py — lets A3THER browse the internet and "gain skills".

Two capabilities:

- :func:`search_web` — a dependency-free web search over DuckDuckGo's
  HTML endpoint (no API key required). Returns ranked results with titles,
  URLs and snippets.
- :func:`learn` — the skill-up path: search the web for a topic, pull the
  top pages' readable text, and ask the LLM gateway to write a concise
  research brief. The brain can then answer from *fresh* knowledge instead
  of its training cutoff.

Both degrade honestly: no internet → clean error; no LLM key → the raw
search results are returned so the user still gets useful links.
"""
from __future__ import annotations

import html
import logging
import re
import urllib.parse
import urllib.request

LOGGER = logging.getLogger("a3ther.internet")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 A3THER/1.0"
)
_DDG_HTML = "https://html.duckduckgo.com/html/"
_READ_TIMEOUT = 15


# --------------------------------------------------------------------------- #
# Search (no API key)
# --------------------------------------------------------------------------- #
def search_web(query: str, max_results: int = 6) -> list[dict]:
    """Ranked web results via DuckDuckGo's HTML endpoint.

    Returns ``[{title, url, snippet}]``. Raises ``RuntimeError`` with a
    clear message when the network is unreachable or the search fails.
    """
    query = (query or "").strip()
    if not query:
        return []
    params = urllib.parse.urlencode({"q": query})
    request = urllib.request.Request(
        f"{_DDG_HTML}?{params}",
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=_READ_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Web search unreachable ({type(exc).__name__}: {exc}) — check internet."
        ) from exc

    results: list[dict] = []
    # Each result block is a div whose class list contains result__body
    # (e.g. class="links_main links_deep result__body") holding a
    # result__a link + result__snippet. Parse with regexes.
    blocks = re.split(r'result__body"', raw)[1:]
    for block in blocks:
        if len(results) >= max_results:
            break
        link = re.search(r'class="result__a"[^>]*href="([^"]+)"', block)
        if not link:
            continue
        url = html.unescape(link.group(1))
        # DuckDuckGo wraps real URLs in a redirect param.
        m = re.search(r"[?&]uddg=([^&]+)", url)
        if m:
            url = urllib.parse.unquote(m.group(1))
        title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        title = re.sub(r"<[^>]+>", "", title_m.group(1)) if title_m else query
        snip_m = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet = re.sub(r"<[^>]+>", "", snip_m.group(1)) if snip_m else ""
        results.append({
            "title": html.unescape(title).strip()[:160],
            "url": url.strip()[:500],
            "snippet": html.unescape(snippet).strip()[:400],
        })
    return results


def _fetch_readable(url: str, max_chars: int = 6000) -> str:
    """Fetch a page and strip tags to a plain-text excerpt (best effort)."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=_READ_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return f"(could not fetch {url}: {type(exc).__name__})"
    raw = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:max_chars]


# --------------------------------------------------------------------------- #
# Learn — search + summarize into a skill brief
# --------------------------------------------------------------------------- #
def learn(topic: str, max_results: int = 5, timeout: int = 60) -> dict:
    """Research ``topic`` and return a concise, sourced brief.

    Uses the LLM gateway to summarise the top search results (falls back
    to the raw results when no provider is configured). The returned
    ``brief`` text can be fed straight back into the system prompt.
    """
    topic = (topic or "").strip()
    if not topic:
        return {"ok": False, "error": "topic cannot be empty"}
    try:
        results = search_web(topic, max_results=max_results)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    if not results:
        return {"ok": False, "error": f"no results found for '{topic}'"}

    try:
        from gateway.router import AllProvidersFailed, get_gateway

        gateway = get_gateway()
        if not gateway.any_available():
            return {
                "ok": True,
                "brief": "",
                "results": results,
                "note": "No LLM provider configured — showing raw search results.",
            }
        context = "\n\n".join(
            f"[{i + 1}] {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(results)
        )
        system = (
            "You are A3THER's research engine. Read the web results below and write a "
            "tight, well-structured brief (150-220 words) on the topic: key facts, "
            "actionable takeaways, and sources. No filler."
        )
        brief = gateway.complete_text(
            f"TOPIC: {topic}\n\nWEB RESULTS:\n{context}",
            system=system,
            max_tokens=500,
            timeout=timeout,
        )
        return {"ok": True, "brief": brief.strip(), "results": results, "topic": topic}
    except AllProvidersFailed as exc:
        return {
            "ok": True,
            "brief": "",
            "results": results,
            "note": f"LLM unavailable ({exc}) — showing raw search results.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": True,
            "brief": "",
            "results": results,
            "note": f"LLM summary failed ({type(exc).__name__}) — showing raw results.",
        }


# --------------------------------------------------------------------------- #
def search_and_fetch(query: str, max_pages: int = 3) -> dict:
    """Search + pull readable text from the top pages (for deep dives)."""
    try:
        results = search_web(query, max_results=max_pages)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    pages = []
    for r in results:
        pages.append({"url": r["url"], "title": r["title"], "text": _fetch_readable(r["url"])})
    return {"ok": True, "results": results, "pages": pages}
