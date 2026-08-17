"""
google_drive — A3THER's Google Drive bridge.

Same proven OAuth pattern as ``youtube_upload``: you provide the Google Cloud
``client_secrets.json`` (the *same* desktop OAuth client that YouTube uses —
the consent screen is already in production, so the extra Drive scopes are
granted in the same one-click sign-in), A3THER opens the browser, you approve,
and the refresh token is stored locally (``%LOCALAPPDATA%\\A3THER\\drive\\``).

Capabilities
------------
- **Connect** — one-click browser sign-in (account chooser always shown), with
  a copy-paste manual fallback.
- **List** — browse your Drive (names, types, sizes, modified times).
- **Upload** — push any local file (rendered edits, screenshots, backups).
- **Download** — pull a Drive file back to a local folder.
- **Backup** — mirror a whole local folder to a dated ``A3THER Backup`` Drive
  folder on a background thread, with progress polling.

Scopes are minimal: ``drive.file`` (read/write only on files A3THER itself
creates) + ``drive.metadata.readonly`` (so "what's in my drive" can list
names/metadata without seeing file contents of unrelated files).

Everything degrades honestly: no client_secrets → clear setup steps; not
linked → "link it first"; network error → the error string, never a fake
success.
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

LOGGER = logging.getLogger("a3ther.drive")

# --------------------------------------------------------------------------- #
# Local state — tokens live in the A3THER data dir, never in the repo.
# --------------------------------------------------------------------------- #
_TOKEN_FILE = "drive_token.json"

# One running backup job (thread-safe via the GIL + a lock).
_BACKUP: dict = {
    "running": False,
    "state": "idle",          # idle | running | done | error
    "job_id": "",
    "source": "",
    "folder_name": "",
    "done": 0,
    "total": 0,
    "bytes": 0,
    "error": "",
}
_BACKUP_LOCK = threading.Lock()


def _data_dir() -> Path:
    try:
        from config.paths import data_path

        base = data_path("drive")
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:  # noqa: BLE001
        base = Path.home() / "A3THER" / "drive"
        base.mkdir(parents=True, exist_ok=True)
        return base


def _client_secrets_path() -> Path:
    """Locate the shared Google OAuth client_secrets.json.

    Search order (frozen-exe safe — never relies on the CWD alone):
      1. ``A3THER_DRIVE_CLIENT_SECRETS`` env override, then the YouTube one.
      2. App-data copy (``%LOCALAPPDATA%\\A3THER\\config\\...``) —
         ``data_path()`` lazily migrates a repo copy in dev mode.
      3. A copy sitting next to the frozen exe.
      4. Repo/working copy.
    """
    for var in ("A3THER_DRIVE_CLIENT_SECRETS", "A3THER_YT_CLIENT_SECRETS"):
        override = os.environ.get(var)
        if override and Path(override).exists():
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


def _load_token() -> dict:
    path = _data_dir() / _TOKEN_FILE
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_token(token: dict) -> None:
    (_data_dir() / _TOKEN_FILE).write_text(json.dumps(token, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# OAuth connect — the proven browser flow from youtube_upload.
# --------------------------------------------------------------------------- #
_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

# State of the one-click browser sign-in (run_local_server blocks until the
# loopback redirect lands, so it runs on a background thread).
_BROWSER_AUTH: dict = {"state": "idle", "error": "", "started_at": 0}


def connect_status() -> dict:
    """Is Google Drive linked? (token + client_secrets present)."""
    secrets = _client_secrets_path()
    token = _load_token()
    return {
        "linked": bool(token.get("refresh_token") or token.get("token")),
        "has_client_secrets": secrets.exists(),
        "client_secrets_path": str(secrets),
        "account": token.get("account") or None,
        "setup_needed": not secrets.exists(),
        "auth_in_progress": _BROWSER_AUTH.get("state") == "running",
        "auth_state": _BROWSER_AUTH.get("state", "idle"),
        "auth_error": _BROWSER_AUTH.get("error", ""),
        "setup_steps": (
            "1) Go to console.cloud.google.com → your 'a3ther' project → "
            "enable the 'Google Drive API'. "
            "2) Your existing YouTube OAuth client (config/client_secrets.json) "
            "already covers Drive — no new credentials needed."
        ) if not secrets.exists() else None,
    }


def _flow():
    """InstalledAppFlow with the registered redirect URI pinned.

    google-auth-oauthlib >= 1.x does NOT populate ``flow.redirect_uri`` from
    the client_secrets ``redirect_uris`` list — leaving it None makes Google
    reject the URL with "Error 400: invalid_request — Missing required
    parameter: redirect_uri". Pinning it to the registered loopback URI fixes
    both the browser and manual flows (same fix as youtube_upload).
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(_client_secrets_path()), _SCOPES
    )
    uris = flow.client_config.get("installed", {}).get("redirect_uris") or ["http://localhost"]
    flow.redirect_uri = uris[0]
    return flow


