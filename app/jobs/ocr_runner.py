"""Operational OCR runners (PR 5B) — initial / incremental / retry sweeps for Windows Server.

Thin operational layer over :func:`app.services.document_ocr.run_ocr` (which owns state, retry,
idempotency, search, and audit). Adds what a production runner needs and the OCR service deliberately
does not: resumable batching to completion, per-batch progress logging, and a PostgreSQL advisory lock
so two runners (a scheduled task overlapping a manual run) never process the same corpus concurrently.

Modes:
- ``initial`` / ``incremental`` — sweep every not-yet-attempted document to completion, one batch at a
  time (resumable: a re-run simply skips what is already done).
- ``retry`` — one batch of failed documents (attempts < max_attempts) per invocation, so retries spread
  across scheduled runs rather than burning all attempts at once.
- ``reprocess`` — force re-OCR (content changed or explicit), swept to completion.

The extraction engine is the production backend by default; when it is not installed the runner returns
``status="backend_unavailable"`` without touching any document. Tests inject a fake ``extractor``.

CLI::

    python -m app.jobs.ocr_runner --mode initial
    python -m app.jobs.ocr_runner --mode incremental --batch-size 50
    python -m app.jobs.ocr_runner --mode retry --max-attempts 3
    python -m app.jobs.ocr_runner --mode reprocess
"""
from __future__ import annotations

import contextlib
import logging
import os

from sqlalchemy import text

from app.db import engine
from app.services.document_ocr import OcrBackendUnavailable

log = logging.getLogger(__name__)

# Session-level advisory lock key (arbitrary, namespaced to OCR). Guards against concurrent sweeps.
_OCR_LOCK_KEY = 511_005_002
_ACCUM = ("candidates", "completed", "failed", "timed_out", "skipped", "unsupported", "encrypted",
          "chars_extracted")

# The production extraction backend as a picklable dotted reference, so the per-document child process
# builds it itself (spawn-safe; the child never imports app.db).
_PRODUCTION_FACTORY = "app.services.ocr_backend.build_production_extractor"


def _isolation_enabled() -> bool:
    """Per-document subprocess isolation (real wall-clock hard cap + stall watchdog). ON BY DEFAULT — the
    single gate every production OCR entrypoint consults (the operational runner, the SharePoint baseline
    live-OCR path, and the ``python -m app.services.document_ocr`` CLI), so a pathological document can
    never freeze the parent process.

    ``OCR_SUBPROCESS_ISOLATION=0`` is a DIAGNOSTICS-ONLY escape hatch (run extraction in-process, no
    wall-clock timeout). It must never be set in production: without isolation a single wedged PDF/image
    hangs the whole run. Unset / ``1`` / ``true`` / ``yes`` / ``on`` all mean isolated (the default)."""
    return os.getenv("OCR_SUBPROCESS_ISOLATION", "1").strip().lower() in {"1", "true", "yes", "on"}


def _status_enabled(status) -> bool:
    """Persistent run-status/heartbeat tracking. On by default in production so a resumed migration is
    observable (working vs. frozen). Explicit ``status`` arg overrides the ``OCR_STATUS_ENABLED`` env."""
    if status is not None:
        return bool(status)
    return os.getenv("OCR_STATUS_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}


def _default_run_id(mode) -> str:
    from datetime import UTC, datetime
    return f"ocr-{mode}-{os.getpid()}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"


def _make_tracker(mode, *, document_ids, max_attempts, status_path, run_id, clock):
    """Build a run tracker with a best-effort candidate total. Tracking never fails the run: if the
    count query errors, the total is simply left unknown."""
    from app.jobs import ocr_status
    from app.services import document_ocr
    try:
        total = document_ocr.count_candidates(mode=mode, document_ids=document_ids,
                                              max_attempts=max_attempts)
    except Exception:  # noqa: BLE001 — a denominator is nice-to-have, never required
        total = None
    return ocr_status.OcrRunTracker(status_path, run_id=run_id or _default_run_id(mode),
                                    mode=mode, total=total, clock=clock)


@contextlib.contextmanager
def _advisory_lock():
    """Hold a Postgres session advisory lock for the duration of a sweep. Yields True if acquired."""
    conn = engine.connect()
    got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _OCR_LOCK_KEY}).scalar())
    try:
        yield got
    finally:
        if got:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _OCR_LOCK_KEY})
        conn.close()


