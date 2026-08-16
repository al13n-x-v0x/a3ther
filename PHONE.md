# Phone ↔ Laptop control

Everything below is legitimate, consent-based remote access (like TeamViewer /
scrcpy). The phone stays in your control — you approve every connection.

## What's already installed (laptop side)

- **scrcpy 4.1** — mirror and control the phone screen from the laptop.
- **adb / Android Platform-Tools 37** — the bridge scrcpy uses.

Both were installed per-user with winget:
`winget install Genymobile.scrcpy Google.PlatformTools`

## Laptop → phone (scrcpy)

### One-time phone setup (~5 min)
1. Phone: **Settings → About phone** → tap **Build number** 7 times
   (enables Developer options).
2. **Settings → Developer options → enable USB debugging**.

### Wired
1. Plug the phone into the laptop with a USB cable.
2. On the phone, accept the "Allow USB debugging?" RSA prompt (tick
   "Always allow").
3. From this repo: `./phone.sh mirror`

### Wireless (Android 11+, both on the same Wi-Fi)
1. **Developer options → Wireless debugging → enable**, tap
   **Pair device with pairing code** — note the `ip:port` and 6-digit code.
2. `adb pair <ip:port>` → enter the code.
3. `./phone.sh wire <ip:port>` (the connect address shown next to
   "Wireless debugging", not the pair address).
4. `./phone.sh mirror`

### Other commands
```
./phone.sh devices          # what adb sees
./phone.sh push ./file.pdf  # laptop -> phone /sdcard/Download/
./phone.sh pull /sdcard/DCIM/Camera/IMG_1.jpg
./phone.sh shot             # screenshot of the phone screen
```

## Phone → laptop (RustDesk)

scrcpy is one-way (laptop controls phone). For the reverse — driving the
laptop from the phone — use **RustDesk**, an open-source remote desktop:

1. Download RustDesk from https://rustdesk.com (Windows installs in one
   click; the phone app is on the Play Store).
2. Run it on **both** devices.
3. Option A (simplest): sign into the same RustDesk account on both, or
   create a permanent password on the laptop, then enter the laptop's
   9-digit ID + password in the phone app.
4. Connect from the phone — you now have the laptop's screen and mouse.

RustDesk also works laptop → laptop and laptop → phone the same way.

## Notes

- **Screen stays on / brightness**: scrcpy mirrors what the phone shows; a
  `scrcpy --stay-awake` variant (add `-w`) keeps the screen on during the
  session. `./phone.sh mirror -w` works too.
- scrcpy's folder bundles its own adb, so `./phone.sh` resolves whichever
  comes first in `%LOCALAPPDATA%\Microsoft\WinGet\Packages`.
- Phone not found? `./phone.sh devices` — if empty, check USB debugging is
  on and the cable allows data (not charge-only).
