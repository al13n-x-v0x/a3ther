"""
actions — A3THER intent-execution modules.

Each submodule exposes the callable(s) the brain dispatches to for a given
intent (OPEN_APP, WEB_SEARCH, WEATHER, SYSTEM, FILE, YOUTUBE, …). All
implementations are dependency-light and degrade gracefully when optional
libraries (psutil, playwright, …) are missing — an action should never
crash the brain.
"""

from __future__ import annotations

from . import (  # noqa: F401
    browser_control,
    computer_control,
    file_controller,
    open_app,
    send_message,
    system_monitor,
    weather_report,
    web_search,
    youtube_video,
)

__all__ = [
    "open_app",
    "web_search",
    "browser_control",
    "weather_report",
    "system_monitor",
    "file_controller",
    "computer_control",
    "send_message",
    "youtube_video",
]