def run_sweep(mode="incremental", *, document_ids=None, extractor=None, batch_size=50,
              max_batches=10_000, loop=True, actor_user_id=None, request_id=None,
              progress=None, isolate=None, factory_ref=None,
              status=None, status_path=None, run_id=None, clock=None) -> dict:
    """Run an OCR sweep. Resumable + concurrency-safe. Returns accumulated counts + status.

    ``loop=True`` processes batches until nothing remains (initial/incremental/reprocess);
    ``loop=False`` runs a single batch (retry). ``document_ids`` targets a specific set (a single pass,
    used for a forced reprocess of chosen documents). Returns ``status``: ``completed`` /
    ``backend_unavailable`` / ``locked`` / ``completed_with_errors``."""
    from app.services import document_ocr
    totals = {"mode": mode, "batches": 0, "errors": 0, "status": "started",
              **{k: 0 for k in _ACCUM}}
    if document_ids is not None:
        loop = False      # a targeted set is a single pass (ids are always re-selected otherwise)

    # Per-document subprocess isolation: production runs each document in a killable child process (real
    # wall-clock timeout). A caller-supplied in-process extractor stays in-process unless it ALSO provides
    # a factory_ref (used by the isolation tests).
    if isolate is None:
        isolate = _isolation_enabled() if extractor is None else (factory_ref is not None)
    if extractor is None:
        try:
            from app.services.ocr_backend import build_production_extractor
            extractor = build_production_extractor()          # availability check (fast fail if libs missing)
        except OcrBackendUnavailable as exc:
            log.warning("OCR sweep %s aborted: %s", mode, exc)
            totals["status"] = "backend_unavailable"
            totals["error"] = str(exc)
            return totals
        if isolate and factory_ref is None:
            factory_ref = _PRODUCTION_FACTORY
    totals["isolated"] = bool(isolate and factory_ref)

    with _advisory_lock() as acquired:
        if not acquired:
            log.info("OCR sweep %s skipped: another OCR run holds the lock.", mode)
            totals["status"] = "locked"
            return totals      # do NOT touch the status file — the lock holder owns it

        # The tracker is created only inside the lock, so exactly one runner ever writes the status file.
        from app.jobs import ocr_status
        tracker = None
        if _status_enabled(status):
            tracker = _make_tracker(mode, document_ids=document_ids, max_attempts=3,
                                    status_path=status_path, run_id=run_id, clock=clock)
            tracker.start()
            tracker.mark_running()
            totals["run_id"] = tracker.snapshot()["run_id"]
            totals["status_path"] = tracker.path
        try:
            for _ in range(max_batches):
                s = document_ocr.run_ocr(mode=mode, document_ids=document_ids, extractor=extractor,
                                         batch_size=batch_size, actor_user_id=actor_user_id,
                                         request_id=request_id,
                                         isolate=bool(isolate and factory_ref), factory_ref=factory_ref,
                                         observer=tracker)
                totals["batches"] += 1
                for k in _ACCUM:
                    totals[k] += s[k]
                totals["errors"] += len(s["errors"])
                log.info("OCR %s batch %d: candidates=%d completed=%d failed=%d timed_out=%d "
                         "encrypted=%d skipped=%d", mode, totals["batches"], s["candidates"],
                         s["completed"], s["failed"], s["timed_out"], s["encrypted"], s["skipped"])
                if progress:
                    progress(totals)
                # Stop when a batch found no candidates, or when a single-batch (retry) run is requested,
                # or when a full batch produced only skips (nothing left to actually do). A batch that
                # timed documents out IS progress (each gets a timed_out row, excluded next pass) — it
                # must NOT halt the sweep, or pathological documents would strand every document after them.
                if not loop or s["candidates"] == 0:
                    break
                if (s["completed"] == 0 and s["failed"] == 0 and s["unsupported"] == 0
                        and s["timed_out"] == 0):
                    break
        except BaseException as exc:      # runner-level crash → record FAILED, then propagate
            if tracker is not None:
                tracker.finish(ocr_status.FAILED, error=exc)
            raise
        if tracker is not None:
            tracker.finish(ocr_status.COMPLETED,
                           error=(f"{totals['errors']} document error(s)" if totals["errors"] else None))

    totals["status"] = "completed_with_errors" if totals["errors"] else "completed"
    return totals


