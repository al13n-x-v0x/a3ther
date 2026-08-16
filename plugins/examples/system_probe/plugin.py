"""System Probe — sample A3THER Python plugin.

Hot-loaded by the plugin manager; exposes live host telemetry via
``psutil``. Drop any folder shaped like this one into ``plugins/`` to add
capabilities without touching the core.
"""
from __future__ import annotations

from plugins.plugin_base import A3THERPlugin, Capability


class Plugin(A3THERPlugin):
    """Reports CPU/RAM/disk/battery telemetry."""

    name = "system-probe"
    version = "1.0.0"
    description = "Live CPU, RAM, disk and battery telemetry."
    author = "AL13N Industries"
    capabilities = [
        Capability(
            name="probe_system",
            description="Return CPU, RAM, disk and battery statistics as text.",
        ),
        Capability(
            name="probe_cpu",
            description="Return the current CPU load percentage.",
        ),
    ]

    def handle(self, capability: str, params: dict) -> str:
        import psutil  # local import so the plugin degrades gracefully

        if capability == "probe_cpu":
            return f"CPU: {psutil.cpu_percent(interval=0.3):.1f}%"

        if capability == "probe_system":
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage(params.get("path", "/") if params else "/")
            battery = None
            try:
                battery = psutil.sensors_battery()
            except Exception:
                pass

            lines = [
                f"CPU: {psutil.cpu_percent(interval=0.3):.1f}% ({psutil.cpu_count()} cores)",
                f"RAM: {vm.percent:.0f}% used ({vm.used // (1024**3)} GB / {vm.total // (1024**3)} GB)",
                f"DISK: {disk.percent:.0f}% used on {disk.total // (1024**3)} GB",
            ]
            if battery is not None:
                lines.append(f"BATTERY: {battery.percent:.0f}% {'charging' if battery.power_plugged else 'on battery'}")
            return "\n".join(lines)

        raise ValueError(f"Unknown capability: {capability}")

    def on_load(self) -> None:
        # Nothing to set up — kept to demonstrate the lifecycle hook.
        return
