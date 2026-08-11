"""
A3THER 3D Website Maker.

Generates single-file, Three.js-powered 3D websites from a plain-English
description. With a gateway provider configured the LLM writes the site;
without one, a themed template is assembled from :mod:`website_maker.themes`.
Sites land in ``Output/websites/<name>/index.html``.
"""
from .generator import generate_website, list_websites

__all__ = ["generate_website", "list_websites"]