def run_initial(*, extractor=None, batch_size=50, actor_user_id=None) -> dict:
    """Initial corpus OCR — sweep every not-yet-attempted document to completion."""
    return run_sweep("initial", extractor=extractor, batch_size=batch_size, loop=True,
                     actor_user_id=actor_user_id, request_id="ocr-initial")


def run_incremental(*, extractor=None, batch_size=50, actor_user_id=None) -> dict:
    """Incremental OCR — pick up newly ingested documents (idempotent; skips completed)."""
    return run_sweep("incremental", extractor=extractor, batch_size=batch_size, loop=True,
                     actor_user_id=actor_user_id, request_id="ocr-incremental")


def run_retry(*, extractor=None, batch_size=50, max_attempts=3, actor_user_id=None,
              isolate=None, factory_ref=None) -> dict:
    """Retry failed documents (attempts < max_attempts) — one batch per invocation."""
    from app.services import document_ocr
    if isolate is None:
        isolate = _isolation_enabled() if extractor is None else (factory_ref is not None)
    if extractor is None:
        try:
            from app.services.ocr_backend import build_production_extractor
            extractor = build_production_extractor()
        except OcrBackendUnavailable as exc:
            return {"mode": "retry", "status": "backend_unavailable", "error": str(exc)}
        if isolate and factory_ref is None:
            factory_ref = _PRODUCTION_FACTORY
    with _advisory_lock() as acquired:
        if not acquired:
            return {"mode": "retry", "status": "locked"}
        s = document_ocr.run_ocr(mode="retry", extractor=extractor, batch_size=batch_size,
                                 max_attempts=max_attempts, actor_user_id=actor_user_id,
                                 request_id="ocr-retry",
                                 isolate=bool(isolate and factory_ref), factory_ref=factory_ref)
    s["status"] = "completed_with_errors" if s["errors"] else "completed"
    return s


def run_reprocess(*, document_ids=None, extractor=None, batch_size=50, actor_user_id=None) -> dict:
    """Force re-OCR. With ``document_ids`` it re-OCRs exactly those (even completed, unchanged); without,
    it sweeps content-changed / not-yet-completed documents firm-wide."""
    return run_sweep("reprocess", document_ids=document_ids, extractor=extractor,
                     batch_size=batch_size, loop=document_ids is None,
                     actor_user_id=actor_user_id, request_id="ocr-reprocess")


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="python -m app.jobs.ocr_runner",
                                description="Run OCR sweeps over canonical documents (production).")
    p.add_argument("--mode", choices=("initial", "incremental", "retry", "reprocess"),
                   default="incremental")
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--document-id", type=int, action="append", dest="document_ids",
                   help="Restrict to specific canonical document id(s); repeatable (reprocess mode).")
    p.add_argument("--status", action="store_true",
                   help="Read-only: print the current OCR run status/heartbeat and exit. Never starts a "
                        "run and never modifies the status file.")
    p.add_argument("--status-file", default=None, help="Status file path for --status (default: env/temp).")
    p.add_argument("--stale-seconds", type=float, default=None,
                   help="Heartbeat age (s) beyond which --status reports a RUNNING run as STALE/frozen.")
    args = p.parse_args(argv)
    if args.status:      # read-only status query — does not touch the run or the file
        from app.jobs import ocr_status
        status_argv = []
        if args.status_file:
            status_argv += ["--file", args.status_file]
        if args.stale_seconds is not None:
            status_argv += ["--stale-seconds", str(args.stale_seconds)]
        return ocr_status.main(status_argv)
    if args.mode == "retry":
        result = run_retry(batch_size=args.batch_size, max_attempts=args.max_attempts)
    elif args.mode == "reprocess":
        result = run_reprocess(document_ids=args.document_ids, batch_size=args.batch_size)
    elif args.mode == "initial":
        result = run_initial(batch_size=args.batch_size)
    else:
        result = run_incremental(batch_size=args.batch_size)
    for k in ("mode", "status", "batches", *_ACCUM):
        if k in result:
            print(f"  {k}: {result[k]}")
    if result.get("error"):
        print(f"  error: {result['error']}")
    return 0 if result.get("status") in ("completed", "locked", "backend_unavailable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
