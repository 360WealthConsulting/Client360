"""Admission control for parallel OCR workers.

Parallel OCR exists to use spare capacity, never to take capacity the Client360 web application
needs. This module answers one question — *may a worker claim more work right now?* — and it is
consulted before each claim, never mid-document. A worker that is told to hold finishes the document
in its hand, releases nothing it has already completed, and simply stops claiming until the answer
changes, so throttling can never corrupt in-flight work.

Three independent gates, any one of which pauses new claims:

* **Client360 health — FAIL CLOSED, and on by default.** Both ``/health`` and ``/readiness`` must
  answer 200 with a healthy status. Unreachable, non-200, or a body reporting anything else pauses
  claiming. This needs NO configuration to be correct in production: the defaults already point at
  the local application, so an operator who sets nothing still gets the protective behaviour rather
  than a gate that silently does nothing. Explicit overrides exist for tests and for deployments
  that do not listen on the default port.
* **Memory floor** — mirrors ``worker.py``'s existing ``MIN_FREE_MB`` behaviour rather than
  inventing a second policy.
* **CPU ceiling** — parallel workers are the only reason this gate is needed; the single worker
  could not saturate the box.

No new dependency: memory and CPU both come from Win32 via ctypes, the same approach ``worker.py``
already uses for memory.
"""
from __future__ import annotations

import ctypes
import json
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

#: Both endpoints are checked, and BOTH must pass. /health says the process is up; /readiness says
#: its database, migrations, configuration, storage and scheduler are actually usable — which is the
#: one that matters while OCR is writing. Checked by default so the production-safe behaviour needs
#: no environment variable to exist.
DEFAULT_HEALTH_URLS = ("http://127.0.0.1:8360/health",
                       "http://127.0.0.1:8360/readiness")

#: Body ``status`` values that count as healthy. A 200 carrying anything else is NOT healthy.
HEALTHY_STATUSES = frozenset({"ok", "ready", "healthy", "pass", "up", "green"})

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


def configured_workers(cap=None) -> int:
    """Worker count from ``OCR_PARALLEL_WORKERS``, clamped to [1, cap].

    ``cap`` defaults to :data:`DEFAULT_MAX_WORKERS` — the box's physical core count. OCR is
    CPU-bound, so oversubscribing buys nothing and takes capacity the web application needs; this
    deployment is therefore capped rather than merely warned about.
    """
    cap = DEFAULT_MAX_WORKERS if cap is None else int(cap)
    return max(1, min(cap, _env_int("OCR_PARALLEL_WORKERS", DEFAULT_WORKERS)))


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


def health_gate_enabled() -> bool:
    """The gate is ON unless deliberately switched off.

    ``OCR_HEALTH_GATE=0`` exists for test environments and for a diagnostic run against a host with
    no application listening. It is NOT a production setting: switching it off removes the only
    thing that stops OCR claiming work while Client360 is unhealthy.
    """
    return os.getenv("OCR_HEALTH_GATE", "1").strip().lower() not in {"0", "false", "no", "off"}


def configured_health_urls():
    """The endpoints to probe. Defaults are production-correct with nothing set.

    ``CLIENT360_HEALTH_URLS`` (comma separated) overrides the pair for a deployment on another
    port. An EXPLICIT empty value is an explicit opt-out and is honoured as such — unlike an unset
    variable, which yields the protective defaults.
    """
    raw = os.getenv("CLIENT360_HEALTH_URLS")
    if raw is None:
        legacy = os.getenv("CLIENT360_HEALTH_URL")          # single-URL form, still honoured
        if legacy is not None:
            return tuple(u for u in [legacy.strip()] if u)
        return DEFAULT_HEALTH_URLS
    return tuple(u.strip() for u in raw.split(",") if u.strip())


def _probe(url, timeout):
    """(ok, detail) for ONE endpoint. Anything short of a 200 with a healthy body is not ok."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:   # noqa: S310 — operator-set URL
            code = resp.getcode()
            if not 200 <= code < 300:
                return False, f"{url} returned {code}"
            body = resp.read(8192).decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        return False, f"{url} unreachable: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — any failure to CONFIRM health is a reason to hold
        return False, f"{url} probe failed: {exc.__class__.__name__}"

    # A 200 is necessary but not sufficient: /readiness answers 200 while reporting a database or
    # migration problem, and that is exactly when OCR must stop claiming.
    try:
        status = (json.loads(body) or {}).get("status")
    except (ValueError, AttributeError):
        return True, f"{url} 200 (no JSON status)"
    if status is None:
        return True, f"{url} 200"
    if str(status).strip().lower() in HEALTHY_STATUSES:
        return True, f"{url} 200 {status}"
    return False, f"{url} reported status={status!r}"


def health_ok(urls=None, timeout=DEFAULT_HEALTH_TIMEOUT):
    """(ok, detail). FAIL CLOSED: every endpoint must answer 200 with a healthy status.

    ``urls=None`` uses the configured/default pair. An explicit empty sequence disables the check.
    """
    if not health_gate_enabled():
        return True, "health gate disabled (OCR_HEALTH_GATE=0)"
    if urls is None:
        urls = configured_health_urls()
    elif isinstance(urls, str):
        urls = tuple(u for u in [urls.strip()] if u)
    if not urls:
        return True, "health check explicitly disabled (no URLs)"

    details = []
    for url in urls:
        ok, detail = _probe(url, timeout)
        details.append(detail)
        if not ok:
            return False, detail          # one bad endpoint is enough to hold
    return True, "; ".join(details)


# --- the decision -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str
    free_mb: int
    cpu_percent: float

    def __bool__(self) -> bool:
        return self.allowed


def may_claim(*, min_free_mb=None, max_cpu_percent=None, health_url=None, health_urls=None,
              sample_seconds=0.25) -> Admission:
    """May a worker claim more documents right now?

    Checked before claiming only. An in-flight document always runs to completion, so a throttle
    can never truncate OCR or leave a half-written result.
    """
    min_free_mb = min_free_mb if min_free_mb is not None else _env_int("OCR_MIN_FREE_MB",
                                                                      DEFAULT_MIN_FREE_MB)
    max_cpu = max_cpu_percent if max_cpu_percent is not None else _env_float(
        "OCR_MAX_CPU_PERCENT", DEFAULT_MAX_CPU_PERCENT)

    probe = health_urls if health_urls is not None else health_url
    ok, detail = health_ok(probe)
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
