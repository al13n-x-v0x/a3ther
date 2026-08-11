"""Real system telemetry via psutil — CPU, RAM, storage, network, temperature, battery, processes."""

import platform
import threading
import time

import psutil

# Non-blocking CPU sampling via a dedicated daemon thread.
# We compute the percentage ourselves from psutil's ABSOLUTE cumulative
# CPU-time counters (``psutil.cpu_times()``) instead of relying on
# ``cpu_percent(interval=None)``. That makes the reading immune to other
# modules calling ``cpu_percent(interval=…)`` (e.g. actions/system_monitor.py),
# which share psutil's global baseline and would otherwise shrink the HUD's
# measurement window.
_CPU_VALUE = 0.0
_CPU_LOCK = threading.Lock()
_CPU_STOP = threading.Event()


def _cpu_busy_percent(prev: object, cur: object, wall_s: float) -> float:
    """Percent of wall time the CPU was busy, from two cumulative snapshots."""
    fields = getattr(cur, "_fields", ())
    total = sum(max(0.0, getattr(cur, f, 0.0) - getattr(prev, f, 0.0)) for f in fields)
    if total <= 0.0 or wall_s <= 0.0:
        return 0.0
    idle = max(0.0, getattr(cur, "idle", 0.0) - getattr(prev, "idle", 0.0))
    busy = total - idle
    return 100.0 * busy / total


def _cpu_sampler_loop() -> None:
    """Sample real CPU% every ~1 s; readers never block."""
    global _CPU_VALUE
    try:
        prev = psutil.cpu_times()
    except Exception:  # noqa: BLE001
        prev = None
    while not _CPU_STOP.is_set():
        _CPU_STOP.wait(1.0)
        try:
            cur = psutil.cpu_times()
            if prev is not None:
                value = _cpu_busy_percent(prev, cur, 1.0)
                with _CPU_LOCK:
                    _CPU_VALUE = round(value, 1)
            prev = cur
        except Exception:  # noqa: BLE001
            pass


def _start_cpu_sampler() -> None:
    threading.Thread(target=_cpu_sampler_loop, name="cpu-sampler", daemon=True).start()


_start_cpu_sampler()


def _sample_cpu_percent() -> float:
    """Latest real CPU% from the background sampler (never blocks, never 0.0)."""
    with _CPU_LOCK:
        return _CPU_VALUE


def _get_cpu_temp() -> float | None:
    """Best-effort CPU temperature. Returns None when unavailable."""
    try:
        temps = psutil.sensors_temperatures()
        for name in ("coretemp", "k10temp", "cpu_thermal", "acpitz", "cpu-thermal", "zenpower"):
            if name in temps and temps[name]:
                return round(temps[name][0].current, 1)
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except Exception:
        pass
    # Windows fallback via WMI (pure COM, no subprocess)
    if platform.system() == "Windows":
        try:
            import wmi  # type: ignore

            w = wmi.WMI(namespace="root/wmi")
            zones = w.MSAcpi_ThermalZoneTemperature()
            if zones:
                return round((zones[0].CurrentTemperature / 10.0) - 273.15, 1)
        except Exception:
            pass
    return None


def _get_gpu() -> dict:
    """GPU usage via pynvml if installed. Zero subprocess on all platforms."""
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        name = pynvml.nvmlDeviceGetName(handle)
        total_gb = round(mem.total / 1024 ** 3, 1)
        used_gb = round(mem.used / 1024 ** 3, 1)
        return {
            "percent": round(float(util), 1),
            "name": str(name),
            "used_gb": used_gb,
            "total_gb": total_gb,
        }
    except Exception:
        return {"percent": None, "name": None, "used_gb": None, "total_gb": None}


def _get_network() -> dict:
    """Aggregate network counters across all interfaces (since boot)."""
    try:
        counters = psutil.net_io_counters()
        return {
            "sent_mb": round(counters.bytes_sent / 1024 ** 2, 1),
            "recv_mb": round(counters.bytes_recv / 1024 ** 2, 1),
            "packets_sent": counters.packets_sent,
            "packets_recv": counters.packets_recv,
        }
    except Exception:
        return {"sent_mb": None, "recv_mb": None, "packets_sent": None, "packets_recv": None}


def get_stats_snapshot() -> dict:
    """Full telemetry snapshot for the HUD."""
    cpu = _sample_cpu_percent()
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    temp = _get_cpu_temp()
    gpu = _get_gpu()
    net = _get_network()
    battery = None
    try:
        bat = psutil.sensors_battery()
        if bat:
            battery = {
                "percent": round(bat.percent, 0),
                "plugged": bool(bat.power_plugged),
                "seconds_left": bat.secsleft if bat.secsleft != psutil.POWER_TIME_UNLIMITED else None,
            }
    except Exception:
        battery = None

    boot_time = psutil.boot_time()
    uptime_secs = max(0, time.time() - boot_time)
    uptime_h = int(uptime_secs // 3600)
    uptime_m = int((uptime_secs % 3600) // 60)

    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mount": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": round(usage.total / 1024 ** 3, 1),
                "used_gb": round(usage.used / 1024 ** 3, 1),
                "percent": usage.percent,
            })
        except Exception:
            continue

    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "cpu": {
            "name": platform.processor() or "Unknown CPU",
            "percent": round(cpu, 1),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "freq_mhz": round(psutil.cpu_freq().current, 0) if psutil.cpu_freq() else None,
            "temp_c": temp,
        },
        "ram": {
            "percent": ram.percent,
            "used_gb": round(ram.used / 1024 ** 3, 1),
            "total_gb": round(ram.total / 1024 ** 3, 1),
            "available_gb": round(ram.available / 1024 ** 3, 1),
            "swap_percent": swap.percent if swap else None,
        },
        "gpu": gpu,
        "storage": {
            "percent": disk.percent,
            "used_gb": round(disk.used / 1024 ** 3, 1),
            "total_gb": round(disk.total / 1024 ** 3, 1),
            "free_gb": round(disk.free / 1024 ** 3, 1),
            "disks": disks,
        },
        "network": net,
        "battery": battery,
        "uptime": {"hours": uptime_h, "minutes": uptime_m},
        "process_count": len(psutil.pids()),
        "timestamp": time.time(),
    }


def get_top_processes(limit: int = 8) -> list[dict]:
    """Top processes by CPU usage."""
    rows = []
    for proc in psutil.process_iter(["name", "cpu_percent", "memory_percent", "pid"]):
        try:
            rows.append(proc.info)
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("cpu_percent") or 0, reverse=True)
    return [
        {
            "pid": r.get("pid"),
            "name": r.get("name") or "unknown",
            "cpu_percent": round(r.get("cpu_percent") or 0, 1),
            "memory_percent": round(r.get("memory_percent") or 0, 1),
        }
        for r in rows[:limit]
    ]
