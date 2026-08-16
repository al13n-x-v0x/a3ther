# 🤖 A.3.T.H.E.R.

### Adaptive 3rd-generation Technology for Heuristic Execution & Research

> **Designed & Developed by AL13N Industries** · Windows · Free & Open Source

---

## 🌌 What is A.3.T.H.E.R.?

A.3.T.H.E.R. is a desktop AI command center for Windows. It's not a chatbot in a browser — it's a **full agent runtime** with a cinematic HUD, a real voice pipeline, global hotkeys, a system tray, a Claude-style quick popup, long-term memory, plugin and gateway ecosystems, and even phone remote control.

It runs **fully local first** (Ollama), but can use OpenAI, DeepSeek, Gemini, Groq, or Anthropic.

---

## ✨ Feature Highlights

| Area | What you get |
|------|--------------|
| **HUD** | Futuristic glassmorphism dashboard — system stats, live console, AI core, themes (cyan/gold/red/…), mode chip |
| **Modes** | `ai`, `humanoid`, `gaming`, `dev`, `chill`, `focus`, `creative`, `offline` — each with its own persona, accent color, icon and TTS voice |
| **Voice** | Real speech-to-text (Vosk, offline) + natural text-to-speech (Edge TTS). Say *"hey aether"* |
| **Hotkeys** | Global `Alt+F1…F8` — summon HUD, voice, screenshot, cycle mode, lock PC, status, hub, quick popup. All rebindable, work in any app |
| **Quick Popup** | Claude macOS-style floating panel (default `Alt+F8`) near your cursor — logo, mode, one-tap actions |
| **System Tray** | Background presence: status tooltip, Summon/Quit without hotkeys |
| **Start with Windows** | Real per-user registry `Run` key, installed from Settings |
| **Memory** | JSON-backed long-term memory + session summaries + keyword search |
| **Plugins & Gateway** | Plugin loader (system-probe, web-fetch) + multi-provider LLM gateway |
| **Remote Control** | Phone app pairs over LAN/Tailscale — discover, pair, open apps, lock, plus **live screen streaming + touch/keyboard control** of the laptop from the phone, and one-command phone mirroring (scrcpy) |
| **ScanGuard** | Double-extension & magic-byte scanner (Windows exe + Android APK) |
| **Phone tools** | `phone.sh` + `PHONE.md` — scrcpy/ADB helpers to mirror & control your phone from the PC |
| **A3THER Lab** | Image generation, camera + vision, Home Assistant control, AI video editor |

---

## 📋 Full Feature List

### 🖥 Core engine
- Cinematic glassmorphism HUD — live telemetry, AI core, event stream, themes (cyan/orange/gold/red/…)
- **8 modes** with real personas: `ai` · `humanoid` · `gaming` · `dev` · `chill` · `focus` · `creative` · `offline` — each changes the accent theme, icon and TTS voice
- **LLM gateway**: local **Ollama** first, plus OpenAI / DeepSeek / Gemini / Groq / Anthropic
- Long-term **memory** (JSON-backed, keyword search, session summaries) + memory API
- **Plugin loader** (system-probe, web-fetch), **MCP host**, extension catalog
- Real **actions**: open apps · web search · live weather · system monitor · browser & computer control · file controller · YouTube · send message
- First-run setup with a provider picker (or run fully offline)

### 🎙 Voice
- Offline **speech-to-text** (Vosk) with wake word **"hey aether"** — no cloud needed
- Natural **text-to-speech** (Edge TTS) + SAPI fallback + optional Kokoro / ElevenLabs
- **Talking popup avatar** — glowing voice rings + live transcript while it speaks

### ⌨ Desktop integration (Windows)
- Global **hotkeys** `Alt+F1…F8` (HUD, voice, screenshot, cycle mode, lock, status, hub, popup) — rebindable, work in the background from any app
- **System tray** icon — status tooltip, Summon / Toggle Voice / Cycle Mode / Shot / Status / Quit
- **Quick popup** — the talking avatar that slides in from a random edge (sometimes from below, sometimes from above)
- **Start with Windows** — real per-user registry `Run` entry, installed from Settings
- **Background mode** — runs hidden, summon anytime (Alt+F1, tray, or popup)
- No console/PowerShell window flashes (all subprocesses run silent)

