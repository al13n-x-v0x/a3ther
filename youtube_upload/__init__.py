"""
youtube_upload — connect your YouTube, publish edits, grow subs.

Pipeline
--------
1. **Connect** — you provide a Google Cloud ``client_secrets.json`` (YouTube
   Data API v3 enabled). A3THER opens the consent page, you approve, and
   the refresh token is stored locally (``%LOCALAPPDATA%\\A3THER\\yt_token.json``).
2. **Propose** — pick a rendered edit. The LLM gateway writes a clickable
   title, a searchable description, and a full tag set in one shot, then
   the video waits in an **approval queue**.
3. **Approve → upload** — you review the card (title, description, tags,
   thumbnail frame) and approve. A3THER uploads with those tags.
4. **Auto-reply bot** — after publishing, a background loop polls the video
   for new comments and auto-replies (pinned intro line + LLM-generated
   reply), which is what actually drives subscribers up.

Everything degrades honestly: no client_secrets → clear setup steps; no
network → error; not approved → nothing is ever uploaded.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path

LOGGER = logging.getLogger("a3ther.youtube")

# --------------------------------------------------------------------------- #
# Local state (tokens, approvals) — stored with the rest of A3THER's data.
# --------------------------------------------------------------------------- #
_TOKEN_FILE = "yt_token.json"
_APPROVALS_FILE = "yt_approvals.json"


def _data_dir() -> Path:
    try:
        from config.paths import data_path

        base = data_path("youtube")
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:  # noqa: BLE001
        base = Path.home() / "A3THER" / "youtube"
        base.mkdir(parents=True, exist_ok=True)
        return base


def _client_secrets_path() -> Path:
    """Locate the Google OAuth client_secrets.json.

    Search order (frozen-exe safe — never relies on the CWD alone):
      1. ``A3THER_YT_CLIENT_SECRETS`` env override.
      2. App-data copy (``%LOCALAPPDATA%\\A3THER\\config\\...``) —
         ``data_path()`` lazily migrates a repo copy in dev mode.
      3. A copy sitting next to the frozen exe (``dist/A3THER/config/``) —
         copied into the app-data dir once so later runs are stable.
      4. Repo/working copy (dev mode, or the exe launched from the repo root).
    """
    override = os.environ.get("A3THER_YT_CLIENT_SECRETS")
    if override:
        return Path(override)
    try:
        from config.paths import data_path

        target = data_path("config/client_secrets.json")
        if target.exists():
            return target
        if getattr(sys, "frozen", False):
            exe_side = Path(sys.executable).resolve().parent / "config" / "client_secrets.json"
            if exe_side.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(exe_side.read_bytes())
                return target
    except Exception:  # noqa: BLE001
        pass
    return Path("config/client_secrets.json").resolve()


def _load_json(name: str) -> dict:
    path = _data_dir() / name
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_json(name: str, data: dict) -> None:
    (_data_dir() / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# OAuth connect
# --------------------------------------------------------------------------- #
_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# State of the one-click browser sign-in (run_local_server runs in a thread
# because it blocks until Google redirects back to the loopback port).
_BROWSER_AUTH: dict = {"state": "idle", "error": "", "started_at": 0}


def connect_status() -> dict:
    """Is YouTube linked? (token present + client_secrets present)."""
    secrets = _client_secrets_path()
    token = _load_json(_TOKEN_FILE)
    return {
        "linked": bool(token.get("refresh_token") or token.get("token")),
        "has_client_secrets": secrets.exists(),
        "client_secrets_path": str(secrets),
        "channel": token.get("channel_title") or None,
        "setup_needed": not secrets.exists(),
        # One-click browser sign-in state ("idle" | "running" | "done" | "error")
        "auth_in_progress": _BROWSER_AUTH.get("state") == "running",
        "auth_state": _BROWSER_AUTH.get("state", "idle"),
        "auth_error": _BROWSER_AUTH.get("error", ""),
        "setup_steps": (
            "1) Go to console.cloud.google.com → create a project → enable "
            "'YouTube Data API v3'. "
            "2) Create OAuth credentials → download JSON → save it as "
            "config/client_secrets.json (or set A3THER_YT_CLIENT_SECRETS)."
        ) if not secrets.exists() else None,
    }


def _flow():
    """Build an InstalledAppFlow with the registered redirect URI applied.

    google-auth-oauthlib >= 1.x does NOT populate ``flow.redirect_uri`` from
    the client_secrets ``redirect_uris`` list — it stays None unless passed
    explicitly, and ``authorization_url()`` then omits the parameter entirely,
    which Google rejects with "Error 400: invalid_request — Missing required
    parameter: redirect_uri". Pinning it to the registered loopback URI fixes
    both the manual and browser flows.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(_client_secrets_path()), _SCOPES
    )
    uris = flow.client_config.get("installed", {}).get("redirect_uris") or ["http://localhost"]
    flow.redirect_uri = uris[0]
    return flow


