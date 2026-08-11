"""
sync/mobile.py — native mobile & iOS compatibility layer.

``MobileDeviceController`` is the abstraction for iOS/iPhone/Android target
nodes. It never assumes a transport is live: every path is best-effort and
returns a result dict, so a missing webhook or unreachable APNs endpoint
degrades to a clean message instead of raising.

Provided integrations:
- **iOS Focus modes** — payloads for Shortcuts-driven Focus changes
  (``work`` / ``sleep`` / ``driving`` / ``do-not-disturb`` / custom).
- **APNs-style push payloads** — full APNs envelope builder (alert, badge,
  sound, category, mutable-content) that can be handed to a push gateway,
  or delivered through a companion-app webhook.
- **Shortcuts webhook payloads** — the JSON body an iOS Shortcut can receive
  and act on (``run_focus``, ``show_notification``, ``flash_screen`` …).
- **Device profiling** — classify an incoming socket/UA as iPhone/iPad/
  Android or a computer terminal and shape the payload accordingly.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

from .logging import log
from .protocol import ClientProfile, MobileDeviceState

#: Allowed iOS Focus mode names (with a sane default mapping).
_FOCUS_MODES = {"work", "sleep", "driving", "personal", "do-not-disturb", "gaming", "none"}

#: Where companion-app webhooks point (configurable at runtime).
_WEBHOOK_BASE = {"app": "", "shortcuts": ""}
_WEBHOOK_LOCK = threading.Lock()


def configure_webhooks(app_url: str = "", shortcuts_url: str = "") -> None:
    """Point the controller at a companion app / iOS Shortcuts webhook."""
    with _WEBHOOK_LOCK:
        if app_url:
            _WEBHOOK_BASE["app"] = app_url.rstrip("/")
        if shortcuts_url:
            _WEBHOOK_BASE["shortcuts"] = shortcuts_url.rstrip("/")


def get_webhooks() -> dict:
    with _WEBHOOK_LOCK:
        return dict(_WEBHOOK_BASE)


@dataclass
class APNsPayload:
    """A validated APNs push envelope (device-token agnostic)."""

    alert_title: str
    alert_body: str
    badge: int = 0
    sound: str = "default"
    category: str = ""
    mutable_content: bool = True
    custom: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.custom = self.custom or {}

    def envelope(self, device_token: str) -> dict:
        """The final JSON body ready for an HTTP/2 push gateway."""
        aps: dict[str, Any] = {
            "alert": {"title": self.alert_title, "body": self.alert_body},
            "badge": self.badge,
            "sound": self.sound,
            "mutable-content": 1 if self.mutable_content else 0,
        }
        if self.category:
            aps["category"] = self.category
        payload: dict[str, Any] = {"aps": aps}
        payload.update(self.custom or {})
        return {"device_token": device_token, "payload": payload, "priority": 10, "push_type": "alert"}

    def shortcuts_body(self) -> dict:
        """The JSON body an iOS Shortcut webhook can consume directly."""
        return {
            "intent": "show_notification",
            "params": {"title": self.alert_title, "body": self.alert_body, "badge": self.badge},
        }


def _post(url: str, body: dict, timeout: float = 8.0) -> dict:
    """Best-effort JSON POST — returns {ok, error} and never raises."""
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
                return {"ok": True, "response": parsed}
            except Exception:
                return {"ok": True, "response": raw[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #

class MobileDeviceController:
    """High-level mobile/IoT command builders + delivery helpers."""

    # -- iOS Focus modes ------------------------------------------------------ #
    def focus_mode_payload(self, mode: str) -> dict:
        """Payload that sets an iOS Focus mode via Shortcuts/companion app."""
        mode = (mode or "").lower().replace(" ", "-")
        if mode not in _FOCUS_MODES:
            return {"ok": False, "error": f"unknown focus mode '{mode}'; use {sorted(_FOCUS_MODES)}"}
        return {
            "ok": True,
            "intent": "run_focus",
            "params": {"focus_mode": mode, "confirm": True},
        }

    # -- APNs push ------------------------------------------------------------- #
    def push_payload(
        self,
        title: str,
        body: str,
        device_token: str = "",
        badge: int = 0,
        category: str = "A3THER_COMMAND",
        custom: dict | None = None,
    ) -> dict:
        """Build an APNs envelope; deliver via webhook when a URL is set."""
        payload = APNsPayload(title, body, badge=badge, category=category, custom=custom or {})
        envelope = payload.envelope(device_token)
        webhooks = get_webhooks()
        result: dict[str, Any] = {"ok": True, "envelope": envelope, "delivered": False}
        if device_token and webhooks["app"]:
            result["delivery"] = _post(f"{webhooks['app']}/push", envelope)
            result["delivered"] = bool(result["delivery"].get("ok"))
        elif webhooks["shortcuts"]:
            result["delivery"] = _post(f"{webhooks['shortcuts']}/push", payload.shortcuts_body())
            result["delivered"] = bool(result["delivery"].get("ok"))
        log("MOBILE", f"push '{title}' → {'delivered' if result['delivered'] else 'queued (no webhook)'}")
        return result

    # -- Shortcuts webhook payloads -------------------------------------------- #
    def shortcuts_payload(self, intent: str, params: dict | None = None) -> dict:
        """Build + optionally deliver an iOS Shortcuts webhook body."""
        body = {"intent": intent, "params": params or {}}
        webhooks = get_webhooks()
        if webhooks["shortcuts"]:
            return _post(f"{webhooks['shortcuts']}/run", body)
        return {"ok": True, "queued": True, "body": body}

    # -- Profiling --------------------------------------------------------------- #
    def profile_state(self, profile: ClientProfile, rssi_dbm: int | None = None) -> MobileDeviceState:
        """Derive a MobileDeviceState from a connected profile (for the HUD)."""
        is_mobile = profile.kind in ("iphone", "ipad", "android")
        return MobileDeviceState(
            node_id=profile.node_id,
            kind=profile.kind,
            online=True,
            screen_on=None,
            focus_mode=None,
            network="wifi" if is_mobile else None,
            last_seen=time.time(),
            rssi_dbm=rssi_dbm,
        )

    # -- convenience command builders -------------------------------------------- #
    def command_for(
        self,
        command: str,
        kind: str,
        params: dict | None = None,
    ) -> dict:
        """Shape a DeviceCommand's params for a specific device kind.

        iPhone/iPad nodes get iOS-shaped payloads (focus/APNs/shortcuts);
        desktops/terminals get plain exec-style params; IoT gets raw MQTT-ish
        key/values. Unknown kinds pass params through untouched.
        """
        params = dict(params or {})
        if kind in ("iphone", "ipad"):
            if command == "system_sleep":
                params.setdefault("ios_action", "lock_screen")
            elif command == "unlock_interface":
                params.setdefault("ios_action", "open_shortcut")
            elif command == "push_notification":
                title = params.pop("title", "A.3.T.H.E.R.")
                body = params.pop("body", "Command from A.3.T.H.E.R.")
                push = self.push_payload(title, body)
                params["payload"] = push.get("envelope")
        elif kind == "android":
            # Map mesh commands onto the ADB allowlist so a broadcast can
            # drive a real Android device: tap/swipe/text/open/unlock/…
            adb_map = {
                "unlock_interface": "unlock",
                "system_sleep": "lock",
                "go_home": "home",
                "back": "back",
                "recent_apps": "recent",
                "launch_app": "open",
                "open_app": "open",
                "tap": "tap",
                "swipe": "swipe",
                "type_text": "text",
                "screenshot": "screenshot",
            }
            adb_action = adb_map.get(command)
            if adb_action:
                params.setdefault("android_action", adb_action)
            if command in ("open_app", "launch_app") and not params.get("package"):
                params.setdefault("package", params.pop("app", ""))
            if command == "push_notification":
                # Android notifications ride a companion bridge; keep the
                # payload intact so the mesh client can show it natively.
                params.setdefault("android_notification", True)
        return params


# --------------------------------------------------------------------------- #
# Singleton
# --------------------------------------------------------------------------- #

_CONTROLLER: MobileDeviceController | None = None
_CONTROLLER_LOCK = threading.Lock()


def get_mobile_controller() -> MobileDeviceController:
    global _CONTROLLER
    if _CONTROLLER is None:
        with _CONTROLLER_LOCK:
            if _CONTROLLER is None:
                _CONTROLLER = MobileDeviceController()
    return _CONTROLLER
