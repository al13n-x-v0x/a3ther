"""
weather_service.py — real current weather via the Open-Meteo API.

Free, no API key. Two steps:

1. Resolve the city name to coordinates (Open-Meteo geocoding) — unless the
   location service already gave us exact lat/lon.
2. Fetch current conditions: temperature, humidity, wind, pressure,
   visibility, UV index, weather code (mapped to icon/text).

Results are cached for 10 minutes. If the network is unavailable the service
reports ``source: "offline"`` with empty values instead of inventing data.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from .location_service import get_location

LOGGER = logging.getLogger("a3ther.services.weather")

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
try:
    from config.paths import data_path as _data_path

    _OVERRIDE_PATH = _data_path("config/weather_override.json")
except Exception:  # noqa: BLE001
    _OVERRIDE_PATH = _BASE_DIR / "config" / "weather_override.json"

_CACHE_TTL = 600.0      # 10 minutes for good data
_OFFLINE_TTL = 60.0     # only 60 s for offline results so recovery is quick
_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1&language=en"
_WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
    "weather_code,wind_speed_10m,pressure_msl,visibility,uv_index"
    "&timezone=auto"
)

# WMO weather code -> (label, font-awesome icon)
_WMO = {
    0: ("CLEAR SKY", "fa-sun"),
    1: ("MAINLY CLEAR", "fa-sun"),
    2: ("PARTLY CLOUDY", "fa-cloud-sun"),
    3: ("OVERCAST", "fa-cloud"),
    45: ("FOG", "fa-smog"),
    48: ("RIME FOG", "fa-smog"),
    51: ("LIGHT DRIZZLE", "fa-cloud-rain"),
    53: ("DRIZZLE", "fa-cloud-rain"),
    55: ("DENSE DRIZZLE", "fa-cloud-rain"),
    61: ("LIGHT RAIN", "fa-cloud-showers-heavy"),
    63: ("RAIN", "fa-cloud-rain"),
    65: ("HEAVY RAIN", "fa-cloud-showers-heavy"),
    71: ("LIGHT SNOW", "fa-snowflake"),
    73: ("SNOW", "fa-snowflake"),
    75: ("HEAVY SNOW", "fa-snowflake"),
    80: ("LIGHT SHOWERS", "fa-cloud-showers-heavy"),
    81: ("SHOWERS", "fa-cloud-showers-heavy"),
    82: ("VIOLENT SHOWERS", "fa-cloud-showers-heavy"),
    95: ("THUNDERSTORM", "fa-bolt"),
    96: ("THUNDERSTORM + HAIL", "fa-bolt"),
    99: ("THUNDERSTORM + HAIL", "fa-bolt"),
}


def _get(url: str, timeout: float = 8.0) -> dict | None:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Open-Meteo request failed (%s): %s", url[:60], exc)
        return None


def _geocode(city: str) -> tuple[float, float] | None:
    data = _get(_GEO_URL.format(city=quote(city)))
    if data and data.get("results"):
        first = data["results"][0]
        return float(first["latitude"]), float(first["longitude"])
    return None


def _describe(code: int) -> dict:
    label, icon = _WMO.get(int(code), ("UNKNOWN", "fa-cloud"))
    return {"label": label, "icon": icon}


def get_city_override() -> str | None:
    """The user-pinned city name, if any (persisted in config/)."""
    try:
        with open(_OVERRIDE_PATH, encoding="utf-8") as fh:
            return (json.load(fh).get("city") or "").strip() or None
    except Exception:  # noqa: BLE001
        return None


def set_city_override(city: str | None) -> dict:
    """Pin the weather city. Pass None/empty to clear the override."""
    try:
        _OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if city and city.strip():
            with open(_OVERRIDE_PATH, "w", encoding="utf-8") as fh:
                json.dump({"city": city.strip()}, fh)
        else:
            try:
                _OVERRIDE_PATH.unlink()
            except FileNotFoundError:
                pass
        # Override change invalidates the stale cache immediately.
        with _LOCK:
            _CACHE.clear()
            _CACHE["updated"] = 0.0
        return {"ok": True, "city": city.strip() if city else None}
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("city override failed")
        return {"ok": False, "error": str(exc)}


def get_weather(force: bool = False, city: str | None = None) -> dict:
    """Return current weather for the detected (or overridden) location.

    ``city`` optionally pins a new override and refreshes immediately.
    """
    if city is not None:
        set_city_override(city)
    override = get_city_override()

    loc = get_location()
    if override:
        # A pinned city takes precedence — geocode it directly.
        loc = {"city": override, "lat": None, "lon": None}

    now = time.time()
    ttl = _OFFLINE_TTL if _CACHE.get("source") == "offline" else _CACHE_TTL
    cache_fresh = (
        not force
        and _CACHE.get("updated", 0)
        and (now - _CACHE["updated"]) < ttl
        and _CACHE.get("city") == loc.get("city")
    )
    if cache_fresh:
        return _CACHE

    lat, lon = loc.get("lat"), loc.get("lon")
    source = "open-meteo"
    if lat is None or lon is None:
        coords = _geocode(loc.get("city", "New York"))
        if coords:
            lat, lon = coords
        else:
            result = {
                "source": "offline",
                "city": loc.get("city"),
                "temperature_c": None,
                "condition": "OFFLINE",
                "icon": "fa-cloud",
                "humidity": None,
                "wind_kmh": None,
                "pressure_hpa": None,
                "visibility_km": None,
                "uv_index": None,
                "updated": now,
            }
            with _LOCK:
                _CACHE.clear()
                _CACHE.update(result)
            return result

    data = _get(_WEATHER_URL.format(lat=lat, lon=lon))
    if not data or "current" not in data:
        result = {
            "source": "offline",
            "city": loc.get("city"),
            "temperature_c": None,
            "condition": "OFFLINE",
            "icon": "fa-cloud",
            "humidity": None,
            "wind_kmh": None,
            "pressure_hpa": None,
            "visibility_km": None,
            "uv_index": None,
            "updated": now,
        }
    else:
        cur = data["current"]
        desc = _describe(cur.get("weather_code", 0))
        visibility = cur.get("visibility")
        result = {
            "source": source,
            "city": loc.get("city"),
            "temperature_c": round(float(cur.get("temperature_2m", 0)), 1),
            "feels_like_c": round(float(cur.get("apparent_temperature", 0)), 1),
            "condition": desc["label"],
            "icon": desc["icon"],
            "humidity": round(float(cur.get("relative_humidity_2m", 0)), 0),
            "wind_kmh": round(float(cur.get("wind_speed_10m", 0)), 1),
            "pressure_hpa": round(float(cur.get("pressure_msl", 0)), 0),
            "visibility_km": round(visibility / 1000, 1) if visibility else None,
            "uv_index": cur.get("uv_index"),
            "updated": now,
        }
    with _LOCK:
        _CACHE.clear()
        _CACHE.update(result)
    return result


# ------------------------------------------------------------------------- #
_CACHE: dict = {"source": "none", "updated": 0.0}
_LOCK = threading.Lock()
