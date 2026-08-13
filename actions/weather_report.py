"""
actions/weather_report.py — WEATHER intent.

Fetches current conditions via Open-Meteo (no API key). Falls back to a
helpful message when offline.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Dict


def _geocode(city: str) -> tuple[float, float] | None:
    """Best-effort city → (lat, lon) via the Open-Meteo geocoding API."""
    url = "https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json&name=" + urllib.parse.quote(city)
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        results = data.get("results") or []
        if results:
            return float(results[0]["latitude"]), float(results[0]["longitude"])
    except Exception:  # noqa: BLE001
        pass
    return None


def weather_action(params: Dict[str, Any] | None = None) -> str:
    city = ((params or {}).get("city") or "").strip()
    if not city:
        return "No city provided — tell me which city's weather you want."
    coords = _geocode(city)
    if not coords:
        return f"Could not find weather for '{city}' (offline?)."
    lat, lon = coords
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        current = data.get("current") or {}
        temp = current.get("temperature_2m")
        hum = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        code = current.get("weather_code")
        cond = {
            0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
            45: "foggy", 48: "icy fog", 51: "light drizzle", 61: "light rain",
            63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
            75: "heavy snow", 80: "rain showers", 95: "thunderstorm",
        }.get(code, "unknown")
        parts = [f"Weather in {city.title()}: {cond}"]
        if temp is not None:
            parts.append(f"{temp:.0f}°C")
        if hum is not None:
            parts.append(f"humidity {hum:.0f}%")
        if wind is not None:
            parts.append(f"wind {wind:.0f} km/h")
        return ", ".join(parts)
    except Exception as exc:  # noqa: BLE001
        return f"Weather lookup failed for '{city}': {exc}"


if __name__ == "__main__":  # pragma: no cover
    print(weather_action({"city": "New York"}))
