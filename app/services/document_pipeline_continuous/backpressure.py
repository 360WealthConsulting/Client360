"""CPU, memory and database backpressure for the worker pool.

The pipeline shares a Windows Server with the application that staff are using. OCR is the heaviest
thing the machine does, and a worker pool sized for throughput alone will happily take the box down
during tax season. So the pool asks this module before every claim, and when the answer is "not now"
it sleeps instead of claiming — which is the whole of the mechanism. There is no queue to drain, no
token bucket, no state: pressure is measured at the moment work would be started, because that is the
only moment the answer can change anything.

THREE GATES
-----------
* CPU — refuse to start new work above ``cpu_limit_percent``. Work already in flight finishes; only
  the next claim waits.
* MEMORY — the same, against system memory. OCR of a large scan is measured in hundreds of megabytes
  per worker, so this is the gate that matters on a 16 GB box.
* DATABASE — refuse when the SQLAlchemy pool has fewer than ``db_headroom`` connections left. This one
  protects the APPLICATION, not the pipeline: exhausting the pool makes staff pages hang, and a
  document that waits thirty seconds costs nobody anything.

psutil IS OPTIONAL
------------------
CPU and memory readings come from ``psutil``, which is listed in ``requirements.txt`` but may not be
installed on an older host. Its absence is reported honestly (``available: False``) and the CPU/memory
gates are SKIPPED rather than guessed — a fabricated 0% reading would be worse than no reading. The
database gate needs no third-party package and always applies, so the pool is never completely
un-governed.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.db import engine

log = logging.getLogger(__name__)

#: Start no new work above these. Defaults leave real headroom for the web application; they are
#: overridable through the environment (see ``app.config``).
DEFAULT_CPU_LIMIT_PERCENT = 85.0
DEFAULT_MEMORY_LIMIT_PERCENT = 85.0
#: Connections that must remain free in the pool for the rest of the application.
DEFAULT_DB_HEADROOM = 5

_psutil = None
_psutil_checked = False


def _load_psutil():
    """Import psutil once, tolerating its absence. Never raises."""
    global _psutil, _psutil_checked
    if not _psutil_checked:
        _psutil_checked = True
        try:
            import psutil  # noqa: PLC0415 — optional dependency, imported on first use
            _psutil = psutil
        except Exception:      # noqa: BLE001 — not installed, or unusable on this host
            _psutil = None
    return _psutil


def reset_psutil_cache() -> None:
    """Forget the psutil probe. Tests use this to exercise both the present and absent paths."""
    global _psutil, _psutil_checked
    _psutil, _psutil_checked = None, False


def system_pressure() -> dict:
    """Current CPU and memory load as percentages, or ``available: False`` when psutil is absent.

    ``cpu_percent(interval=None)`` is the non-blocking form: it reports load since the previous call
    rather than sleeping to sample. A worker loop calls this many times a minute, so a blocking sample
    would be pure latency."""
    psutil = _load_psutil()
    if psutil is None:
        return {"available": False, "cpu_percent": None, "memory_percent": None}
    try:
        return {"available": True,
                "cpu_percent": float(psutil.cpu_percent(interval=None)),
                "memory_percent": float(psutil.virtual_memory().percent)}
    except Exception as exc:      # noqa: BLE001 — a metrics read must never stop the pipeline
        log.debug("psutil reading failed: %s", exc)
        return {"available": False, "cpu_percent": None, "memory_percent": None}


def database_pressure(*, engine_=None) -> dict:
    """Free capacity in the SQLAlchemy connection pool.

    Reads the pool's own counters rather than querying the server: the number that matters is how many
    connections THIS process may still open before the application starts waiting."""
    db = engine_ or engine
    try:
        pool = db.pool
        size = int(pool.size())
        checked_out = int(pool.checkedout())
        overflow_limit = int(getattr(pool, "_max_overflow", 0) or 0)
        capacity = size + max(0, overflow_limit)
        return {"available": True, "pool_size": size, "checked_out": checked_out,
                "capacity": capacity, "free": max(0, capacity - checked_out)}
    except Exception as exc:      # noqa: BLE001 — non-pooled dialects and test doubles
        log.debug("connection-pool reading failed: %s", exc)
        return {"available": False, "pool_size": None, "checked_out": None,
                "capacity": None, "free": None}


def assess(*, cpu_limit_percent: float = DEFAULT_CPU_LIMIT_PERCENT,
           memory_limit_percent: float = DEFAULT_MEMORY_LIMIT_PERCENT,
           db_headroom: int = DEFAULT_DB_HEADROOM, engine_=None) -> dict:
    """Should the pool claim more work right now?

    Returns ``{ok, reason, ...readings}``. ``ok=False`` means "wait"; it never means "stop", and it is
    never an error — a pipeline that pauses under load is working correctly."""
    system = system_pressure()
    database = database_pressure(engine_=engine_)
    reading = {
        "cpu_percent": system["cpu_percent"], "memory_percent": system["memory_percent"],
        "system_metrics_available": system["available"],
        "db_free_connections": database["free"], "db_checked_out": database["checked_out"],
        "db_capacity": database["capacity"],
        "limits": {"cpu_percent": cpu_limit_percent, "memory_percent": memory_limit_percent,
                   "db_headroom": db_headroom},
    }
    if database["available"] and database["free"] is not None and database["free"] < db_headroom:
        return {**reading, "ok": False, "reason": "database_pool_headroom"}
    if system["available"]:
        if system["cpu_percent"] is not None and system["cpu_percent"] >= cpu_limit_percent:
            return {**reading, "ok": False, "reason": "cpu_saturated"}
        if system["memory_percent"] is not None and system["memory_percent"] >= memory_limit_percent:
            return {**reading, "ok": False, "reason": "memory_saturated"}
    return {**reading, "ok": True, "reason": None}


def legacy_ocr_sweep_active(conn, *, lock_key: int | None = None) -> bool:
    """Is one of the existing operational OCR sweeps (``app/jobs/ocr_runner.py``) running right now?

    The sweeps guard themselves with a session-level Postgres advisory lock so two of them never
    process the same corpus concurrently. The continuous pipeline is a THIRD writer of the same OCR
    state, and it was added long after those sweeps shipped, so it yields to them: it reads the lock
    without taking it and defers its own OCR stage while a sweep holds it. Reading rather than taking
    is deliberate — taking the lock would serialise the entire worker pool behind one connection, and
    the pipeline's per-document leases already give it the mutual exclusion it needs among its own
    workers.

    A bigint advisory key is stored split across ``pg_locks.classid``/``objid``. PostgreSQL advisory
    locks are CLUSTER-wide, not per-database, and this probe is too: a sweep running against any
    database on the same server is detected, and the pipeline defers. That is the conservative
    direction — the sweeps and the pipeline share a machine and an OCR engine, not just a schema."""
    if lock_key is None:
        from app.jobs.ocr_runner import _OCR_LOCK_KEY
        lock_key = _OCR_LOCK_KEY
    key = int(lock_key)
    try:
        return bool(conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_locks
                 WHERE locktype = 'advisory'
                   AND granted
                   AND classid = :classid
                   AND objid = :objid
            )
        """), {"classid": (key >> 32) & 0xFFFFFFFF, "objid": key & 0xFFFFFFFF}).scalar())
    except Exception as exc:      # noqa: BLE001 — a lock probe must never stop the pipeline
        log.debug("advisory-lock probe failed: %s", exc)
        return False
