"""The facade: one place the scheduler, the CLI, the Windows service and the routes all call.

Everything below composes the modules in this package with the settings from ``app.config``. Keeping
the composition here means the four entry points cannot drift into four slightly different pipelines —
a scheduler tick and a dedicated service host run exactly the same code with a different loop around
it.
"""
from __future__ import annotations

import logging
import threading

from app.db import engine
from app.services.document_pipeline_continuous import discovery, metrics, queue, worker
from app.services.document_pipeline_continuous.model import PipelineNotInstalled, installed

log = logging.getLogger(__name__)


def _settings() -> dict:
    """Resolve the pipeline's runtime settings from the environment, once per call."""
    from app.config import (
        document_pipeline_batch_size,
        document_pipeline_cpu_limit_percent,
        document_pipeline_db_headroom,
        document_pipeline_discovery_page_size,
        document_pipeline_lease_seconds,
        document_pipeline_max_attempts,
        document_pipeline_memory_limit_percent,
        document_pipeline_stall_seconds,
        document_pipeline_worker_count,
        document_pipeline_worker_timeout_seconds,
    )
    return {
        "workers": document_pipeline_worker_count(),
        "batch_size": document_pipeline_batch_size(),
        "lease_seconds": document_pipeline_lease_seconds(),
        "max_attempts": document_pipeline_max_attempts(),
        "page_size": document_pipeline_discovery_page_size(),
        "stall_seconds": document_pipeline_stall_seconds(),
        "worker_timeout_seconds": document_pipeline_worker_timeout_seconds(),
        "pressure_limits": {
            "cpu_limit_percent": document_pipeline_cpu_limit_percent(),
            "memory_limit_percent": document_pipeline_memory_limit_percent(),
            "db_headroom": document_pipeline_db_headroom(),
        },
    }


def _worker_kwargs(overrides: dict | None = None) -> dict:
    """The configured worker settings, with any caller override winning.

    Built as a dict rather than spread as keyword arguments so an override of a setting the facade
    also supplies replaces it instead of colliding with it — passing ``batch_size=`` to :func:`drain`
    should reconfigure the worker, not raise ``got multiple values for keyword argument``."""
    settings = _settings()
    kwargs = {
        "batch_size": settings["batch_size"],
        "lease_seconds": settings["lease_seconds"],
        "max_attempts": settings["max_attempts"],
        "pressure_limits": settings["pressure_limits"],
    }
    kwargs.update(overrides or {})
    return kwargs


def require_installed() -> None:
    if not installed():
        raise PipelineNotInstalled(
            "the continuous document pipeline is not installed in this database. "
            "Apply the docpipe01 migration (`alembic upgrade head`) first.")


# --- discovery ------------------------------------------------------------------------------------

def discover(*, max_pages: int | None = None, page_size: int | None = None) -> dict:
    """Find every new and changed document and turn it into queued work.

    ``max_pages=None`` drains discovery completely — the backlog is never truncated to a fixed number
    of documents. A caller with a time budget passes a page count and the next call resumes."""
    require_installed()
    settings = _settings()
    return discovery.discover(page_size=page_size or settings["page_size"], max_pages=max_pages,
                              max_attempts=settings["max_attempts"])


# --- processing -----------------------------------------------------------------------------------

def drain(*, max_passes: int | None = None, **overrides) -> dict:
    """Process queued work until the queue is empty (or backpressure asks for a pause), then return.

    Unbounded by default: the queue emptying is what ends it, not a document count. A caller with a
    time budget — the scheduler tick — passes a pass count, and the next call resumes."""
    require_installed()
    return worker.drain(max_passes=max_passes, **_worker_kwargs(overrides))


def tick(*, discovery_pages: int | None = 5, max_passes: int = 200) -> dict:
    """One complete scheduler tick: discover, then process what was discovered.

    Bounded so the tick returns to the scheduler promptly. The bound costs nothing, because the next
    tick resumes from the persisted cursor and the untouched queue — which is the difference between
    a bounded TICK and a truncated BACKLOG."""
    require_installed()
    found = discover(max_pages=discovery_pages)
    processed = drain(max_passes=max_passes)
    return {"discovery": found, "processing": processed}


def run_service(*, stop_event: threading.Event | None = None, workers: int | None = None,
                discovery_interval_seconds: int = 60, **overrides) -> dict:
    """Run the pipeline continuously until ``stop_event`` is set. The dedicated service host's body.

    A discovery thread and ``workers`` processing threads. Discovery is separated from processing on
    purpose: a slow discovery pass over a large corpus must never stop the workers from draining what
    has already been found."""
    require_installed()
    settings = _settings()
    stop_event = stop_event or threading.Event()
    worker_count = workers if workers is not None else settings["workers"]

    def _discovery_loop():
        while not stop_event.is_set():
            try:
                discover()
            except Exception:      # noqa: BLE001 — discovery failing must not stop processing
                log.exception("document pipeline discovery pass failed")
            stop_event.wait(discovery_interval_seconds)

    discovery_thread = threading.Thread(target=_discovery_loop, name="docpipe-discovery", daemon=True)
    discovery_thread.start()
    try:
        results = worker.run_pool(workers=worker_count, stop_event=stop_event,
                                  **_worker_kwargs(overrides))
    finally:
        stop_event.set()
        discovery_thread.join(timeout=5)
    return {"workers": results}


# --- operations -----------------------------------------------------------------------------------

def status() -> dict:
    """The metrics snapshot the operations surfaces read."""
    settings = _settings() if installed() else {"worker_timeout_seconds":
                                                metrics.DEFAULT_WORKER_TIMEOUT_SECONDS}
    return metrics.snapshot(worker_timeout_seconds=settings["worker_timeout_seconds"])


def health() -> dict:
    """Healthy / idle / stopped / stalled, with the reason."""
    if not installed():
        return {"status": "not_installed", "healthy": False,
                "reason": "the docpipe01 migration has not been applied"}
    settings = _settings()
    return metrics.health(stall_seconds=settings["stall_seconds"],
                          worker_timeout_seconds=settings["worker_timeout_seconds"])


def monitor() -> dict:
    """Evaluate health and raise an alert when the pipeline has stopped progressing."""
    if not installed():
        return {"status": "not_installed", "healthy": False}
    settings = _settings()
    return metrics.check_and_alert(stall_seconds=settings["stall_seconds"],
                                   worker_timeout_seconds=settings["worker_timeout_seconds"])


def blockers(*, limit: int = 100, offset: int = 0) -> list[dict]:
    require_installed()
    return metrics.open_blockers(limit=limit, offset=offset)


def reviews(*, lane: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    require_installed()
    return metrics.open_reviews(lane=lane, limit=limit, offset=offset)


def requeue_document(document_id: int, *, actor_user_id=None, request_id=None) -> bool:
    """Explicitly put one document back through the pipeline — the operator's escape hatch after a
    blocker is fixed. Deliberately separate from resolving the blocker: "I looked at it" and "process
    it again" are different decisions."""
    require_installed()
    with engine.begin() as conn:
        sha = queue.document_content_hash(conn, document_id)
        moved = queue.requeue(conn, document_id, content_sha256=sha, reason="operator_requeue")
        if moved:
            queue.resolve_blocker(conn, document_id=document_id, status="resolved",
                                  actor_user_id=actor_user_id, note="re-queued by operator",
                                  request_id=request_id)
    return moved