def get_auth_url() -> dict:
    """Return the consent URL to open in the browser (manual fallback)."""
    secrets = _client_secrets_path()
    if not secrets.exists():
        return {"ok": False, "error": connect_status()["setup_steps"]}
    try:
        flow = _flow()
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        return {"ok": True, "url": url, "note": "open the URL, approve, then paste the code back"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def browser_auth_start() -> dict:
    """Start the one-click Google sign-in (opens the default browser)."""
    secrets = _client_secrets_path()
    if not secrets.exists():
        return {"ok": False, "error": connect_status()["setup_steps"]}
    with threading.Lock():
        if _BROWSER_AUTH.get("state") == "running":
            return {"ok": True, "started": True, "note": "sign-in already in progress"}
        _BROWSER_AUTH.update({"state": "running", "error": "", "started_at": time.time()})
    threading.Thread(target=_browser_auth_worker, daemon=True, name="drive-browser-auth").start()
    return {"ok": True, "started": True, "note": "browser opened — approve the Google sign-in"}


def _browser_auth_worker() -> None:
    try:
        flow = _flow()
        # port=0 → random free loopback port; prompt="select_account consent"
        # always shows the account chooser so the user picks the right Google
        # account, then re-consents.
        creds = flow.run_local_server(port=0, prompt="select_account consent", open_browser=True)
        result = _save_creds(creds)
        _BROWSER_AUTH.update({"state": "done", "error": ""})
        LOGGER.info("Google Drive linked via browser flow: %s", result.get("account"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Google Drive browser auth failed")
        _BROWSER_AUTH.update({"state": "error", "error": f"{type(exc).__name__}: {exc}"})


def exchange_code(code: str) -> dict:
    """Exchange the consent-page code for a stored refresh token (manual path)."""
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


def _save_creds(creds) -> dict:
    """Persist the credential object + best-effort account email."""
    token = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    try:
        about = _service(creds).about().get(fields="user(emailAddress)").execute()
        user = about.get("user") or {}
        if user.get("emailAddress"):
            token["account"] = user["emailAddress"]
    except Exception as exc:  # noqa: BLE001
        token["account_error"] = f"{type(exc).__name__}: {exc}"
    _save_token(token)
    return {"ok": True, "linked": True, "account": token.get("account"), "account_error": token.get("account_error")}


def _service(creds=None):
    """Lazy Drive v3 service; refreshes + persists the token when expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if creds is None:
        token = _load_token()
        if not token:
            raise RuntimeError("Google Drive not linked yet")
        creds = Credentials(
            token=token.get("token"),
            refresh_token=token.get("refresh_token"),
            token_uri=token.get("token_uri") or "https://oauth2.googleapis.com/token",
            client_id=token.get("client_id"),
            client_secret=token.get("client_secret"),
        )
    if creds.expired:
        creds.refresh(Request())
        token = _load_token()
        token["token"] = creds.token
        _save_token(token)
    return build("drive", "v3", credentials=creds)


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
def list_files(query: str = "", limit: int = 50) -> dict:
    """List files (names, types, sizes, modified times). Minimal scopes only."""
    try:
        service = _service()
        q = "trashed=false"
        if query.strip():
            q += f" and name contains '{query.strip().replace(chr(39), '')}'"
        resp = service.files().list(
            q=q,
            pageSize=min(max(int(limit), 1), 100),
            fields="files(id,name,mimeType,size,modifiedTime),nextPageToken",
            orderBy="modifiedTime desc",
        ).execute()
        files = []
        for f in resp.get("files", []):
            files.append(
                {
                    "id": f["id"],
                    "name": f.get("name", "untitled"),
                    "mime": f.get("mimeType", ""),
                    "folder": f.get("mimeType") == "application/vnd.google-apps.folder",
                    "size": f.get("size"),
                    "size_mb": round(int(f.get("size") or 0) / 1_048_576, 2),
                    "modified": f.get("modifiedTime", ""),
                }
            )
        return {"ok": True, "files": files, "count": len(files)}
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Drive list failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _resolve_path(path: str) -> Path | None:
    """Resolve a local path (expanduser) or a bare filename in the data dir."""
    p = Path(path or "").expanduser()
    if p.is_file():
        return p
    # Bare name → look in a couple of obvious A3THER output locations.
    for base in (_data_dir().parent / "videos", Path.home() / "Videos" / "A3THER"):
        cand = base / (path or "")
        if cand.is_file():
            return cand
    return None


def upload_file(path: str, folder_id: str | None = None, name: str | None = None) -> dict:
    """Upload one local file to Drive (resumable, 8 MB chunks)."""
    local = _resolve_path(path)
    if local is None:
        return {"ok": False, "error": f"file not found: {path}"}
    if local.stat().st_size < 1:
        return {"ok": False, "error": "file is empty"}
    try:
        from googleapiclient.http import MediaFileUpload

        service = _service()
        file_name = name or local.name
        body = {"name": file_name}
        if folder_id:
            body["parents"] = [folder_id]
        media = MediaFileUpload(str(local), chunksize=8 * 1024 * 1024, resumable=True)
        req = service.files().create(body=body, media_body=media, fields="id,name,mimeType,size")
        result = req.execute()
        return {
            "ok": True,
            "file": {
                "id": result.get("id"),
                "name": result.get("name"),
                "mime": result.get("mimeType", ""),
                "size": result.get("size"),
            },
            "local": str(local),
        }
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Drive upload failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def download_file(file_id: str, dest_dir: str | None = None) -> dict:
    """Download a Drive file (by id) into a local folder (default: ~/Downloads/A3THER)."""
    if not file_id.strip():
        return {"ok": False, "error": "file id is required"}
    try:
        service = _service()
        meta = service.files().get(fileId=file_id, fields="name,size").execute()
        dest = Path(dest_dir or Path.home() / "Downloads" / "A3THER").expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / meta.get("name", file_id)
        # No resumable download here — files are fetched with get_media which
        # streams in 64 KB chunks; large files use mediaIoDownload below.
        from googleapiclient.http import MediaIoBaseDownload

        request = service.files().get_media(fileId=file_id)
        fh = open(out, "wb")  # noqa: SIM115 — MediaIoBaseDownload needs a file-like
        try:
            downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        finally:
            fh.close()
        return {"ok": True, "name": meta.get("name"), "size": meta.get("size"), "dest": str(out)}
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Drive download failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# Backup — mirror a local folder into a dated Drive folder on a thread.
# --------------------------------------------------------------------------- #
def backup_status() -> dict:
    with _BACKUP_LOCK:
        return dict(_BACKUP)


def backup_folder(source_dir: str, folder_name: str | None = None) -> dict:
    """Start a threaded backup of ``source_dir`` into ``A3THER Backup <date>``."""
    src = Path(source_dir or "").expanduser()
    if not src.is_dir():
        return {"ok": False, "error": f"not a folder: {source_dir}"}
    files = [p for p in src.rglob("*") if p.is_file()]
    if not files:
        return {"ok": False, "error": f"'{src.name}' is empty — nothing to back up"}
    if not _load_token():
        return {"ok": False, "error": "Google Drive isn't linked — connect it first"}
    with _BACKUP_LOCK:
        if _BACKUP["running"]:
            return {"ok": False, "error": "a backup is already running"}
        job_id = uuid.uuid4().hex[:8]
        _BACKUP.update(
            {
                "running": True,
                "state": "running",
                "job_id": job_id,
                "source": str(src),
                "folder_name": folder_name or f"A3THER Backup {time.strftime('%Y-%m-%d')}",
                "done": 0,
                "total": len(files),
                "bytes": 0,
                "error": "",
            }
        )
    threading.Thread(target=_backup_worker, args=(src,), daemon=True, name="drive-backup").start()
    return {"ok": True, "started": True, "files": len(files), "job_id": _BACKUP["job_id"]}


def _find_or_create_folder(service, folder_name: str) -> str:
    """Return the id of ``folder_name`` in Drive, creating it if missing."""
    resp = service.files().list(
        q=(
            f"name='{folder_name.replace(chr(39), '')}' and "
            "mimeType='application/vnd.google-apps.folder' and trashed=false"
        ),
        fields="files(id)",
        pageSize=10,
    ).execute()
    items = resp.get("files") or []
    if items:
        return items[0]["id"]
    folder = (
        service.files()
        .create(
            body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        )
        .execute()
    )
    return folder["id"]


def _backup_worker(src: Path) -> None:
    from googleapiclient.http import MediaFileUpload

    files = [p for p in src.rglob("*") if p.is_file()]
    try:
        service = _service()
        folder_id = _find_or_create_folder(service, _BACKUP["folder_name"])
        for idx, path in enumerate(files, start=1):
            rel = path.relative_to(src)
            # Drive names can't contain '/'; flatten the tree with a readable
            # separator so the backup stays fully restorable by name.
            drive_name = str(rel).replace(os.sep, " › ")
            media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True)
            req = service.files().create(
                body={"name": drive_name, "parents": [folder_id]},
                media_body=media,
                fields="id",
            )
            req.execute()
            with _BACKUP_LOCK:
                _BACKUP["done"] = idx
                _BACKUP["bytes"] += path.stat().st_size
        with _BACKUP_LOCK:
            _BACKUP.update({"running": False, "state": "done", "error": ""})
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Drive backup failed")
        with _BACKUP_LOCK:
            _BACKUP.update({"running": False, "state": "error", "error": f"{type(exc).__name__}: {exc}"})
