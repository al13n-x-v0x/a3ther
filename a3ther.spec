# -*- mode: python ; coding: utf-8 -*-
"""
a3ther.spec — PyInstaller spec for the A.3.T.H.E.R. desktop exe.

Build (from the repo root, with pyinstaller installed):

    pip install pyinstaller
    pyinstaller a3ther.spec --noconfirm

Output:  dist/A3THER/A3THER.exe  (a folder — onedir mode; starts fast,
no temp-file extraction). A single-file build is possible with
``pyinstaller a3ther.spec --noconfirm --onefile`` — but onedir is the
recommended default: it boots faster and is far more reliable with the
FastAPI/uvicorn stack.

The exe needs no Python and no pip: every imported dependency is bundled
inside dist/A3THER. The only runtime requirement is a normal Windows PC
(the HUD opens in the default browser).
"""

block_cipher = None

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs  # noqa: E402

# ---------------------------------------------------------------------------
# Vosk ships its native DLLs (libvosk.dll + gcc runtime) inside the Python
# package — PyInstaller's import hook does NOT copy them, so the bundled
# STT silently fails unless we collect them explicitly.
# ---------------------------------------------------------------------------
vosk_binaries = collect_dynamic_libs("vosk")

# ---------------------------------------------------------------------------
# imageio-ffmpeg embeds a static ffmpeg.exe inside the package directory —
# PyInstaller must ship it or the video editor cannot render in the exe.
# ---------------------------------------------------------------------------
imageio_ffmpeg_datas = collect_data_files("imageio_ffmpeg")

# ---------------------------------------------------------------------------
# Static assets that must ship inside the bundle:
#   Frontend/  →  the HUD (index.html / style.css / script.js / phone.html)
#                AND the Extensions dashboard (plugins.html/js/css).
#   config/api_keys.template.json  →  an EMPTY template, copied to
#   config/api_keys.json in the bundle so old readers never crash.
#
#   ⚠ NEVER bundle the live config/api_keys.json — it holds real API keys.
#   Real keys live in %LOCALAPPDATA%/A3THER at runtime (see config/paths.py).
# ---------------------------------------------------------------------------
datas = [
    ("Frontend/index.html", "Frontend"),
    ("Frontend/style.css", "Frontend"),
    ("Frontend/script.js", "Frontend"),
    ("Frontend/phone.html", "Frontend"),
    ("Frontend/plugins.html", "Frontend"),
    ("Frontend/plugins.js", "Frontend"),
    ("Frontend/plugins.css", "Frontend"),
    ("Frontend/hub.html", "Frontend"),
    ("Frontend/hub.js", "Frontend"),
    ("Frontend/assets/logo.png", "Frontend/assets"),
    ("config/api_keys.template.json", "config"),
]

# ---------------------------------------------------------------------------
# uvicorn + friends are imported dynamically and PyInstaller can miss them.
# ---------------------------------------------------------------------------
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.middleware",
    "uvicorn.middleware.message_logger",
    "uvicorn.middleware.proxy_headers",
    "uvicorn.middleware.wsgi",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.client",
    "websockets.legacy.server",
    # yt-dlp (internet clips) — dynamically loads its extractors.
    "yt_dlp",
    "yt_dlp.extractor",
    "yt_dlp.extractor.youtube",
    "yt_dlp.downloader",
    "yt_dlp.postprocessor",
    "yt_dlp.postprocessor.ffmpeg",
    "yt_dlp.utils",
    "yt_dlp.compat",
    "yt_dlp.networking",
    "yt_dlp.networking.requests",
    "yt_dlp.cookies",
    # Bluetooth LE controller — lazily imported by the sync API.
    "sync.ble_controller",
    "bleak",
    "bleak.backends",
    "bleak.backends.winrt",
    "bleak.backends.winrt.client",
    "bleak.backends.winrt.scanner",
    # Native HUD window (core/desktop.py) — pywebview + pythonnet backend.
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "clr",
    "pythonnet",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=vosk_binaries,
    datas=datas + imageio_ffmpeg_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy ML stacks — only needed lazily for Kokoro/Whisper/embeddings,
        # which degrade gracefully without them. Excluding keeps the exe ~90 MB
        # instead of ~800 MB. Edge TTS (the default voice) needs none of this.
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "faster_whisper",
        "sentence_transformers",
        "onnxruntime",
        "tokenizers",
        "PyQt6",
        "PySide6",
        "tkinter",
        "playwright",
        "cv2",
        "matplotlib",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="A3THER",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Windowless: no console window on launch. main.py redirects stdout/stderr
    # to %LOCALAPPDATA%/A3THER/logs/a3ther.log, so all prints still land in
    # the terminal log (and are mirrored to GET /api/engine/status).
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="A3THER",
)
