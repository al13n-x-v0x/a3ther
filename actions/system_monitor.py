"""
actions/system_monitor.py — SYSTEM intent.

Returns a human-readable system status line. Uses ``psutil`` when available
(accurate live values); falls back to a best-effort summary otherwise —
never raises.
"""

from __future__ import annotations

from typing import Any, Dict


def get_system_status() -> Dict[str, Any] | str:
    try:
        import psutil  # noqa: PLC0415

        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        booted = psutil.boot_time()
        import datetime  # noqa: PLC0415

        uptime = str(datetime.timedelta(seconds=int(__import__("time").time() - booted)))
        battery = None
        try:
            bat = psutil.sensors_battery()
            if bat:
                battery = f"{int(bat.percent)}%" + (" (charging)" if bat.power_plugged else "")
        except Exception:  # noqa: BLE001
            pass
        parts = [
            f"CPU {cpu}%",
            f"RAM {mem.percent}% ({mem.used // (1024**3)}/{mem.total // (1024**3)} GB)",
            f"disk {disk.percent}%",
            f"up {uptime}",
        ]
        if battery:
            parts.append(f"battery {battery}")
        return ", ".join(parts)
    except Exception as exc:  # noqa: BLE001
        return f"System status unavailable (install psutil): {exc}"


if __name__ == "__main__":  # pragma: no cover
    print(get_system_status())
