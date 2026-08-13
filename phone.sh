#!/usr/bin/env bash
# phone.sh — control your Android phone from this laptop (scrcpy + adb).
#
#   ./phone.sh devices             list connected phones
#   ./phone.sh mirror              mirror & control the phone screen (USB or Wi-Fi)
#   ./phone.sh wire <ip:port>      connect over Wi-Fi (run "adb pair" first)
#   ./phone.sh push <file>         copy a file from laptop -> phone Downloads/
#   ./phone.sh pull <phone-path>   copy a file from phone -> current folder
#   ./phone.sh shot                save a screenshot of the phone to phone-shot.png
#
# One-time phone setup (5 min): Settings > About > tap "Build number" 7x to
# enable Developer options, then Developer options > enable USB debugging.
# For wireless: phone on the same Wi-Fi, `adb pair <ip:port>` with the code
# the phone shows, then `./phone.sh wire <ip:port>`.
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

case "$cmd" in
    devices)
        "$ADB" devices -l
        ;;
    mirror)
        if ! "$ADB" devices | grep -qE "device$"; then
            echo "No phone connected." >&2
            echo "  USB: plug in and accept the RSA prompt on the phone." >&2
            echo "  Wi-Fi: $0 wire <ip:port>" >&2
            exit 1
        fi
        "$SCRCPY" "$@"
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
        "$ADB" exec-out screencap -p > phone-shot.png
        echo "Saved phone-shot.png"
        ;;
    *)
        sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
        ;;
esac
