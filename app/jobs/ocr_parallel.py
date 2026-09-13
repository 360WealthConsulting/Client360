"""Parallel OCR corpus runner: N workers, one claim per document, no global lock.

Relationship to the existing single worker
------------------------------------------
This does not replace ``app/services/document_ocr.run_ocr``; it schedules it. Every worker claims a
batch of document ids and hands exactly those ids to ``run_ocr``, which is the same call the
single-threaded runner makes. Everything that makes OCR correct therefore stays where it already
lives, unchanged:

* existing OCR results — ``run_ocr`` skips a completed, content-unchanged document,
* the database-authoritative checkpoint — candidates are recomputed from the database every claim,
* unsupported / encrypted / timeout handling — untouched inside ``_ocr_one``,
* the retry lane — same mode, same predicate (NOTE: this module drains ONE lane and exits; the
  continuous initial -> retry -> classification service is app.jobs.ocr_supervisor),
* duplicate-hash reuse — the ``reused``/``skipped`` path is ``run_ocr``'s,
* audit behaviour — one audit row per ``run_ocr`` call, the same granularity as a 50-document chunk
  today,
* BELOW_NORMAL priority — set per worker process at startup.

What is genuinely new is only: who may touch a document (a claim, not a global lock), and when a
worker may take more work (admission control).

``run_sweep``'s per-batch advisory lock is deliberately NOT used here. That lock exists to stop two
uncoordinated sweeps colliding; per-document claiming is a strictly stronger guarantee, and keeping
the coarse lock would serialise the workers and defeat the whole exercise.

Safety
------
This module never runs unless it is given an explicit worker count and mode, and it will not start
while the legacy single worker's lifetime lock (511005777) is held, so it cannot race the production
worker by accident.
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import threading
import time
from datetime import UTC, datetime

from sqlalchemy import text

from app.jobs import ocr_claims, ocr_throttle

log = logging.getLogger(__name__)

#: The legacy single-worker lifetime lock. If it is held, the old worker is sweeping and we refuse.
LEGACY_WORKER_LOCK_KEY = 511_005_777

_PRODUCTION_FACTORY = "app.services.ocr_backend.build_production_extractor"

DEFAULT_BATCH = 10
DEFAULT_IDLE_SLEEP = 5.0
DEFAULT_THROTTLE_SLEEP = 30.0


def legacy_worker_running(engine) -> bool:
    """True if the single-worker runner holds its lifetime lock right now."""
    with engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                           {"k": LEGACY_WORKER_LOCK_KEY}).scalar()
        if got:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LEGACY_WORKER_LOCK_KEY})
            return False
        return True


class _LeaseKeeper:
    """Refreshes the leases on the documents a worker is holding, while it holds them.

    Without this a batch that legitimately takes longer than the lease would have its documents
    stolen mid-OCR. With it, only a worker that has actually stopped making progress loses its
    claims, which is exactly the distinction the lease is meant to draw.
    """

    def __init__(self, engine, worker_id, lease_seconds, interval):
        self._engine, self._worker_id = engine, worker_id
        self._lease, self._interval = lease_seconds, interval
        self._ids: list[int] = []
        self._stop = threading.Event()
        self._thread = None

    def start(self, document_ids):
        self._ids = list(document_ids)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self._interval):
            try:
                with self._engine.begin() as conn:
                    ocr_claims.heartbeat(conn, worker_id=self._worker_id,
                                         document_ids=self._ids, lease_seconds=self._lease)
            except Exception:  # noqa: BLE001 — a missed heartbeat must never kill the worker
                pass

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def worker_loop(*, worker_id=None, mode="initial", batch=DEFAULT_BATCH,
                lease_seconds=ocr_claims.DEFAULT_LEASE_SECONDS,
                heartbeat_seconds=ocr_claims.DEFAULT_HEARTBEAT_SECONDS,
                max_attempts=3, max_batches=None, extractor=None, factory_ref=_PRODUCTION_FACTORY,
                stop_when_empty=True, on_batch=None, document_ids=None) -> dict:
    """One worker: claim, OCR, complete, repeat until the lane is empty.

    Returns accumulated counts. Tests inject ``extractor`` with ``factory_ref=None`` to stay
    in-process; production uses the isolated subprocess path.

    ``document_ids`` confines the worker to an explicit set — a targeted run over a manifest rather
    than a corpus sweep. Omitted, the worker claims whatever is next across the whole lane.
    """
    from app.db import engine
    from app.services import document_ocr

    ocr_throttle.lower_priority()
    worker_id = worker_id or ocr_claims.new_worker_id()
    totals = {"worker_id": worker_id, "batches": 0, "claimed": 0, "completed": 0, "failed": 0,
              "timed_out": 0, "skipped": 0, "unsupported": 0, "encrypted": 0, "lost_claims": 0,
              "throttled_waits": 0, "chars_extracted": 0}
    keeper = _LeaseKeeper(engine, worker_id, lease_seconds, heartbeat_seconds)

    while max_batches is None or totals["batches"] < max_batches:
        admission = ocr_throttle.may_claim()
        if not admission:
            totals["throttled_waits"] += 1
            log.info("worker %s holding: %s", worker_id, admission.reason)
            time.sleep(DEFAULT_THROTTLE_SLEEP)
            continue

        with engine.begin() as conn:
            claims = ocr_claims.claim_batch(conn, worker_id=worker_id, mode=mode, limit=batch,
                                            lease_seconds=lease_seconds,
                                            max_attempts=max_attempts,
                                            document_ids=document_ids)
        if not claims:
            if stop_when_empty:
                break
            time.sleep(DEFAULT_IDLE_SLEEP)
            continue

        totals["claimed"] += len(claims)
        ids = [c.document_id for c in claims]
        keeper.start(ids)
        try:
            # The one call that does the actual work. Same entry point, same semantics, same audit
            # granularity as the single-worker chunk.
            #
            # `isolate` is OMITTED when there is a factory_ref, never passed as None. run_ocr's
            # isolation decision is fail-closed on a sentinel: omitted + factory_ref means isolated,
            # while an explicit None is falsy and resolves to the IN-PROCESS path, which then falls
            # back to default_extractor and fails every document with "No OCR engine configured".
            isolation = {} if factory_ref else {"isolate": False}
            summary = document_ocr.run_ocr(document_ids=ids, mode=mode, extractor=extractor,
                                           factory_ref=factory_ref,
                                           batch_size=len(ids), max_attempts=max_attempts,
                                           **isolation)
        finally:
            keeper.stop()

        for key in ("completed", "failed", "timed_out", "skipped", "unsupported", "encrypted",
                    "chars_extracted"):
            totals[key] += summary.get(key, 0)

        with engine.begin() as conn:
            for claim in claims:
                # A worker that lost its lease mid-batch must not stamp its result over the new
                # holder's. It drops the claim instead; the document is simply processed again.
                if ocr_claims.claim_is_current(conn, worker_id=worker_id, claim=claim):
                    ocr_claims.complete(conn, worker_id=worker_id, claim=claim,
                                        outcome=summary.get("status"))
                else:
                    totals["lost_claims"] += 1

        totals["batches"] += 1
        if on_batch is not None:
            on_batch(dict(totals))

    with engine.begin() as conn:
        ocr_claims.release(conn, worker_id=worker_id, document_ids=[])
    return totals


def _worker_entry(kwargs, out_q):
    try:
        out_q.put(worker_loop(**kwargs))
    except Exception as exc:  # noqa: BLE001 — a dead worker must not hang the pool
        out_q.put({"worker_id": kwargs.get("worker_id"), "error": f"{type(exc).__name__}: {exc}"})


def run_parallel(*, workers=None, mode="initial", batch=DEFAULT_BATCH, max_batches=None,
                 allow_beside_legacy=False, **kw) -> dict:
    """Start ``workers`` processes and wait for them. Refuses to run beside the legacy worker."""
    from app.db import engine

    workers = int(workers or ocr_throttle.configured_workers())
    if workers > ocr_throttle.DEFAULT_MAX_WORKERS:
        log.warning("worker count %d exceeds the %d physical cores; oversubscribing",
                    workers, ocr_throttle.DEFAULT_MAX_WORKERS)
    if not allow_beside_legacy and legacy_worker_running(engine):
        return {"status": "refused",
                "error": "the single-worker runner holds lock 511005777; refusing to run beside it"}

    started = datetime.now(UTC)
    ctx = mp.get_context("spawn")
    out_q = ctx.Queue()
    procs = []
    for i in range(workers):
        kwargs = dict(kw, mode=mode, batch=batch, max_batches=max_batches,
                      worker_id=ocr_claims.new_worker_id(f"ocr{i}"))
        p = ctx.Process(target=_worker_entry, args=(kwargs, out_q), daemon=False)
        p.start()
        procs.append(p)

    results = [out_q.get() for _ in procs]
    for p in procs:
        p.join()

    elapsed = (datetime.now(UTC) - started).total_seconds()
    agg = {"status": "completed", "workers": workers, "mode": mode,
           "elapsed_seconds": round(elapsed, 2), "per_worker": results}
    for key in ("claimed", "completed", "failed", "timed_out", "skipped", "unsupported",
                "encrypted", "lost_claims", "chars_extracted", "batches"):
        agg[key] = sum(r.get(key, 0) for r in results if isinstance(r, dict))
    agg["documents_per_minute"] = (round(agg["completed"] / (elapsed / 60.0), 2)
                                   if elapsed > 0 else 0.0)
    return agg


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.jobs.ocr_parallel",
                                description="Parallel OCR corpus runner (per-document claiming).")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--mode", default="initial", choices=("initial", "retry"))
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    p.add_argument("--max-batches", type=int, default=None)
    p.add_argument("--allow-beside-legacy", action="store_true",
                   help="DANGEROUS: run even while the single-worker runner holds its lock.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s")
    result = run_parallel(workers=args.workers, mode=args.mode, batch=args.batch,
                          max_batches=args.max_batches,
                          allow_beside_legacy=args.allow_beside_legacy)
    for k, v in result.items():
        if k != "per_worker":
            print(f"  {k:24} {v}")
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
