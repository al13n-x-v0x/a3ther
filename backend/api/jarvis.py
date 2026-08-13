"""
backend/api/jarvis.py — the A3THER Lab feature set.

Real implementations (no fake prototypes) for the creative + smart-home
capabilities that turn A3THER from a chatbot into a full assistant
(internal module name kept as ``jarvis`` for continuity):

    POST /api/jarvis/image    generate an image from a prompt (OpenAI images API)
    POST /api/jarvis/camera   capture a frame from the webcam (OpenCV)
    POST /api/jarvis/vision   "look at" an image with a vision-capable LLM (OpenAI)
    GET  /api/jarvis/ha       list Home Assistant entities grouped by room/domain
    POST /api/jarvis/ha/toggle  toggle one Home Assistant entity
    GET  /api/jarvis/status   which features are configured right now

Every feature degrades honestly: when a key or optional dependency is
missing the endpoint returns a 400 with exact setup instructions — never a
silent fake result.
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

jarvis_router = APIRouter(prefix="/api/jarvis", tags=["jarvis"])

_OPENAI_BASE = "https://api.openai.com/v1"
_IMAGE_MODEL = "gpt-image-1"
_VISION_MODEL = "gpt-4.1-mini"


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class ImageRequest(BaseModel):
    prompt: str
    size: str | None = "1024x1024"


class VisionRequest(BaseModel):
    prompt: str
    image_base64: str  # PNG/JPEG data (no data: prefix)


class HaToggleRequest(BaseModel):
    entity_id: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _openai_key() -> str:
    from config import get_config  # noqa: PLC0415

    cfg = get_config()
    key = str(cfg.get("openai_api_key") or "").strip()
    if not key:
        # Also try the per-provider map (first_run stores openai under both).
        providers = cfg.get("llm_providers") or {}
        key = str(providers.get("openai_api_key") or "").strip()
    return key


def _ha_config() -> tuple[str, str]:
    """(url, token) for Home Assistant, or ("", "")."""
    try:
        from core.ui_settings import get_ui_setting  # noqa: PLC0415

        url = str(get_ui_setting("ha_url", "") or "").strip().rstrip("/")
        token = str(get_ui_setting("ha_token", "") or "").strip()
        return url, token
    except Exception:  # noqa: BLE001
        return "", ""


def _http_json(url: str, payload: dict | None = None, token: str | None = None, timeout: float = 30.0):
    """Dependency-free HTTP JSON call (urllib). Returns (status, body)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if payload is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Image generation
# --------------------------------------------------------------------------- #
@jarvis_router.post("/image")
def jarvis_image(body: ImageRequest):
    prompt = (body.prompt or "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt is empty"}, status_code=400)
    key = _openai_key()
    if not key:
        return JSONResponse(
            {"error": "Image generation needs an OpenAI key. "
             "Add one in Settings → API (or run: python -m core.first_run)."},
            status_code=400,
        )
    status, data = _http_json(
        f"{_OPENAI_BASE}/images/generations",
        {
            "model": _IMAGE_MODEL,
            "prompt": prompt,
            "size": body.size or "1024x1024",
            "n": 1,
        },
        token=key,
        timeout=90.0,
    )
    if status != 200:
        return JSONResponse({"error": f"OpenAI images failed ({status}): {data}"}, status_code=502)
    try:
        item = (data.get("data") or [{}])[0]
    except Exception:  # noqa: BLE001
        item = {}
    if item.get("b64_json"):
        return {"ok": True, "prompt": prompt, "image_base64": item["b64_json"]}
    if item.get("url"):
        return {"ok": True, "prompt": prompt, "url": item["url"]}
    return JSONResponse({"error": "no image in the OpenAI response"}, status_code=502)