### 📱 Remote control
- **Phone → laptop**: pair with a 6-digit code, then Status / Open apps / Lock / Screenshot / shell (opt-in)
- **Live screen streaming** — the phone sees the laptop screen in real time (MJPEG, ~12 fps)
- **Full touch control** — tap = click, hold = right-click, drag = mouse, scroll = wheel, double-tap = double-click, on-screen keyboard
- **Works anywhere** — Tailscale helper (`python -m remote_dev.tailnet`) makes cross-network control one command; no port-forwarding
- **Laptop → phone**: `phone.sh wifi` — plug in once, then full wireless mirror + control (scrcpy/ADB); also push/pull/screenshot
- **AetherRemote.apk** — the phone app with discovery, pairing, commands, and a Screen viewer button

### 🧪 A3THER Lab
- **Image generation** — one prompt, one image (OpenAI images API)
- **Camera + vision** — capture the webcam, then ask a vision model what it sees
- **Home Assistant** — list every device grouped by room/domain, tap to toggle (needs your HA server + long-lived token)
- **AI video editor** — real OpenCV rendering: folder of clips/images → styled montage (4 styles + title card), internet clip search (yt-dlp)

### 🛡 Security & tooling
- Pairing codes → long-lived bearer tokens; every state-changing call is authenticated; shell execution is opt-in and logged
- **ScanGuard** — double-extension & magic-byte scanner (Windows exe + Android APK)
- Website maker, calendar, notifications, AI predictor, live devices (Bluetooth + LAN)
- USB phone watcher with touch-free auto-unlock, broadcast mesh, video studio
- Honest everywhere: features without their optional deps/config return clear setup instructions — never faked success

---

## 🚀 Quick Start — the EXE (no Python, no pip)

