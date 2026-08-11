"""
backend/api/live.py — real-time HUD data endpoints.

Every panel on the A.3.T.H.E.R. dashboard polls these endpoints. All
services degrade gracefully (offline → empty/null values with a ``source``
flag), so the HUD always shows the truth, never fabricated numbers.

Endpoints
---------
GET  /api/live/status        → full telemetry snapshot (psutil)
GET  /api/live/specs         → real hardware specs (CPU brand, GPU, RAM, OS)
GET  /api/live/devices       → Bluetooth + LAN device inventory (cached 5 s)
POST /api/live/devices/rescan→ force a Bluetooth + ARP rescan
GET  /api/live/weather       → current weather (Open-Meteo, cached 10 min)
GET  /api/live/weather?city=X→ pin/refresh weather for a specific city
POST /api/live/weather/city  → persist a city override (body: {"city": "…"})
GET  /api/live/location      → detected city/country (ip-api, cached 30 min)
"""
from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

live_router = APIRouter(prefix="/api/live", tags=["live"])


class CityOverride(BaseModel):
    city: str | None = None


@live_router.get("/status")
def live_status():
    t0 = time.time()
    try:
        from backend.services.stats_service import get_stats_snapshot, get_top_processes

        snapshot = get_stats_snapshot()
        snapshot["top_processes"] = get_top_processes(limit=6)
        snapshot["latency_ms"] = round((time.time() - t0) * 1000, 1)
        # Feed the AI Predictor a fresh sample every poll (rate-limited 30 s).
        try:
            from backend.services.predictor_service import feed_snapshot

            feed_snapshot(snapshot)
        except Exception:  # noqa: BLE001
            pass
        return snapshot
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)


@live_router.get("/predict")
def live_predict():
    """AI Predictor — next-value projections from real telemetry trends."""
    try:
        from backend.services.predictor_service import get_predictions

        return get_predictions()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)


@live_router.get("/specs")
def live_specs(force: bool = False):
    """Real hardware specs — CPU brand, GPU(s), RAM, storage, OS, battery.

    Results are cached 60 s server-side (see specs_service) so the
    PowerShell GPU lookup isn't re-spawned on every frontend poll.
    """
    try:
        from backend.services.specs_service import get_hardware_specs_cached

        return get_hardware_specs_cached(force=bool(force))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)


@live_router.get("/devices")
def live_devices():
    try:
        from backend.services.device_service import get_devices_snapshot

        return get_devices_snapshot()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)


@live_router.post("/devices/rescan")
def live_devices_rescan():
    try:
        from backend.services.device_service import refresh_devices

        return refresh_devices()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)


@live_router.get("/weather")
def live_weather(force: bool = False, city: str | None = None):
    try:
        from backend.services.weather_service import get_weather

        return get_weather(force=bool(force), city=city)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)


@live_router.post("/weather/city")
def live_weather_city(body: CityOverride):
    """Persist a pinned weather city (Settings panel). Pass empty to clear."""
    try:
        from backend.services.weather_service import set_city_override, get_weather

        result = set_city_override(body.city)
        if result.get("ok"):
            weather = get_weather(force=True)
            result["weather"] = weather
        return result
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)


@live_router.get("/location")
def live_location(force: bool = False):
    try:
        from backend.services.location_service import get_location

        return get_location(force=bool(force))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)


@live_router.get("/files")
def live_files(limit: int = 24):
    """Recent indexed workspace files — powers the Files nav view."""
    try:
        from codebase.indexer import CodeIndexer

        indexer = CodeIndexer()
        # Lazy first scan so a fresh checkout isn't an empty list forever.
        if not indexer.files():
            try:
                indexer.index_directory(".")
            except Exception:  # noqa: BLE001
                pass
        files = sorted(indexer.files(), key=lambda f: f.get("mtime", 0), reverse=True)[: int(limit)]
        return {
            "files": [
                {
                    "path": f.get("path"),
                    "language": f.get("language"),
                    "size": f.get("size"),
                    "symbols": len(f.get("symbols", [])),
                }
                for f in files
            ],
            "count": len(files),
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc), "source": "error"}, status_code=500)
