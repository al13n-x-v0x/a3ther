"""
specs_service.py — real hardware specifications for the HUD.

Pulls the machine's actual specs instead of placeholder names:

- CPU brand: Windows registry ``ProcessorNameString``, Linux ``/proc/cpuinfo``
  ``model name``, macOS ``sysctl machdep.cpu.brand_string``.
- GPU: NVML (pynvml) when an NVIDIA driver is present, otherwise Windows WMI
  ``Win32_VideoController``.
- RAM / storage / battery: psutil.
- OS + hostname: platform.

Every source is best-effort — if a lookup fails it degrades to a sensible
fallback rather than raising, so the HUD always gets a real, truthful answer.
"""
from __future__ import annotations

import logging
import os
import platform
import threading
import time

import psutil

LOGGER = logging.getLogger("a3ther.services.specs")

# Specs are mostly static — cache 60 s so repeated frontend polls (and
# multiple browser tabs) don't re-spawn a PowerShell subprocess every time.
_CACHE: dict = {}
_LOCK = threading.Lock()
_CACHE_TTL = 60.0


# --------------------------------------------------------------------------- #
# CPU
# --------------------------------------------------------------------------- #
def _cpu_brand_windows() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(value).strip() or None
    except Exception:  # noqa: BLE001
        return None


def _cpu_brand_linux() -> str | None:
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _cpu_brand_mac() -> str | None:
    try:
        out = os.popen("sysctl -n machdep.cpu.brand_string 2>/dev/null").read().strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


def get_cpu_brand() -> str:
    """The real CPU brand string, e.g. 'Intel(R) Core(TM) i7-13700K'."""
    system = platform.system()
    if system == "Windows":
        brand = _cpu_brand_windows()
        if brand:
            return brand
    elif system == "Darwin":
        brand = _cpu_brand_mac()
        if brand:
            return brand
    else:
        brand = _cpu_brand_linux()
        if brand:
            return brand
    # Last-resort: processor() often reports 'Intel64 Family 6 Model …'.
    return platform.processor() or "Unknown CPU"


# --------------------------------------------------------------------------- #
# GPU
# --------------------------------------------------------------------------- #
def _gpu_nvml() -> dict | None:
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpus.append(
                {
                    "name": pynvml.nvmlDeviceGetName(handle),
                    "percent": float(util),
                    "used_gb": round(mem.used / 1024 ** 3, 1),
                    "total_gb": round(mem.total / 1024 ** 3, 1),
                }
            )
        return {"driver": "nvml", "gpus": gpus}
    except Exception:  # noqa: BLE001
        return None


def _gpu_wmi() -> list[dict] | None:
    """GPU names via the ``wmi`` package when installed."""
    try:
        import wmi  # type: ignore

        w = wmi.WMI()
        cards = []
        for card in w.Win32_VideoController():
            cards.append(
                {
                    "name": card.Name or "Unknown GPU",
                    "percent": None,
                    "used_gb": None,
                    "total_gb": None,
                    "driver_version": card.DriverVersion,
                }
            )
        return cards or None
    except Exception:  # noqa: BLE001
        return None


def _gpu_powershell_cim() -> list[dict] | None:
    """Windows fallback with zero extra deps — PowerShell CIM query."""
    if platform.system() != "Windows":
        return None
    try:
        import subprocess

        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name)"],
            capture_output=True, text=True, timeout=12,
        )
        if out.returncode != 0:
            return None
        names = [n.strip() for n in out.stdout.splitlines() if n.strip()]
        if not names:
            return None
        return [{"name": n, "percent": None, "used_gb": None, "total_gb": None, "driver_version": None} for n in names]
    except Exception:  # noqa: BLE001
        return None


def get_gpus() -> dict:
    """Real GPU info: NVML first, WMI on Windows as fallback, PowerShell CIM last."""
    nvml = _gpu_nvml()
    if nvml:
        return {"source": "nvml", "gpus": nvml["gpus"]}
    if platform.system() == "Windows":
        wmi_cards = _gpu_wmi()
        if wmi_cards:
            return {"source": "wmi", "gpus": wmi_cards}
        cim_cards = _gpu_powershell_cim()
        if cim_cards:
            return {"source": "cim", "gpus": cim_cards}
    return {"source": "none", "gpus": []}


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
def get_hardware_specs() -> dict:
    """Complete real-specs snapshot for the HUD's System Status panel."""
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    gpus = get_gpus()
    boot = psutil.boot_time()
    uptime_secs = max(0, time.time() - boot)

    battery = None
    try:
        bat = psutil.sensors_battery()
        if bat:
            battery = {
                "percent": round(bat.percent),
                "plugged": bool(bat.power_plugged),
            }
    except Exception:  # noqa: BLE001
        pass

    return {
        "hostname": platform.node(),
        "os": platform.system(),
        "os_version": platform.version(),
        "platform": platform.platform(),
        "cpu": {
            "brand": get_cpu_brand(),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "freq_mhz": round(psutil.cpu_freq().current, 0) if psutil.cpu_freq() else None,
            "arch": platform.machine(),
        },
        "gpu": gpus,
        "ram": {
            "total_gb": round(ram.total / 1024 ** 3, 1),
            "type": "DDR",  # psutil can't read DIMM type cross-platform
        },
        "storage": {
            "total_gb": round(disk.total / 1024 ** 3, 1),
            "used_gb": round(disk.used / 1024 ** 3, 1),
            "free_gb": round(disk.free / 1024 ** 3, 1),
        },
        "battery": battery,
        "uptime_seconds": uptime_secs,
        "python": platform.python_version(),
    }


def get_hardware_specs_cached(force: bool = False) -> dict:
    """Cached wrapper — registry/PowerShell lookups are only re-run every TTL."""
    now = time.time()
    with _LOCK:
        if not force and _CACHE.get("updated", 0) and (now - _CACHE["updated"]) < _CACHE_TTL:
            return dict(_CACHE)
    result = get_hardware_specs()
    result["updated"] = now
    with _LOCK:
        _CACHE.clear()
        _CACHE.update(result)
    return dict(_CACHE)
