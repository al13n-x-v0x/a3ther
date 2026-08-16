"""
location_service.py — real geo-location for the HUD environment panel.

Resolves the public IP to a city/country via ip-api.com (free, no API key).
Results are cached for 30 minutes so the dashboard never hammers the API.

Offline / blocked? Falls back to a configurable city (``A3THER_CITY`` env or
"New York, USA") and reports ``source: "fallback"`` so the UI stays honest.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from urllib.request import urlopen

LOGGER = logging.getLogger("a3ther.services.location")

_CACHE_TTL = 1800.0  # 30 minutes
_DEFAULT_CITY = os.environ.get("A3THER_CITY", "New York, USA")


def _fetch_via_ipapi() -> dict | None:
    try:
        with urlopen("http://ip-api.com/json/?fields=status,city,regionName,country,lat,lon,query,timezone", timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") == "success":
            return {
                "city": data.get("city") or data.get("regionName") or "Unknown",
                "country": data.get("country") or "",
                "lat": float(data.get("lat", 0)),
                "lon": float(data.get("lon", 0)),
                "ip": data.get("query"),
                "timezone": data.get("timezone"),
                "source": "ip-api",
            }
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("ip-api lookup failed: %s", exc)
    return None


def get_location(force: bool = False) -> dict:
    """Return the current location, cached; falls back to A3THER_CITY."""
    now = time.time()
    if not force and _CACHE.get("updated", 0) and (now - _CACHE["updated"]) < _CACHE_TTL:
        return _CACHE

    data = _fetch_via_ipapi()
    if not data:
        # Fallback: configured city with unknown coordinates (weather will
        # still resolve via Open-Meteo geocoding).
        parts = _DEFAULT_CITY.split(",")
        data = {
            "city": parts[0].strip(),
            "country": parts[1].strip() if len(parts) > 1 else "",
            "lat": None,
            "lon": None,
            "ip": None,
            "timezone": None,
            "source": "fallback",
        }
    data["updated"] = now
    with _LOCK:
        _CACHE.clear()
        _CACHE.update(data)
    return data


def get_city_label() -> str:
    loc = get_location()
    return f"{loc.get('city', '')}, {loc.get('country', '')}".strip(" ,")


# ------------------------------------------------------------------------- #
_CACHE: dict = {"source": "none", "updated": 0.0}
_LOCK = threading.Lock()
