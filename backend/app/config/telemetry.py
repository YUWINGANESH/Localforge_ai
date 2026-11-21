"""Lightweight cross-platform system and process memory telemetry."""
import ctypes
import os
import sys
from typing import Any


def get_memory_stats() -> dict[str, Any]:
    """
    Return current backend process RSS memory (MB) and host RAM stats without external dependencies.
    """
    rss_mb: float | None = None
    total_ram_gb: float | None = None
    available_ram_gb: float | None = None

    if sys.platform == "win32":
        try:
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            # Current process working set
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                rss_mb = round(pmc.WorkingSetSize / (1024 * 1024), 2)

            # Host RAM status
            mem_status = MEMORYSTATUSEX()
            mem_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem_status)):
                total_ram_gb = round(mem_status.ullTotalPhys / (1024**3), 2)
                available_ram_gb = round(mem_status.ullAvailPhys / (1024**3), 2)
        except Exception:
            pass
    else:
        try:
            with open("/proc/self/status", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        rss_mb = round(kb / 1024, 2)
                        break
        except Exception:
            pass

    return {
        "pid": os.getpid(),
        "process_rss_mb": rss_mb,
        "host_total_ram_gb": total_ram_gb,
        "host_available_ram_gb": available_ram_gb,
    }