# --------------------------------------------------------------------------- #
# Camera + vision
# --------------------------------------------------------------------------- #
@jarvis_router.post("/camera")
def jarvis_camera():
    """Capture one frame from the default webcam (OpenCV) → base64 PNG."""
    try:
        import cv2  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return JSONResponse(
            {"error": "Camera needs OpenCV. Install: pip install opencv-python"},
            status_code=400,
        )
    cap = None
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not cap.isOpened():
            return JSONResponse({"error": "no webcam found on this machine"}, status_code=400)
        ok, frame = cap.read()
        if not ok or frame is None:
            return JSONResponse({"error": "could not read a frame from the camera"}, status_code=500)
        ok, buf = cv2.imencode(".png", frame)
        if not ok:
            return JSONResponse({"error": "could not encode the frame"}, status_code=500)
        return {
            "ok": True,
            "image_base64": base64.b64encode(buf.tobytes()).decode("ascii"),
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
        }
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"camera failed: {exc}"}, status_code=500)
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:  # noqa: BLE001
                pass


@jarvis_router.post("/vision")
def jarvis_vision(body: VisionRequest):
    """Ask a vision-capable LLM to look at a picture (OpenAI)."""
    prompt = (body.prompt or "").strip()
    image = (body.image_base64 or "").strip()
    if not image:
        return JSONResponse({"error": "image_base64 is empty"}, status_code=400)
    key = _openai_key()
    if not key:
        return JSONResponse(
            {"error": "Vision needs an OpenAI key (Settings → API)."},
            status_code=400,
        )
    payload = {
        "model": _VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or "Describe this image in detail."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
                ],
            }
        ],
        "max_tokens": 500,
    }
    status, data = _http_json(
        f"{_OPENAI_BASE}/chat/completions", payload, token=key, timeout=60.0
    )
    if status != 200:
        return JSONResponse({"error": f"OpenAI vision failed ({status}): {data}"}, status_code=502)
    try:
        reply = data["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "unexpected vision response"}, status_code=502)
    return {"ok": True, "answer": reply}


# --------------------------------------------------------------------------- #
# Home Assistant
# --------------------------------------------------------------------------- #
@jarvis_router.get("/ha")
def jarvis_ha():
    url, token = _ha_config()
    if not url or not token:
        return JSONResponse(
            {"error": "Home Assistant is not configured. "
             "Settings → Home Assistant: paste your server URL and a long-lived "
             "access token (Profile → Security → Long-lived access tokens)."},
            status_code=400,
        )
    status, data = _http_json(f"{url}/api/states", token=token, timeout=15.0)
    if status != 200:
        return JSONResponse(
            {"error": f"Home Assistant unreachable ({status}): {data.get('error') or data}"},
            status_code=502,
        )
    states = data if isinstance(data, list) else []
    by_domain: dict[str, list[dict]] = {}
    for st in states:
        entity = st.get("entity_id", "")
        domain = entity.split(".")[0] if "." in entity else "other"
        by_domain.setdefault(domain, []).append(
            {
                "entity_id": entity,
                "name": (st.get("attributes") or {}).get("friendly_name", entity),
                "state": st.get("state"),
                "attributes": (st.get("attributes") or {}),
            }
        )
    return {"ok": True, "entities": len(states), "by_domain": by_domain}


@jarvis_router.post("/ha/toggle")
def jarvis_ha_toggle(body: HaToggleRequest):
    url, token = _ha_config()
    entity = (body.entity_id or "").strip()
    if not url or not token:
        return JSONResponse(
            {"error": "Home Assistant is not configured (Settings → Home Assistant)."},
            status_code=400,
        )
    if not entity:
        return JSONResponse({"error": "entity_id is empty"}, status_code=400)
    status, data = _http_json(
        f"{url}/api/services/homeassistant/toggle",
        {"entity_id": entity},
        token=token,
        timeout=15.0,
    )
    if status != 200:
        return JSONResponse(
            {"error": f"toggle failed ({status}): {data.get('error') or data}"},
            status_code=502,
        )
    return {"ok": True, "entity_id": entity, "toggled": True}


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
@jarvis_router.get("/status")
def jarvis_status():
    key = _openai_key()
    url, token = _ha_config()
    try:
        import cv2  # noqa: PLC0415, F401

        camera = True
    except Exception:  # noqa: BLE001
        camera = False
    return {
        "ok": True,
        "image_generation": bool(key),
        "vision": bool(key),
        "camera": camera,
        "home_assistant": bool(url and token),
    }
