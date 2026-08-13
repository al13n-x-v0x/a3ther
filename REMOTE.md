# A3THER Remote Control — Phase 1

A real, working phone ↔ laptop link built into the A3THER project. No fake
prototypes: everything here runs and was verified end-to-end on a live
machine.

```
┌──────────────┐   UDP beacon (42871)   ┌──────────────────┐
│    Phone     │ ◄────────────────────► │     Laptop       │
│ AetherRemote │   HTTP JSON (42872)    │ remote_dev/      │
└──────────────┘                        └──────────────────┘
      ▲  discovery + pair + command            │
      └──────────────┬─────────────────────────┘
                     │ executes locally
             open / lock / status / run*
```

## Components

### Desktop (laptop) — `remote_dev/`

- **`devices.py`** — device identity (persisted in the OS app-data folder),
  pairing store (short-lived codes → long-lived tokens), and LAN discovery
  (a UDP beacon that answers `A3THER?` probes).
- **`agent_server.py`** — threaded HTTP server:

  | Endpoint          | Auth   | Purpose                                  |
  |-------------------|--------|------------------------------------------|
  | `GET /identity`   | none   | public device info                       |
  | `POST /pair`      | none   | get a 6-digit code + `a3ther://` QR      |
  | `POST /pair/confirm` | none | exchange the code for a token         |
  | `POST /command`   | token  | run an action (open / lock / status / run) |
  | `GET /devices`    | token  | list paired devices                      |
  | `POST /revoke`    | token  | unpair a device                          |

  Actions are real: `open <app>` launches Chrome/VS Code/Notepad/Explorer
  (fire-and-forget, no hang), `lock` locks the workstation, `status`
  reports CPU/memory/uptime via psutil, `screenshot` captures the screen
  (when `mss` is installed), and `run <command>` executes shell commands —
  **disabled by default**, enable with `A3THER_ALLOW_SHELL=1` or
  `launcher --allow-remote-shell`.

- Wired into `launcher.py`: the server starts automatically unless
  `--no-remote` is passed. Run it standalone with
  `python -m remote_dev.agent_server`.

### Phone — `remote_app/`

A Briefcase/Toga app (`AetherRemote.apk`) with a pure-Python stdlib client
(`src/aetherremote/client.py` — discovery, pair, command). It finds the
laptop on the LAN automatically, or takes its IP manually; pairs with the
code shown on the laptop; then sends Status / Open Chrome / Open Notepad /
Lock commands and shows the results.

Build (needs the Android SDK + a JDK):

```bash
cd remote_app
export ANDROID_HOME="$LOCALAPPDATA/BeeWare/briefcase/Cache/tools/android_sdk"
python -m briefcase create android
python -m briefcase build android
# APK: build/aetherremote/android/gradle/app/build/outputs/apk/debug/app-debug.apk
```

## Quick start

1. Start the laptop side: `python launcher.py` (or
   `python -m remote_dev.agent_server` for just the link).
2. The laptop broadcasts itself on the LAN and prints
   `[A3THER] Remote control ready on LAN port 42872`.
3. Install `AetherRemote.apk` on the phone (same Wi-Fi).
4. Open the app → tap **Find laptop** (or type the laptop's IP).
5. Ask the laptop for a code (`POST /pair`, or the server CLI will print
   one) — or read the QR shown by the server — and type the 6 digits into
   the phone.
6. Tap **Pair**, then use **Status / Open Chrome / Open Notepad / Lock**.

## Security model

- Pairing is code-authenticated and time-limited (10 min); the resulting
  token is only exchanged over the LAN.
- Every state-changing call requires the Bearer token; unauthenticated
  calls return 401 (verified in tests).
- Shell execution is opt-in and logged with the caller's device info.
- The server binds to the LAN only — nothing is exposed to the internet,
  no tunnels, no third-party relay.
- Identity and pairing state live in the OS app-data folder, never in the
  repo.

## Roadmap (Phase 2+ — not implemented yet, not faked)

- Screen capture streaming to the phone (video path is real work; until it
  lands the phone app honestly reports actions, not a fake screen).
- Phone → laptop touchpad/keyboard input.
- Voice commands from the phone (reuse the existing wake-word/STT stack).
- File transfer and clipboard sync.
