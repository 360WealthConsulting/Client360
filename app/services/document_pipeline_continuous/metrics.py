"""Operational metrics, stall detection, and the alert a stalled pipeline raises.

A continuous pipeline that nobody is watching is a pipeline that stopped three days ago. These are the
numbers that answer the only two questions an operator actually has — "is it moving?" and "what needs
a human?" — and the stall detector turns the first one into something that pages instead of something
somebody remembers to check.

WHAT "STALLED" MEANS HERE
-------------------------
Not "idle". An empty queue with no workers is a correctly stopped pipeline, and calling that an
incident trains people to ignore the alert. Stalled means all three of:

1. there is work to do (``backlog > 0``),
2. nothing has completed inside the stall window, and
3. either no worker has heartbeated inside the window, or every worker that has is holding the same
   document it was holding at the start of it.

Condition 3 is what separates "wedged on one pathological scan" from "grinding through a big one":
a live worker updates its heartbeat DURING a document, so a worker that is genuinely working reads as
alive even when nothing has completed for twenty minutes.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import text

from app.db import engine
from app.services.document_pipeline_continuous import discovery, queue
from app.services.document_pipeline_continuous.model import installed

log = logging.getLogger(__name__)

#: No completion and no fresh heartbeat for this long, with a backlog waiting, is a stall.
DEFAULT_STALL_SECONDS = 900
#: A worker that has not heartbeated for this long is presumed dead by the health check (the lease,
#: not this number, is what actually frees its work).
DEFAULT_WORKER_TIMEOUT_SECONDS = 300
#: Throughput windows reported, in minutes.
THROUGHPUT_WINDOWS = (1, 5, 60)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def counters(conn) -> dict:
    """Task counts by state and by ownership outcome, in one pass over one table."""
    rows = conn.execute(text("""
        SELECT state, outcome, count(*) AS total
          FROM document_pipeline_tasks
         GROUP BY state, outcome
    """)).mappings().all()
    by_state: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    for row in rows:
        by_state[row["state"]] = by_state.get(row["state"], 0) + int(row["total"])
        if row["outcome"]:
            by_outcome[row["outcome"]] = by_outcome.get(row["outcome"], 0) + int(row["total"])
    return {"by_state": by_state, "by_outcome": by_outcome}


def throughput(conn, *, windows=THROUGHPUT_WINDOWS) -> dict:
    """Documents completed per window, and the derived per-minute rate.

    Read from ``completed_at`` rather than from a counter, so a restarted worker does not reset the
    firm's view of how fast the pipeline is going."""
    result = {}
    for minutes in windows:
        total = int(conn.execute(text("""
            SELECT count(*) FROM document_pipeline_tasks
             WHERE completed_at IS NOT NULL
               AND completed_at >= now() - make_interval(mins => :minutes)
        """), {"minutes": int(minutes)}).scalar() or 0)
        result[f"last_{minutes}m"] = total
        result[f"per_minute_{minutes}m"] = round(total / minutes, 3)
    return result


def workers(conn, *, worker_timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS) -> dict:
    """Registered workers, and how long ago each was last heard from."""
    rows = conn.execute(text("""
        SELECT worker_id, host, pid, state, current_document_id, claimed_total, completed_total,
               failed_total, started_at, last_heartbeat_at, stopped_at,
               EXTRACT(EPOCH FROM (now() - last_heartbeat_at)) AS heartbeat_age_seconds
          FROM document_pipeline_workers
         ORDER BY last_heartbeat_at DESC
    """)).mappings().all()
    listed = []
    live = 0
    for row in rows:
        age = float(row["heartbeat_age_seconds"] or 0.0)
        alive = row["state"] in ("starting", "running", "draining") and age <= worker_timeout_seconds
        live += 1 if alive else 0
        listed.append({"worker_id": row["worker_id"], "host": row["host"], "pid": row["pid"],
                       "state": row["state"], "alive": alive,
                       "current_document_id": row["current_document_id"],
                       "claimed_total": int(row["claimed_total"] or 0),
                       "completed_total": int(row["completed_total"] or 0),
                       "failed_total": int(row["failed_total"] or 0),
                       "started_at": _iso(row["started_at"]),
                       "last_heartbeat_at": _iso(row["last_heartbeat_at"]),
                       "heartbeat_age_seconds": round(age, 1)})
    newest = listed[0]["last_heartbeat_at"] if listed else None
    return {"workers": listed, "live": live, "registered": len(listed), "last_heartbeat_at": newest,
            "last_heartbeat_age_seconds": listed[0]["heartbeat_age_seconds"] if listed else None}


