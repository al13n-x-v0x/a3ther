"""
backend/services/predictor_service.py — the AI Predictor.

Predicts what happens *next* from real history: it keeps a rolling window
of telemetry snapshots (fed from ``stats_service``), fits a least-squares
trend line to each metric, and projects the next value at a sensible
horizon. Every prediction is honest:

- trends come from least-squares slopes of real samples (never random),
- a prediction is only emitted once enough history exists (≥ 5 samples),
- confidence scales with sample count and how linear the fit is,
- when a metric is flat it says "stable" instead of inventing drama.

It also folds in two real context signals:
- device inventory from ``device_service`` (RSSI of the strongest
  Bluetooth device → signal stable / fluctuating),
- the current weather condition (real storms explain Wi-Fi/BT dropouts).

No LLM is required — the model is a tiny, dependency-free linear trend
extrapolator, so it works offline and instantly.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from .stats_service import get_stats_snapshot

#: Max history window kept per metric (30 samples @ ~30 s ≈ 15 min of memory).
_MAX_SAMPLES = 30
#: Minimum samples before a trend prediction is offered.
_MIN_SAMPLES = 5

#: How far ahead each metric projects (minutes).
_HORIZONS = {
    "cpu": 10,
    "ram": 15,
    "network": 5,
    "battery": 60,   # minutes — battery horizon is handled in hours below
    "disk": 60 * 24,  # 24 h
    "temp": 10,
}


class _Predictor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: deque[dict] = deque(maxlen=_MAX_SAMPLES)
        self._last_feed = 0.0
        self._min_feed_gap = 30.0  # seconds between history samples
        self._last_net_total: float | None = None

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #
    def feed(self, snapshot: dict | None = None) -> None:
        """Append one snapshot to history (rate-limited to one per 30 s)."""
        now = time.time()
        with self._lock:
            if now - self._last_feed < self._min_feed_gap and self._history:
                return
            self._last_feed = now
        snap = snapshot or self._live_snapshot()
        if not snap:
            return
        # Network activity is stored as the *delta* since the previous sample.
        # Cumulative byte counters only ever rise, so predicting on the sum
        # would trivially say "up" forever — per-interval deltas are the real
        # signal (MB of activity between polls).
        net_total = None
        net = None
        net_info = snap.get("network") or {}
        if net_info.get("recv_mb") is not None and net_info.get("sent_mb") is not None:
            net_total = float(net_info.get("recv_mb", 0) or 0) + float(net_info.get("sent_mb", 0) or 0)
        with self._lock:
            if net_total is not None and self._last_net_total is not None and net_total >= self._last_net_total:
                net = net_total - self._last_net_total
            if net_total is not None:
                self._last_net_total = net_total

        record = {
            "t": now,
            "cpu": snap.get("cpu", {}).get("percent") if snap.get("cpu") else None,
            "ram": snap.get("ram", {}).get("percent") if snap.get("ram") else None,
            "net": net,
            "disk": snap.get("storage", {}).get("percent") if snap.get("storage") else None,
            "temp": (snap.get("cpu") or {}).get("temp_c"),
            "battery": (snap.get("battery") or {}).get("percent"),
        }
        with self._lock:
            self._history.append(record)

    @staticmethod
    def _live_snapshot() -> dict:
        try:
            return get_stats_snapshot()
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------------ #
    # Math
    # ------------------------------------------------------------------ #
    def _series(self, key: str) -> list[tuple[float, float]]:
        """[(t_seconds, value)] for a metric, oldest → newest."""
        with self._lock:
            return [(r["t"], r[key]) for r in self._history if r.get(key) is not None]

    @staticmethod
    def _trend(points: list[tuple[float, float]]) -> dict | None:
        """Least-squares fit. Returns slope (per minute), r², latest value."""
        n = len(points)
        if n < 2:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        sxx = sum((x - mean_x) ** 2 for x in xs)
        if sxx == 0:
            return None
        sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        slope_per_sec = sxy / sxx
        slope_per_min = slope_per_sec * 60.0
        # r² = fraction of variance explained by the line.
        syy = sum((y - mean_y) ** 2 for y in ys)
        r2 = (sxy ** 2) / (sxx * syy) if syy else 0.0
        return {
            "slope": slope_per_min,   # units per minute
            "r2": r2,
            "last": ys[-1],
            "count": n,
        }

    # ------------------------------------------------------------------ #
    # Predictions
    # ------------------------------------------------------------------ #
    def _metric_prediction(
        self, key: str, label: str, unit: str, icon: str, kind: str = "pct"
    ) -> dict | None:
        points = self._series(key)
        if len(points) < _MIN_SAMPLES:
            return None
        fit = self._trend(points)
        if fit is None:
            return None
        horizon = _HORIZONS.get(key, 10)
        slope = fit["slope"]
        if abs(slope) < 0.05:
            return None  # flat — no prediction worth showing

        predicted = fit["last"] + slope * horizon
        direction = "up" if slope > 0 else "down"
        confidence = self._confidence(fit)
        if kind == "pct":
            predicted = max(0.0, min(100.0, predicted))
            detail = f"trend {slope:+.1f}%/min · projected ≈ {predicted:.0f}%"
        elif kind == "mbps":
            predicted = max(0.0, predicted)
            detail = f"activity {slope:+.0f} MB/min · projected ≈ {predicted:.0f} MB"
        else:  # temp
            detail = f"trend {slope:+.1f}°C/min · projected ≈ {predicted:.0f}°C"

        note = self._note_for(key, direction, predicted, horizon)
        return {
            "metric": key,
            "label": label,
            "icon": icon,
            "unit": unit,
            "value_now": round(fit["last"], 1),
            "value_pred": round(predicted, 1),
            "trend": direction,
            "slope": round(slope, 3),
            "horizon_min": horizon,
            "confidence": confidence,
            "note": note,
        }

    @staticmethod
    def _note_for(key: str, direction: str, predicted: float, horizon: int) -> str:
        if key == "cpu":
            if direction == "up" and predicted > 85:
                return f"load projected to hit {predicted:.0f}% in ~{horizon} min — expect slowdowns"
            if direction == "up":
                return f"load climbing — likely reaches {predicted:.0f}% within {horizon} min"
            return f"load easing — projected down to {predicted:.0f}% in ~{horizon} min"
        if key == "ram":
            if direction == "up" and predicted > 90:
                return f"memory pressure — may hit {predicted:.0f}% in ~{horizon} min, consider closing apps"
            return f"memory {direction} — projected {predicted:.0f}% within {horizon} min"
        if key == "network":
            return f"traffic {direction} — projected {predicted:.0f} MB activity within {horizon} min"
        if key == "battery":
            if direction == "up":
                return f"charging — projected {predicted:.0f}% within the hour"
            return f"draining — projected {predicted:.0f}% in the next hour"
        if key == "disk":
            return f"disk usage {direction} — projected {predicted:.0f}% within 24 h"
        if key == "temp":
            if direction == "up" and predicted > 80:
                return f"thermal climb — may reach {predicted:.0f}°C, watch fans"
            return f"temperature {direction} — projected {predicted:.0f}°C in {horizon} min"
        return ""

    @staticmethod
    def _confidence(fit: dict) -> int:
        """0–95: more samples + straighter line → higher confidence."""
        count_bonus = min(20 + fit["count"] * 2, 60)
        linearity = max(0.0, min(fit["r2"], 1.0)) * 35
        return int(min(count_bonus + linearity, 95))

    # ------------------------------------------------------------------ #
    # Context signals
    # ------------------------------------------------------------------ #
    def _device_signal(self) -> dict | None:
        """RSSI trend of the strongest Bluetooth device, if any."""
        try:
            from .device_service import get_devices_snapshot

            snap = get_devices_snapshot()
        except Exception:  # noqa: BLE001
            return None
        rssis = [
            d.get("rssi")
            for d in (snap.get("devices") or [])
            if d.get("source") == "bluetooth" and d.get("rssi") is not None
        ]
        if not rssis:
            return None
        strongest = max(rssis)
        state = "fluctuating" if max(rssis) - min(rssis) > 15 else "stable"
        return {"rssi": strongest, "state": state, "count": len(rssis)}

    def _weather_context(self) -> str | None:
        try:
            from .weather_service import get_weather

            w = get_weather()
            cond = (w.get("condition") or "").lower()
            if not cond or cond == "offline":
                return None
            return cond
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    # Public
    # ------------------------------------------------------------------ #
    def get_predictions(self) -> dict:
        """Full forecast payload for /api/live/predict."""
        self.feed()  # rate-limited append of the freshest snapshot

        predictions: list[dict] = []
        builders = [
            ("cpu", "CPU LOAD", "%", "fa-microchip", "pct"),
            ("ram", "MEMORY", "%", "fa-memory", "pct"),
            ("network", "NETWORK", "MB", "fa-network-wired", "mbps"),
            ("battery", "BATTERY", "%", "fa-battery-three-quarters", "pct"),
            ("disk", "STORAGE", "%", "fa-hard-drive", "pct"),
            ("temp", "TEMPERATURE", "°C", "fa-temperature-half", "temp"),
        ]
        for key, label, unit, icon, kind in builders:
            try:
                pred = self._metric_prediction(key, label, unit, icon, kind)
            except Exception:  # noqa: BLE001
                pred = None
            if pred:
                predictions.append(pred)

        # Context cards (always honest — only when real data exists).
        context: list[dict] = []
        signal = self._device_signal()
        if signal:
            context.append({
                "kind": "signal",
                "icon": "fa-bluetooth-b",
                "title": "BLUETOOTH SIGNAL",
                "detail": (
                    f"{signal['state'].upper()} — strongest device at {signal['rssi']} dBm "
                    f"({signal['count']} BLE device(s))"
                ),
            })
        weather = self._weather_context()
        if weather:
            context.append({
                "kind": "weather",
                "icon": "fa-cloud-rain",
                "title": "WEATHER CONTEXT",
                "detail": weather.upper(),
            })

        predictions.sort(key=lambda p: p["confidence"], reverse=True)
        headline = predictions[0] if predictions else None
        samples = len(_PREDICTOR._history) if _PREDICTOR._history else 0
        return {
            "generated": time.time(),
            "samples": samples,
            "learning": samples < _MIN_SAMPLES,
            "headline": headline,
            "predictions": predictions,
            "context": context,
        }


# --------------------------------------------------------------------------- #
# Singleton
# --------------------------------------------------------------------------- #
_PREDICTOR = _Predictor()


def get_predictions() -> dict:
    """Process-wide predictor entry point (used by the API)."""
    return _PREDICTOR.get_predictions()


def feed_snapshot(snapshot: dict) -> None:
    """Allow the live-status poller to feed history opportunistically."""
    _PREDICTOR.feed(snapshot)
