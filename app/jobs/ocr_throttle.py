"""Admission control for parallel OCR workers.

Parallel OCR exists to use spare capacity, never to take capacity the Client360 web application
needs. This module answers one question — *may a worker claim more work right now?* — and it is
consulted before each claim, never mid-document. A worker that is told to hold finishes the document
in its hand, releases nothing it has already completed, and simply stops claiming until the answer
changes, so throttling can never corrupt in-flight work.

Three independent gates, any one of which pauses new claims:

* **Client360 health** — if the application's health endpoint is failing, OCR stops taking new work
  immediately. Configured but unreachable is treated as unhealthy (fail closed); not configured at
  all is treated as "no opinion" (fail open), so an operator who has not wired a URL does not get a
  worker that refuses to start.
* **Memory floor** — mirrors ``worker.py``'s existing ``MIN_FREE_MB`` behaviour rather than
  inventing a second policy.
* **CPU ceiling** — parallel workers are the only reason this gate is needed; the single worker
  could not saturate the box.

No new dependency: memory and CPU both come from Win32 via ctypes, the same approach ``worker.py``
already uses for memory.
"""
from __future__ import annotations

import ctypes
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

#: Defaults chosen to match worker.py where an equivalent knob already exists.
DEFAULT_MIN_FREE_MB = 2048
DEFAULT_MAX_CPU_PERCENT = 85.0
DEFAULT_HEALTH_TIMEOUT = 5.0
DEFAULT_WORKERS = 2

#: Never exceed the physical core count: OCR is CPU-bound and hyperthread siblings buy little while
#: doubling memory pressure. An explicit override is still honoured, with a warning from the caller.
DEFAULT_MAX_WORKERS = 4


def _env_int(name, default):
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name, default):
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def configured_workers() -> int:
    """Worker count from ``OCR_PARALLEL_WORKERS``, clamped to at least 1."""
    return max(1, _env_int("OCR_PARALLEL_WORKERS", DEFAULT_WORKERS))


# --- host probes ------------------------------------------------------------------------------

class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def free_mb() -> int:
    """Available physical memory in MB. Returns a large number off-Windows so tests are portable."""
    if not hasattr(ctypes, "windll"):
        return 1 << 20
    s = _MemStatus()
    s.dwLength = ctypes.sizeof(_MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return int(s.ullAvailPhys // (1024 * 1024))


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


def _system_times():
    idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel),
                                          ctypes.byref(user))
    to_i = lambda f: (f.high << 32) | f.low  # noqa: E731
    return to_i(idle), to_i(kernel), to_i(user)


def cpu_percent(sample_seconds: float = 0.25) -> float:
    """System-wide CPU utilisation over a short sample. 0.0 off-Windows."""
    if not hasattr(ctypes, "windll"):
        return 0.0
    i0, k0, u0 = _system_times()
    time.sleep(max(0.05, sample_seconds))
    i1, k1, u1 = _system_times()
    busy = (k1 - k0) + (u1 - u0) - (i1 - i0)
    total = (k1 - k0) + (u1 - u0)
    return 0.0 if total <= 0 else max(0.0, min(100.0, 100.0 * busy / total))


def health_ok(url=None, timeout=DEFAULT_HEALTH_TIMEOUT):
    """(ok, detail). ``None`` url means "not configured" — no opinion, so admission is not blocked."""
    url = url if url is not None else os.getenv("CLIENT360_HEALTH_URL", "").strip()
    if not url:
        return True, "health check not configured"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:   # noqa: S310 — operator-set URL
            code = resp.getcode()
            if 200 <= code < 300:
                return True, f"health {code}"
            return False, f"health returned {code}"
    except urllib.error.URLError as exc:
        return False, f"health unreachable: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — any failure to confirm health is a reason to hold
        return False, f"health probe failed: {exc.__class__.__name__}"


# --- the decision -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str
    free_mb: int
    cpu_percent: float

    def __bool__(self) -> bool:
        return self.allowed


def may_claim(*, min_free_mb=None, max_cpu_percent=None, health_url=None,
              sample_seconds=0.25) -> Admission:
    """May a worker claim more documents right now?

    Checked before claiming only. An in-flight document always runs to completion, so a throttle
    can never truncate OCR or leave a half-written result.
    """
    min_free_mb = min_free_mb if min_free_mb is not None else _env_int("OCR_MIN_FREE_MB",
                                                                      DEFAULT_MIN_FREE_MB)
    max_cpu = max_cpu_percent if max_cpu_percent is not None else _env_float(
        "OCR_MAX_CPU_PERCENT", DEFAULT_MAX_CPU_PERCENT)

    ok, detail = health_ok(health_url)
    mem = free_mb()
    if not ok:
        return Admission(False, f"Client360 health gate: {detail}", mem, -1.0)

    if mem < min_free_mb:
        return Admission(False, f"free memory {mem} MB below floor {min_free_mb} MB", mem, -1.0)

    cpu = cpu_percent(sample_seconds)
    if cpu > max_cpu:
        return Admission(False, f"CPU {cpu:.1f}% above ceiling {max_cpu:.1f}%", mem, cpu)

    return Admission(True, f"ok ({detail}; {mem} MB free; CPU {cpu:.1f}%)", mem, cpu)


def lower_priority() -> bool:
    """BELOW_NORMAL, so the Client360 web application always wins the CPU. Mirrors worker.py."""
    if not hasattr(ctypes, "windll"):
        return False
    try:
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        return bool(ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))
    except Exception:  # noqa: BLE001 — priority is an optimisation, never a reason to stop
        return False