def queue_depth(conn) -> dict:
    """Where the pending work is sitting, by stage — the shape of the backlog, not just its size."""
    rows = conn.execute(text("""
        SELECT stage, count(*) AS total
          FROM document_pipeline_tasks
         WHERE state IN ('queued', 'leased')
         GROUP BY stage
    """)).mappings().all()
    return {row["stage"]: int(row["total"]) for row in rows}


def blocker_reasons(conn, *, limit: int = 20) -> list[dict]:
    """Open blockers grouped by reason, worst first. The one view that says what to go fix."""
    rows = conn.execute(text("""
        SELECT reason_code, stage, count(*) AS total
          FROM document_pipeline_blockers
         WHERE status = 'open'
         GROUP BY reason_code, stage
         ORDER BY total DESC
         LIMIT :limit
    """), {"limit": int(limit)}).mappings().all()
    return [{"reason_code": row["reason_code"], "stage": row["stage"], "documents": int(row["total"])}
            for row in rows]


def review_lanes(conn) -> dict:
    """Open ownership reviews by lane."""
    rows = conn.execute(text("""
        SELECT lane, count(*) AS total
          FROM document_pipeline_ownership_reviews
         WHERE status = 'open'
         GROUP BY lane
    """)).mappings().all()
    return {row["lane"]: int(row["total"]) for row in rows}


def snapshot(conn=None, *, worker_timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS) -> dict:
    """The full operational picture: backlog, running, completed, linked, review, blocked, failed,
    throughput and last heartbeat, plus the discovery cursor behind them all."""
    if conn is None:
        with engine.connect() as own:
            return snapshot(own, worker_timeout_seconds=worker_timeout_seconds)
    if not installed():
        return {"installed": False, "reason": "the docpipe01 migration has not been applied"}

    counts = counters(conn)
    by_state, by_outcome = counts["by_state"], counts["by_outcome"]
    worker_view = workers(conn, worker_timeout_seconds=worker_timeout_seconds)
    backlog_view = discovery.backlog_estimate(conn)

    queued = by_state.get("queued", 0)
    running = by_state.get("leased", 0)
    return {
        "installed": True,
        "generated_at": _now().isoformat(),
        # The nine headline numbers.
        "backlog": queued + backlog_view["undiscovered"],
        "queued": queued,
        "undiscovered": backlog_view["undiscovered"],
        "running": running,
        "completed": by_state.get("succeeded", 0),
        "linked": by_outcome.get("linked", 0),
        "review": by_state.get("review", 0),
        "blocked": by_state.get("blocked", 0),
        "failed": by_state.get("failed", 0),
        "throughput": throughput(conn),
        "last_heartbeat_at": worker_view["last_heartbeat_at"],
        # Supporting detail.
        "unresolved": by_outcome.get("unresolved", 0),
        "already_owned": by_outcome.get("already_owned", 0),
        "queue_depth_by_stage": queue_depth(conn),
        "blocker_reasons": blocker_reasons(conn),
        "review_lanes": review_lanes(conn),
        "workers": worker_view,
        "discovery": {k: _iso(v) for k, v in backlog_view.items()},
    }


# --- health / stall detection --------------------------------------------------------------------

