"""
generator.py — turn a description into a live 3D website.

Pipeline
--------
1. Pick a theme (neon / glass / hologram).
2. If the gateway has an available provider, ask the LLM for a complete
   single-file HTML page (Three.js + the theme's CSS vars + a section
   layout), and run a strict validity check (must contain ``<html`` and
   ``<body``); on any failure fall back to the template.
3. The fallback template assembles a themed page with the description
   turned into title/sections, always including the theme's Three.js scene.
4. Write to ``Output/websites/<name>/index.html``.
"""
from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path

from config import base_dir

from .themes import DEFAULT_THEME, THEMES

LOGGER = logging.getLogger("a3ther.website")

OUTPUT_DIR = base_dir() / "Output" / "websites"


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", (name or "site").strip().lower())
    return cleaned or "site"


def _template_page(title: str, theme_name: str, description: str) -> str:
    theme = THEMES.get(theme_name, THEMES[DEFAULT_THEME])
    # User inputs are escaped — the template is trusted, the inputs are not.
    title_html = html.escape((title or "My 3D Site").title())
    description_html = html.escape(description or "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{title_html}</title>
{theme["css"]}
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{color:var(--fg);font-family:'Segoe UI',system-ui,sans-serif;{theme["background"]}overflow-x:hidden}}
#bg{{position:fixed;inset:0;z-index:-1}}
main{{max-width:900px;margin:0 auto;padding:14vh 24px 10vh}}
h1{{font-size:clamp(2.2rem,6vw,4.2rem);letter-spacing:.02em;margin-bottom:.6em;text-shadow:0 0 30px var(--accent)}}
.lead{{color:var(--fg);opacity:.85;font-size:clamp(1.05rem,2.5vw,1.4rem);max-width:56ch}}
.panel{{background:var(--panel);border:1px solid var(--edge);border-radius:18px;padding:28px;margin-top:36px;backdrop-filter:blur(10px)}}
.panel h2{{color:var(--accent);margin-bottom:.5em}}
.panel p{{line-height:1.7;opacity:.9}}
.cta{{display:inline-block;margin-top:28px;padding:14px 30px;border-radius:40px;color:var(--bg);background:var(--accent);font-weight:700;text-decoration:none;box-shadow:0 0 30px var(--accent)}}
</style>
</head>
<body>
<div id="bg"></div>
<main>
<h1>{title_html}</h1>
<p class="lead">{description_html}</p>
<section class="panel">
<h2>Built with A3THER 3D Website Maker</h2>
<p>This page was generated automatically from a plain-language description.
The background is a live Three.js scene — rotate, glow and all.</p>
</section>
<a class="cta" href="#">Explore</a>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
{theme["scene"]}
</script>
</body>
</html>
"""


def _llm_page(title: str, theme_name: str, description: str) -> str | None:
    try:
        from gateway.router import get_gateway

        gateway = get_gateway()
        if not gateway.any_available():
            return None
    except Exception:  # noqa: BLE001
        return None

    theme = THEMES.get(theme_name, THEMES[DEFAULT_THEME])
    try:
        prompt = (
            "You are a senior 3D web developer. Write a COMPLETE single-file HTML "
            "page for this site. Requirements:\n"
            f"- Title: {title!r}\n- Description: {description}\n"
            "- Include the CSS variables below, the Three.js scene snippet verbatim, "
            "and a hero section plus at least two content panels matching the description.\n"
            "- Use the Three.js CDN script src='https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js'.\n"
            "- Output ONLY the raw HTML — no markdown fences, no commentary.\n\n"
            f"CSS vars:\n{theme['css']}\n\n"
            f"Scene snippet:\n{theme['scene']}\n\nHTML:"
        )
        page = gateway.complete_text(prompt, max_tokens=4096)
        page = page.strip()
        if page.startswith("```"):
            page = re.sub(r"^```[a-zA-Z]*\n?", "", page)
            page = re.sub(r"\n?```\s*$", "", page)
        # Validity gate: must be a real page and keep the 3D scene.
        if "<html" not in page.lower() or "<body" not in page.lower():
            LOGGER.warning("LLM page failed validity check — using template")
            return None
        if "three.min.js" not in page:
            LOGGER.warning("LLM page dropped the Three.js scene — using template")
            return None
        return page
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("LLM page generation failed: %s", exc)
        return None


def generate_website(description: str, name: str = "", theme: str = "") -> dict:
    """Generate a 3D website and return its info dict."""
    theme_name = theme if theme in THEMES else DEFAULT_THEME
    site_name = _safe_name(name)
    title = (name or "My 3D Site").strip()

    page = _llm_page(title, theme_name, description)
    source = "llm" if page is not None else "template"
    if page is None:
        page = _template_page(title, theme_name, description)

    site_dir = OUTPUT_DIR / site_name
    site_dir.mkdir(parents=True, exist_ok=True)
    index = site_dir / "index.html"
    index.write_text(page, encoding="utf-8")

    return {
        "ok": True,
        "name": site_name,
        "theme": theme_name,
        "source": source,
        "path": str(index),
        "bytes": len(page),
        "preview": f"Output/websites/{site_name}/index.html",
    }


def list_websites() -> list[dict]:
    """Enumerate previously generated sites."""
    if not OUTPUT_DIR.exists():
        return []
    sites = []
    for site_dir in sorted(OUTPUT_DIR.iterdir()):
        if (site_dir / "index.html").exists():
            sites.append(
                {
                    "name": site_dir.name,
                    "path": str(site_dir / "index.html"),
                    "bytes": (site_dir / "index.html").stat().st_size,
                }
            )
    return sites
