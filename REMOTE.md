# A3THER Remote Control — Phase 1 & 2

A real, working phone ↔ laptop link built into the A3THER project. No fake
prototypes: everything here runs and was verified end-to-end on a live
machine.

```
┌──────────────┐   UDP beacon (42871)   ┌──────────────────┐
│    Phone     │ ◄────────────────────► │     Laptop       │
│ AetherRemote │   HTTP JSON (42872)    │ remote_dev/      │
└──────────────┘                        └──────────────────┘
      ▲  discovery · pair · command           │
      │  screen stream (MJPEG)                │ executes locally
      │  tap / drag / keys (input)            ▼
      └────────────────────────────────► open / lock / status / run*
```

## Components

### Desktop (laptop) — `remote_dev/`

- **`devices.py`** — device identity (persisted in the OS app-data folder),
  pairing store (short-lived codes → long-lived tokens), and LAN discovery
  (a UDP beacon that answers `A3THER?` probes).
- **`agent_server.py`** — threaded HTTP server:

  | Endpoint            | Auth   | Purpose                                  |
  |---------------------|--------|------------------------------------------|
  | `GET /identity`     | none   | public device info                       |
  | `POST /pair`        | none   | get a 6-digit code + `a3ther://` QR      |
  | `POST /pair/confirm`| none   | exchange the code for a token            |
  | `POST /command`     | token  | run an action (open / lock / status / run) |
  | `GET /remote/viewer`| token  | the screen-viewer page (phone browser)   |
  | `GET /remote/stream`| token  | **live screen stream** (MJPEG)           |
  | `POST /remote/input`| token  | **remote mouse + keyboard**              |
  | `GET /devices`      | token  | list paired devices                      |
  | `POST /revoke`      | token  | unpair a device                          |

- **`screen_stream.py`** — a background capture thread (mss + PIL) publishes
  JPEG frames (downscaled to 960 px, ~12 fps) to a shared slot; every
  stream client reads the same frames (one capture cost for all viewers).
- **`input_control.py`** — REAL input via ctypes: mouse move/click/drag/
  double-click/wheel (SetCursorPos + mouse_event) and keyboard text + special
  keys (SendInput). Coordinates are normalized (0..1), so the phone never
  needs to know the laptop's resolution.
- **`viewer.py`** — the self-contained viewer page (no CDN): tap = click,
  long-press = right-click, drag = mouse move, scroll = wheel, double-tap =
  double-click, plus an on-screen keyboard bar for typing.
- **`tailnet.py`** — `python -m remote_dev.tailnet` prints your Tailscale IP
  and the viewer URL so the phone can reach the laptop **from anywhere**.

Actions are real: `open <app>` launches Chrome/VS Code/Notepad/Explorer
(fire-and-forget, no hang), `lock` locks the workstation, `status` reports
CPU/memory/uptime via psutil, `screenshot` captures the screen (when `mss`
is installed), and `run <command>` executes shell commands — **disabled by
default**, enable with `A3THER_ALLOW_SHELL=1` or
`launcher --allow-remote-shell`.

Wired into `launcher.py`: the server starts automatically unless
`--no-remote` is passed. Run it standalone with
`python -m remote_dev.agent_server`.

### Phone — `remote_app/`

A Briefcase/Toga app (`AetherRemote.apk`) with a pure-Python stdlib client
(`src/aetherremote/client.py` — discovery, pair, command, viewer URL). It
finds the laptop on the LAN automatically, or takes its IP manually; pairs
with the code shown on the laptop; then sends Status / Open Chrome / Open
Notepad / Lock, or taps **Screen** to open the live viewer — the phone
becomes a touchpad + keyboard for the laptop.

### Laptop → phone (controlling the phone from the laptop)

- **`phone.sh`** — `./phone.sh wifi` turns a USB-connected Android phone
  into a wireless mirror in one command (auto `adb tcpip 5555` → connect →
  scrcpy); `mirror`, `push`, `pull`, `shot`, `wire`, `stop` cover the rest.
- That's scrcpy/ADB — the honest, robust way to fully control an Android
  phone from Windows (see `PHONE.md`).

## Quick start

1. Start the laptop side: `python launcher.py` (or
   `python -m remote_dev.agent_server` for just the link).
2. Install `AetherRemote.apk` on the phone.
3. App → **Find laptop** (or type its IP) → enter the 6-digit code shown on
   the laptop → **Pair**.
4. Tap **Screen** — the phone shows the laptop live; tap/drag/type to
   control it.

### Use it from anywhere (not just the same Wi-Fi)

The server binds `0.0.0.0`, so anything that can reach the laptop works.
**Tailscale** is the zero-config encrypted way to make that true on any
network (home, mobile data, another country) — no port forwarding, no
public exposure:

```bash
winget install Tailscale.Tailscale      # on the laptop
python -m remote_dev.tailnet            # prints your IP + the viewer URL
```

Then on the phone: use the tailnet IP in AetherRemote (or open
`http://<tailnet-ip>:42872/remote/viewer?token=…`), and it just works —
LAN or not.

## Security model

- Pairing is code-authenticated and time-limited; the token is only ever
  sent over your own network (LAN or Tailnet).
- Every state-changing call (commands, stream, input) requires the Bearer
  token; unauthenticated calls return 401 (verified in tests).
- Shell execution is opt-in and logged with the caller's device info.
- The server binds `0.0.0.0` (needed for phones on Wi-Fi) but is **not**
  exposed to the internet by itself — no tunnels, no port forwarding, no
  third-party relay. Tailscale keeps cross-network traffic private.
- Identity and pairing state live in the OS app-data folder, never in the
  repo.

## Roadmap (not implemented yet, not faked)

- Voice commands from the phone (reuse the existing wake-word/STT stack).
- File transfer and clipboard sync between phone and laptop.
- Screen streaming **from** the phone to the laptop in-app (scrcpy covers
  this today via `phone.sh wifi`).
