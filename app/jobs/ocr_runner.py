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
_ACCUM = ("candidates", "completed", "failed", "timed_out", "skipped", "unsupported", "chars_extracted")

# The production extraction backend as a picklable dotted reference, so the per-document child process
# builds it itself (spawn-safe; the child never imports app.db).
_PRODUCTION_FACTORY = "app.services.ocr_backend.build_production_extractor"


def _isolation_enabled() -> bool:
    """Per-document subprocess isolation (real wall-clock timeout). On by default in production; a
    pathological document can never freeze the migration. Disable only for diagnostics."""
    return os.getenv("OCR_SUBPROCESS_ISOLATION", "1").strip().lower() in {"1", "true", "yes", "on"}


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
              progress=None, isolate=None, factory_ref=None) -> dict:
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
            return totals

        for _ in range(max_batches):
            s = document_ocr.run_ocr(mode=mode, document_ids=document_ids, extractor=extractor,
                                     batch_size=batch_size, actor_user_id=actor_user_id,
                                     request_id=request_id,
                                     isolate=bool(isolate and factory_ref), factory_ref=factory_ref)
            totals["batches"] += 1
            for k in _ACCUM:
                totals[k] += s[k]
            totals["errors"] += len(s["errors"])
            log.info("OCR %s batch %d: candidates=%d completed=%d failed=%d timed_out=%d skipped=%d",
                     mode, totals["batches"], s["candidates"], s["completed"], s["failed"],
                     s["timed_out"], s["skipped"])
            if progress:
                progress(totals)
            # Stop when a batch found no candidates, or when a single-batch (retry) run is requested,
            # or when a full batch produced only skips (nothing left to actually do). A batch that timed
            # documents out IS progress (each gets a timed_out row, excluded next pass) — it must NOT halt
            # the sweep, or a run of pathological documents would strand every document after them.
            if not loop or s["candidates"] == 0:
                break
            if (s["completed"] == 0 and s["failed"] == 0 and s["unsupported"] == 0
                    and s["timed_out"] == 0):
                break

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
    args = p.parse_args(argv)
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