def health(conn=None, *, stall_seconds: int = DEFAULT_STALL_SECONDS,
           worker_timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS,
           snapshot_=None) -> dict:
    """Is the pipeline healthy, idle, or stalled? See the module docstring for what 'stalled' means."""
    if conn is None and snapshot_ is None:
        with engine.connect() as own:
            return health(own, stall_seconds=stall_seconds,
                          worker_timeout_seconds=worker_timeout_seconds)
    view = snapshot_ or snapshot(conn, worker_timeout_seconds=worker_timeout_seconds)
    if not view.get("installed"):
        return {"status": "not_installed", "healthy": False, "reason": view.get("reason"),
                "stall_seconds": stall_seconds}

    backlog = int(view["backlog"])
    running = int(view["running"])
    live_workers = int(view["workers"]["live"])
    heartbeat_age = view["workers"]["last_heartbeat_age_seconds"]
    completed_recently = int(view["throughput"][f"last_{max(THROUGHPUT_WINDOWS)}m"])

    if backlog == 0 and running == 0:
        # Nothing to do. A pipeline with an empty queue is finished, not broken — whether or not a
        # worker happens to be running right now.
        return {"status": "idle", "healthy": True, "reason": "no backlog", "backlog": 0,
                "stall_seconds": stall_seconds}

    if live_workers == 0:
        return {"status": "stopped", "healthy": False,
                "reason": f"{backlog} document(s) waiting and no live worker",
                "backlog": backlog, "live_workers": 0, "stall_seconds": stall_seconds}

    stalled = (heartbeat_age is not None and heartbeat_age > stall_seconds
               and completed_recently == 0)
    if stalled:
        return {"status": "stalled", "healthy": False,
                "reason": (f"no heartbeat for {int(heartbeat_age)}s and nothing completed in "
                           f"{max(THROUGHPUT_WINDOWS)}m with {backlog} document(s) waiting"),
                "backlog": backlog, "live_workers": live_workers,
                "last_heartbeat_age_seconds": heartbeat_age, "stall_seconds": stall_seconds}

    return {"status": "healthy", "healthy": True, "reason": None, "backlog": backlog,
            "live_workers": live_workers, "last_heartbeat_age_seconds": heartbeat_age,
            "stall_seconds": stall_seconds}


def raise_stall_alert(status: dict, *, actor_user_id=None) -> dict | None:
    """Raise an observability alert for a stalled or stopped pipeline. Returns the alert, or None.

    The alert code carries the minute the stall was detected, so a stall that persists produces one
    alert rather than one per check — the existing alert store rejects a duplicate code, and that
    rejection is the deduplication. Never raises: a monitoring failure must not stop the pipeline."""
    if status.get("healthy"):
        return None
    stamp = _now().strftime("%Y%m%dT%H%MZ")
    code = f"document_pipeline.{status.get('status', 'unhealthy')}.{stamp}"
    try:
        from app.services.observability.alerts import raise_alert
        return raise_alert(None, code=code, severity="critical",
                           title=f"Document pipeline {status.get('status')}",
                           detail=status.get("reason"), actor_user_id=actor_user_id)
    except Exception as exc:      # noqa: BLE001 — duplicate code, or observability unavailable
        log.warning("document pipeline %s: %s (alert not recorded: %s)",
                    status.get("status"), status.get("reason"), exc)
        return None


def check_and_alert(*, stall_seconds: int = DEFAULT_STALL_SECONDS,
                    worker_timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS) -> dict:
    """One monitoring tick: evaluate health and raise an alert if the pipeline is not progressing."""
    status = health(stall_seconds=stall_seconds, worker_timeout_seconds=worker_timeout_seconds)
    if not status.get("healthy") and status.get("status") != "not_installed":
        log.error("document pipeline %s: %s", status.get("status"), status.get("reason"))
        alert = raise_stall_alert(status)
        status["alert_raised"] = alert is not None
    return status


def open_blockers(*, limit: int = 100, offset: int = 0) -> list[dict]:
    """The visible blocker queue, for the operations surface."""
    with engine.connect() as conn:
        rows = queue.open_blockers(conn, limit=limit, offset=offset)
    for row in rows:
        row["first_seen_at"] = _iso(row.get("first_seen_at"))
        row["last_seen_at"] = _iso(row.get("last_seen_at"))
    return rows


def open_reviews(*, lane: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """The one ownership review queue, for the operations surface."""
    with engine.connect() as conn:
        rows = queue.open_reviews(conn, lane=lane, limit=limit, offset=offset)
    for row in rows:
        row["opened_at"] = _iso(row.get("opened_at"))
        row["updated_at"] = _iso(row.get("updated_at"))
    return rows
