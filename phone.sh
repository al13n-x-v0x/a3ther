#!/usr/bin/env bash
# phone.sh — control your Android phone from this laptop (scrcpy + adb).
#
#   ./phone.sh devices             list connected phones
#   ./phone.sh wifi                ONE-COMMAND wireless control: USB plug-in,
#                                  auto-enable Wi-Fi ADB, connect, mirror
#   ./phone.sh mirror              mirror & control the phone screen (USB or Wi-Fi)
#   ./phone.sh wire <ip:port>      connect over Wi-Fi (run "adb pair" first)
#   ./phone.sh push <file>         copy a file from laptop -> phone Downloads/
#   ./phone.sh pull <phone-path>   copy a file from phone -> current folder
#   ./phone.sh shot                save a screenshot of the phone to phone-shot.png
#   ./phone.sh stop                disconnect all wireless phones
#
# One-time phone setup (5 min): Settings > About > tap "Build number" 7x to
# enable Developer options, then Developer options > enable USB debugging.
# For wireless: phone on the same Wi-Fi, then just run  ./phone.sh wifi
# (USB attached once) — it flips the phone to wireless ADB and mirrors it.
set -euo pipefail

# --- locate adb / scrcpy (WinGet packages dir, then PATH) ------------------
WINGET_PKGS="${LOCALAPPDATA:-$HOME/AppData/Local}/Microsoft/WinGet/Packages"

find_tool() {
    local name="$1"
    if command -v "$name" >/dev/null 2>&1; then
        command -v "$name" | head -1
        return 0
    fi
    local exe
    exe="$(find "$WINGET_PKGS" -iname "$name.exe" 2>/dev/null | head -1)"
    [ -n "$exe" ] && echo "$exe"
}

ADB="$(find_tool adb)"
SCRCPY="$(find_tool scrcpy)"

if [ -z "$ADB" ] || [ -z "$SCRCPY" ]; then
    echo "adb / scrcpy not found. Install with:" >&2
    echo "  winget install Genymobile.scrcpy Google.PlatformTools" >&2
    exit 1
fi

cmd="${1:-help}"
shift || true

# --- helpers ----------------------------------------------------------------
usb_serial() {
    "$ADB" devices | awk 'NR>1 && $2=="device" {print $1; exit}'
}

phone_ip() {
    "$ADB" -s "$1" shell ip route 2>/dev/null | awk '/wlan0|wlan|eth0/ && /src/ {print $NF; exit}'
}

case "$cmd" in
    devices)
        "$ADB" devices -l
        ;;
    wifi)
        # 1. Find a phone over USB; 2. flip it to wireless ADB; 3. connect; 4. mirror.
        serial="$(usb_serial || true)"
        if [ -z "$serial" ]; then
            echo "No phone over USB. Plug it in (USB debugging on) and run again." >&2
            exit 1
        fi
        echo "> Found over USB: $serial"
        echo "> Switching the phone to wireless ADB (port 5555)…"
        "$ADB" -s "$serial" tcpip 5555 >/dev/null
        ip="$(phone_ip "$serial" || true)"
        if [ -z "$ip" ]; then
            echo "Could not read the phone's Wi-Fi IP." >&2
            echo "Open Settings > Wi-Fi on the phone and pass it manually:" >&2
            echo "  $0 wire <ip>:5555" >&2
            exit 1
        fi
        echo "> Phone Wi-Fi IP: $ip"
        "$ADB" connect "$ip:5555" >/dev/null
        echo "> Connected wirelessly — launching scrcpy (phone screen on your PC)."
        echo "  (You can unplug the USB now.)"
        exec "$SCRCPY" -s "$ip:5555" "$@"
        ;;
    mirror)
        serial="$(usb_serial || true)"
        if [ -z "$serial" ]; then
            echo "No phone connected." >&2
            echo "  USB: plug in and accept the RSA prompt on the phone." >&2
            echo "  Wi-Fi: $0 wifi   (one command, needs USB once) or  $0 wire <ip:port>" >&2
            exit 1
        fi
        exec "$SCRCPY" -s "$serial" "$@"
        ;;
    wire)
        if [ $# -lt 1 ]; then
            echo "Usage: $0 wire <ip:port>" >&2
            exit 1
        fi
        echo "> adb connect $1"
        "$ADB" connect "$1"
        ;;
    push)
        if [ $# -lt 1 ]; then
            echo "Usage: $0 push <file>  (lands in /sdcard/Download/)" >&2
            exit 1
        fi
        "$ADB" push "$1" /sdcard/Download/
        ;;
    pull)
        if [ $# -lt 1 ]; then
            echo "Usage: $0 pull <phone-path>  (e.g. /sdcard/DCIM/Camera/IMG_1.jpg)" >&2
            exit 1
        fi
        "$ADB" pull "$1" .
        ;;
    shot)
        serial="$(usb_serial || true)"
        [ -n "$serial" ] || serial="$(adb devices | awk 'NR>1 && $2=="device" {print $1; exit}')"
        "$ADB" -s "$serial" exec-out screencap -p > phone-shot.png
        echo "Saved phone-shot.png"
        ;;
    stop)
        "$ADB" disconnect >/dev/null 2>&1 || true
        echo "Wireless phones disconnected."
        ;;
    *)
        sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
        ;;
esac
