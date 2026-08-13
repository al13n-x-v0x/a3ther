"""
remote_dev/tailnet.py — reach A3THER from anywhere (not just the LAN).

The remote server already binds 0.0.0.0, so ANY address reachable from the
phone works. Tailscale is the zero-config, encrypted, NAT-traversal way to
make the laptop reachable from the phone across the internet — no port
forwarding, no public exposure, no relay to self-host.

Run:  python -m remote_dev.tailnet

It prints:
  * your tailnet IP (the address the phone pairs with / opens the viewer on)
  * the direct viewer URL
  * a QR-encoded pairing code when ``qrencode`` is available
  * clear install instructions when Tailscale isn't installed yet
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .agent_server import DEFAULT_PORT

_TAILSCALE_CANDIDATES = (
    "tailscale",
    str(Path.home() / "AppData" / "Local" / "Programs" / "tailscale" / "tailscale.exe"),
    str(Path.home() / "AppData" / "Local" / "Tailscale" / "tailscale.exe"),
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/bin/tailscale",
    "/usr/sbin/tailscale",
)


def find_tailscale() -> str | None:
    for cand in _TAILSCALE_CANDIDATES:
        if cand == "tailscale":
            found = shutil.which(cand)
            if found:
                return found
        elif Path(cand).exists():
            return cand
    return None


def tailnet_ip(ts: str) -> str | None:
    """The laptop's Tailscale IPv4 (the address a phone can reach)."""
    for args in (["ip", "-4"], ["ip"]):
        try:
            out = subprocess.run([ts, *args], capture_output=True, text=True, timeout=8)
            first = (out.stdout or "").strip().splitlines()
            if first:
                return first[0].strip()
        except Exception:  # noqa: BLE001
            continue
    return None


def print_setup() -> int:
    ts = find_tailscale()
    if ts is None:
        print("Tailscale is NOT installed.")
        print()
        print("Tailscale makes this laptop reachable from your phone ANYWHERE")
        print("(any network) with zero port-forwarding and end-to-end encryption.")
        print()
        print("Install it:")
        print("    winget install Tailscale.Tailscale")
        print("  or: https://tailscale.com/download")
        print()
        print("Then sign in on BOTH devices with the same account:")
        print("    tailscale up")
        print("    tailscale login")
        print()
        print("Then re-run this helper:  python -m remote_dev.tailnet")
        return 1

    ip = tailnet_ip(ts)
    if not ip:
        print("Tailscale is installed but has no IPv4 address.")
        print("Run 'tailscale up' (or sign in) and try again.")
        return 1

    print(f"Tailscale found: {ts}")
    print(f"Laptop tailnet IP: {ip}")
    print()
    print("On the phone (AetherRemote app):")
    print(f"    address  = {ip}")
    print(f"    code     = the 6-digit code shown on the laptop")
    print()
    print(f"Remote screen viewer URL (open in the phone browser after pairing):")
    print(f"    http://{ip}:{DEFAULT_PORT}/remote/viewer?token=<your-token>")
    print()
    print("That URL works over ANY network — home Wi-Fi, mobile data, another")
    print("country — because Tailscale connects the two devices directly.")
    print()
    try:
        out = subprocess.run([ts, "status", "--json"], capture_output=True, text=True, timeout=8)
        status = json.loads(out.stdout or "{}")
        self_info = status.get("Self", {})
        print(f"Current tailnet node: {self_info.get('HostName', '?')}  ({self_info.get('DNSName', '?')})")
    except Exception:  # noqa: BLE001
        pass
    return 0


def main() -> int:
    return print_setup()


if __name__ == "__main__":
    sys.exit(main())