def get_auth_url() -> dict:
    """Return the consent URL to open in the browser (no approval needed)."""
    secrets = _client_secrets_path()
    if not secrets.exists():
        return {"ok": False, "error": connect_status()["setup_steps"]}
    try:
        flow = _flow()
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        return {"ok": True, "url": url, "note": "open the URL, approve, then paste the code back"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _save_creds(creds) -> dict:
    """Persist an OAuth credential object and fetch the channel name."""
    token = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    try:
        service = _service(creds)
        resp = service.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items") or []
        if items:
            token["channel_title"] = items[0]["snippet"]["title"]
        else:
            token["channel_error"] = "no channel on this account (or no channel selected)"
    except Exception as exc:  # noqa: BLE001
        token["channel_error"] = f"{type(exc).__name__}: {exc}"
    _save_json(_TOKEN_FILE, token)
    return {"ok": True, "linked": True, "channel": token.get("channel_title"), "channel_error": token.get("channel_error")}


def browser_auth_start() -> dict:
    """Start the one-click Google sign-in.

    Runs ``InstalledAppFlow.run_local_server`` on a background thread: it opens
    the user's default web browser at the Google consent page, waits on a local
    loopback port for the redirect, exchanges the code automatically, and stores
    the refresh token — no copy/paste required. Callers poll ``connect_status``
    until ``auth_state`` is "done" or "error".
    """
    secrets = _client_secrets_path()
    if not secrets.exists():
        return {"ok": False, "error": connect_status()["setup_steps"]}
    with threading.Lock():
        if _BROWSER_AUTH.get("state") == "running":
            return {"ok": True, "started": True, "note": "sign-in already in progress"}
        _BROWSER_AUTH.update({"state": "running", "error": "", "started_at": time.time()})
    threading.Thread(
        target=_browser_auth_worker, daemon=True, name="yt-browser-auth"
    ).start()
    return {"ok": True, "started": True, "note": "browser opened — approve the Google sign-in"}


def _browser_auth_worker() -> None:
    """Blocking worker: run_local_server opens the browser + loopback listener."""
    try:
        flow = _flow()
        # port=0 → random free loopback port. prompt="select_account consent"
        # always shows the account chooser so the user picks the right Google
        # account (the one owning the channel they want), then re-consents.
        creds = flow.run_local_server(port=0, prompt="select_account consent", open_browser=True)
        result = _save_creds(creds)
        _BROWSER_AUTH.update({"state": "done", "error": ""})
        LOGGER.info("YouTube linked via browser flow: %s", result.get("channel"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("YouTube browser auth failed")
        _BROWSER_AUTH.update({"state": "error", "error": f"{type(exc).__name__}: {exc}"})


def exchange_code(code: str) -> dict:
    """Exchange the consent-page code for a stored refresh token (manual fallback)."""
    secrets = _client_secrets_path()
    if not secrets.exists():
        return {"ok": False, "error": connect_status()["setup_steps"]}
    try:
        flow = _flow()
        flow.fetch_token(code=code.strip())
        return _save_creds(flow.credentials)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"code exchange failed: {type(exc).__name__}: {exc}"}


def disconnect() -> dict:
    try:
        (_data_dir() / _TOKEN_FILE).unlink(missing_ok=True)
        return {"ok": True, "linked": False}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _service(creds=None):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if creds is None:
        token = _load_json(_TOKEN_FILE)
        if not token:
            raise RuntimeError("YouTube not linked yet")
        creds = Credentials(
            token=token.get("token"),
            refresh_token=token.get("refresh_token"),
            token_uri=token.get("token_uri") or "https://oauth2.googleapis.com/token",
            client_id=token.get("client_id"),
            client_secret=token.get("client_secret"),
        )
    if creds.expired:
        creds.refresh(Request())
        # Persist the refreshed token.
        token = _load_json(_TOKEN_FILE)
        token.update({"token": creds.token})
        _save_json(_TOKEN_FILE, token)
    return build("youtube", "v3", credentials=creds)


# --------------------------------------------------------------------------- #
# AI title / description / tags
# --------------------------------------------------------------------------- #
def _ai_metadata(video_name: str) -> dict:
    """One-shot LLM generation of clickable metadata (degrades gracefully)."""
    try:
        from gateway.router import AllProvidersFailed, get_gateway

        gateway = get_gateway()
        if not gateway.any_available():
            raise RuntimeError("no LLM configured")
        prompt = (
            f"Write YouTube metadata for an edit video named '{video_name}'. "
            "Return ONLY JSON: {\"title\": \"<clickable title, <=70 chars, "
            "no clickbait lies>\", \"description\": \"<2-4 lines with hashtags>\", "
            "\"tags\": [\"<5-8 tags>\"]}"
        )
        raw = gateway.complete_text(prompt, max_tokens=400, timeout=40)
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if not m:
            raise RuntimeError("LLM did not return JSON")
        data = json.loads(m.group(0))
        title = str(data.get("title") or video_name)[:70]
        desc = str(data.get("description") or f"An A3THER edit — {video_name}")
        tags = [str(t).strip() for t in (data.get("tags") or [])][:10]
        return {"title": title, "description": desc, "tags": tags}
    except Exception:  # noqa: BLE001
        return {
            "title": video_name[:70],
            "description": f"An A3THER edit — {video_name}\n#a3ther #edit #shorts",
            "tags": ["a3ther", "edit", "shorts", "ai", "tiktok"],
        }


# --------------------------------------------------------------------------- #
# Approval queue
# --------------------------------------------------------------------------- #
def _resolve_video(video_path: str) -> Path | None:
    """Resolve a video from a filesystem path OR a rendered filename."""
    path = Path(video_path or "").expanduser()
    if path.is_file():
        return path
    # Bare filename → look inside the engine's videos dir (path-traversal safe).
    try:
        from video_editor.engine import get_video_path

        return get_video_path(path.name)
    except Exception:  # noqa: BLE001
        return None


def propose_upload(video_path: str, title: str | None = None) -> dict:
    """Stage a rendered video for approval with AI-generated metadata."""
    path = _resolve_video(video_path)
    if path is None:
        return {"ok": False, "error": f"video not found: {video_path}"}
    if path.stat().st_size < 1024:
        return {"ok": False, "error": "video file is empty"}

    meta = _ai_metadata(path.stem) if not title else {"title": title, "description": "", "tags": []}
    approval = {
        "id": uuid.uuid4().hex[:8],
        "video_path": str(path),
        "video_name": path.name,
        "size_mb": round(path.stat().st_size / 1_048_576, 2),
        "status": "pending",            # pending | approved | uploaded | rejected | failed
        "title": (title or meta["title"]).strip()[:70],
        "description": meta["description"],
        "tags": meta["tags"],
        "video_id": None,
        "error": "",
        "created": time.time(),
    }
    approvals = _load_json(_APPROVALS_FILE)
    approvals[approval["id"]] = approval
    _save_json(_APPROVALS_FILE, approvals)
    return {"ok": True, "approval": approval}


def approvals() -> list[dict]:
    data = _load_json(_APPROVALS_FILE)
    items = list(data.values())
    items.sort(key=lambda a: a.get("created", 0), reverse=True)
    return items


def approve(approval_id: str, edits: dict | None = None) -> dict:
    """Approve a pending upload (optionally editing title/desc/tags)."""
    approvals_data = _load_json(_APPROVALS_FILE)
    item = approvals_data.get(approval_id)
    if not item:
        return {"ok": False, "error": f"no approval '{approval_id}'"}
    if item["status"] not in ("pending", "failed"):
        return {"ok": False, "error": f"approval is already '{item['status']}'"}
    if edits:
        if edits.get("title"):
            item["title"] = str(edits["title"])[:70]
        if edits.get("description"):
            item["description"] = str(edits["description"])
        if edits.get("tags"):
            item["tags"] = [str(t).strip() for t in edits["tags"] if str(t).strip()][:10]
    item["status"] = "approved"
    approvals_data[approval_id] = item
    _save_json(_APPROVALS_FILE, approvals_data)
    # Upload on a background thread so the API returns instantly.
    threading.Thread(target=_upload_worker, args=(approval_id,), daemon=True, name="yt-upload").start()
    return {"ok": True, "approval": item, "note": "upload started in the background"}


def reject(approval_id: str) -> dict:
    approvals_data = _load_json(_APPROVALS_FILE)
    item = approvals_data.get(approval_id)
    if not item:
        return {"ok": False, "error": f"no approval '{approval_id}'"}
    item["status"] = "rejected"
    approvals_data[approval_id] = item
    _save_json(_APPROVALS_FILE, approvals_data)
    return {"ok": True, "approval": item}


def _upload_worker(approval_id: str) -> None:
    approvals_data = _load_json(_APPROVALS_FILE)
    item = approvals_data.get(approval_id)
    if not item:
        return
    try:
        service = _service()
        body = {
            "snippet": {
                "title": item["title"],
                "description": item["description"],
                "tags": item["tags"],
                "categoryId": "22",  # People & Blogs
            },
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        media = _media_upload(item["video_path"])
        resp = service.videos().insert(part="snippet,status", body=body, media_body=media).execute()
        item["video_id"] = resp.get("id")
        item["status"] = "uploaded"
        item["error"] = ""
        item["url"] = f"https://youtu.be/{resp.get('id')}"
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("YouTube upload failed")
        item["status"] = "failed"
        item["error"] = f"{type(exc).__name__}: {exc}"
    approvals_data[approval_id] = item
    _save_json(_APPROVALS_FILE, approvals_data)


def _media_upload(video_path: str):
    from googleapiclient.http import MediaFileUpload

    return MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True)


# --------------------------------------------------------------------------- #
# Auto-reply bot — the "grow subs" loop
# --------------------------------------------------------------------------- #
_BOT_RUNNING = False
_BOT_LOCK = threading.Lock()


def bot_start() -> dict:
    """Start the comment auto-reply loop for all published videos."""
    global _BOT_RUNNING
    with _BOT_LOCK:
        if _BOT_RUNNING:
            return {"ok": True, "running": True}
        if not _load_json(_TOKEN_FILE):
            return {"ok": False, "error": "YouTube not linked"}
        _BOT_RUNNING = True
    threading.Thread(target=_bot_loop, daemon=True, name="yt-reply-bot").start()
    return {"ok": True, "running": True}


def bot_stop() -> dict:
    global _BOT_RUNNING
    _BOT_RUNNING = False
    return {"ok": True, "running": False}


def bot_status() -> dict:
    return {"running": _BOT_RUNNING, "replied": _load_json(_TOKEN_FILE).get("bot_replied", 0)}


def _bot_loop() -> None:
    replied: set[str] = set()
    while _BOT_RUNNING:
        try:
            service = _service()
            approvals_data = _load_json(_APPROVALS_FILE)
            video_ids = [a["video_id"] for a in approvals_data.values() if a.get("video_id")]
            for vid in video_ids:
                try:
                    comments = service.commentThreads().list(
                        part="snippet", videoId=vid, maxResults=10,
                        textFormat="plainText",
                    ).execute()
                    for thread in comments.get("items", []):
                        comment = thread["snippet"]["topLevelComment"]["snippet"]
                        cid = comment["id"]
                        if cid in replied:
                            continue
                        text = comment.get("textDisplay", "")
                        if _should_skip_reply(text):
                            replied.add(cid)
                            continue
                        reply = _ai_reply(text)
                        try:
                            service.comments().insert(
                                part="snippet",
                                body={
                                    "snippet": {
                                        "videoId": vid,
                                        "textOriginal": reply,
                                        "parentId": cid,
                                    }
                                },
                            ).execute()
                        except Exception as exc:  # noqa: BLE001
                            LOGGER.warning("reply failed: %s", exc)
                        replied.add(cid)
                except Exception:  # noqa: BLE001
                    continue  # comments may be disabled — skip
            token = _load_json(_TOKEN_FILE)
            token["bot_replied"] = len(replied)
            _save_json(_TOKEN_FILE, token)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("YT bot cycle failed: %s", exc)
        for _ in range(60):
            if not _BOT_RUNNING:
                return
            time.sleep(5)


def _should_skip_reply(text: str) -> bool:
    lowered = (text or "").lower()
    return any(k in lowered for k in ("subscribe", "sub to me", "follow", "check out my"))


def _ai_reply(comment: str) -> str:
    try:
        from gateway.router import get_gateway

        gateway = get_gateway()
        if gateway.any_available():
            return gateway.complete_text(
                f"Reply to this YouTube comment as A3THER, a friendly AI editor "
                f"(1 sentence, warm, no emoji spam): \"{comment[:300]}\"",
                max_tokens=60, timeout=30,
            ).strip()[:280]
    except Exception:  # noqa: BLE001
        pass
    return "Appreciate you watching! 🔥"