1. Download **`A3THER.exe`** (single self-contained file) from the [latest release](https://github.com/al13n-x-v0x/a3ther/releases/latest).
2. Run it — done. The HUD opens in your browser at `http://localhost:8000` (native window mode also available).
3. First run **auto-installs** the optional voice deps it needs (`vosk`, `edge-tts`, …) — no manual pip.
4. The Vosk STT model (~40 MB) downloads once on first voice use.
5. No LLM key? It falls back to local **Ollama** — or pick a provider in Settings.

> 🔒 Nothing is bundled with your keys. API keys live in `%LOCALAPPDATA%\A3THER\` — never in the repo or the exe.

---

## 🛠 Setup from Git (for developers)

```bash
# 1. Clone
git clone https://github.com/al13n-x-v0x/a3ther.git
cd a3ther

# 2. Create a venv (Python 3.10+ recommended)
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS (partial support)

# 3. Install core deps
pip install -r requirements.txt

# 4. Run
python launcher.py              # full app (server + HUD + hotkeys + tray)
python launcher.py --background # hidden, summon with Alt+F1 or the tray
python main.py                  # boot engine only (no browser HUD)
```

**Optional extras** (auto-installed by the app on first run, installable manually):

```bash
pip install vosk edge-tts sounddevice pystray pillow
```

### First-run setup

```bash
python -m core.first_run        # pick your LLM provider & key (or Enter for offline/Ollama)
```

Or just launch the app and press **Enter** to skip — it runs offline with Ollama.

---

## 🎮 Using A.3.T.H.E.R.

### Global hotkeys (work in any app, even in the background)

| Key | Action |
|-----|--------|
| `Alt+F1` | Summon / hide the HUD |
| `Alt+F2` | Toggle voice input |
| `Alt+F3` | Take a screenshot |
| `Alt+F4` | Cycle mode (humanoid → gaming → dev → …) |
| `Alt+F5` | Lock the PC |
| `Alt+F6` | Show status |
| `Alt+F7` | Open the Hub |
| `Alt+F8` | Quick popup (Claude-style) |

All rebindable in **Settings → Global Hotkeys**. Fn-key laptops: `Fn+Alt+F1` etc. `AltGr` behaves as right-Alt for the same bindings.

### System tray
Right-click the **A3THER** icon (cyan "A") in the notification area for Summon HUD · Toggle Voice · Cycle Mode · Screenshot · Status · Quit.

### Start with Windows
**Settings → Background & Startup → Start with Windows** — installs a real `HKCU\...\CurrentVersion\Run` entry so A.3.T.H.E.R. boots hidden and waits for `Alt+F1` or the tray.

### Phone remote control
1. `python launcher.py` on the laptop.
2. Install **`AetherRemote.apk`** on your phone — same Wi-Fi.
3. Phone → *Find laptop* → enter the 6-digit code shown on the laptop → *Pair*.
4. **Screen** — live view of the laptop; tap = click, hold = right-click, drag = mouse, scroll = wheel, keyboard bar = typing.
5. Status · Open Chrome · Open Notepad · Lock — straight from the phone.

**Use it from anywhere:** the server binds `0.0.0.0`. Install Tailscale on both devices (`winget install Tailscale.Tailscale`, then `python -m remote_dev.tailnet` for your IP), and the phone reaches the laptop over any network — mobile data, another country — with zero port-forwarding.

**Control the phone from the laptop:** `./phone.sh wifi` — plug the phone in once over USB, and it auto-switches to wireless ADB and mirrors the phone screen on your PC (scrcpy).

---

## 🏗 Architecture

```
core/            hotkeys, tray, popup, startup, modes, ui_settings, auto_deps,
                 engine state, desktop window, first-run
backend/         FastAPI server, AI brain, gateway, features API, plugins
voice/           STT (Vosk) + TTS (Edge) pipeline, wake-word engine
memory/          long-term memory manager + orchestrator (keyword search)
config/          settings store + paths + API-key management
actions/         open_app, web_search, weather, system monitor, browser/computer
                 control, file controller, youtube, send_message
Frontend/        the HUD — index.html, script.js, style.css (+ phone, hub, plugins)
remote_dev/      LAN remote-control server: pairing, discovery, commands
remote_app/      Toga phone client → AetherRemote.apk
scan_apk/        ScanGuard scanner → Windows exe + Android APK
```

**Backend**: Python + FastAPI + uvicorn. **HUD**: vanilla HTML/CSS/JS (no framework — fast and light). **Voice**: Vosk (offline STT) + Edge TTS. **LLMs**: Ollama local, or OpenAI / DeepSeek / Gemini / Groq / Anthropic.

---

## 🔨 Building the EXE

```bash
pip install pyinstaller
pyinstaller a3ther.spec --noconfirm
# → dist/A3THER/A3THER.exe (onedir; bundles Python + all deps + Vosk DLLs)
```

Build the phone APK:

```bash
cd remote_app && briefcase build android  # or use the prebuilt AetherRemote.apk
```

---

## 📜 Status

| Component | Status |
|-----------|--------|
| HUD + themes + modes | ✅ Live |
| Voice (STT/TTS, wake word) | ✅ Live (offline STT, Edge TTS) |
| Global hotkeys | ✅ Live (rebindable) |
| System tray + quick popup | ✅ Live |
| Start with Windows | ✅ Live (registry) |
| Memory + plugins + gateway | ✅ Live |
| Phone remote control (LAN/Tailnet) | ✅ Live — pair + commands + **screen streaming + touch/keyboard input** |
| ScanGuard scanner | ✅ Live (exe + APK) |
| Laptop → phone mirroring | ✅ Live (`phone.sh wifi` via scrcpy/ADB) |
| Video editor | ✅ Live (OpenCV renders; honest errors when deps missing) |
| A3THER Lab (image gen, camera+vision, Home Assistant) | ✅ Live (honest setup prompts when keys/deps missing) |
| Silent subprocesses | ✅ Live — no console/PowerShell window flashes |

See [`REMOTE.md`](REMOTE.md) for the honest remote-control roadmap.

---

## ⚠️ Disclaimer

A.3.T.H.E.R. is an original software project created by **AL13N Industries**. Some visual inspiration comes from futuristic HUD concepts and science-fiction interfaces. All code, architecture, and UI are independently developed for this project.

The ScanGuard scanner and remote-control features are **security tooling for your own devices** — use them ethically and only on systems you own or are authorized to test.

---

## 👨‍💻 Created By

**AL13N Industries** — **A.3.T.H.E.R.**

**Adaptive 3rd-generation Technology for Heuristic Execution & Research**

**v1.0** — Windows · Local-first · Open Source
