"""Continuous OCR supervisor: the lifecycle the single worker had, with parallel OCR inside it.

``app.jobs.ocr_parallel`` drains ONE lane and exits. That is not a service. ``worker.py`` is the
service: it loops forever over initial OCR, then retry OCR, then classification catch-up, keeping a
heartbeat the whole time. Replacing worker.py with the parallel runner alone would silently stop
classification and never touch the retry lane.

This module is the missing piece. Per pass it:

1. drains **initial** OCR through ``run_parallel`` with N workers,
2. drains **retry** OCR through ``run_parallel`` with the same claim system,
3. runs **classification catch-up**, single-threaded and in the existing batch size,
4. publishes heartbeat / state / counters,
5. sleeps briefly and repeats, stopping only when every lane is empty and nothing moved.

What is deliberately NOT parallelised
-------------------------------------
Classification. ``run_knowledge_pipeline`` has no claim system of its own and nothing in the codebase
establishes that two concurrent invocations are safe, so it keeps exactly the behaviour and batch
size ``worker.py`` used: sequential, ``CLASSIFY_BATCH`` documents at a time. Parallel OCR feeds it
faster; it drains at the same rate it always did.

Everything that made the single worker correct is preserved by delegation rather than
reimplementation: OCR goes through ``run_ocr`` (existing results, duplicate-hash reuse, unsupported /
encrypted / timeout handling, attempts, audit), claiming goes through ``ocr_claims`` (stale-lease
recovery, no duplicate processing), admission goes through ``ocr_throttle`` (fail-closed health,
memory floor, CPU ceiling), and the database remains the only checkpoint — every pass recomputes the
outstanding set, so a kill at any instant resumes correctly.

Single instance, and exclusion of the in-app sweep
-------------------------------------------------
The supervisor holds TWO session-level advisory locks for its whole life, on one session it owns:

``SUPERVISOR_LOCK_KEY`` (511005888)
    "A supervisor is running." A second copy exits immediately rather than queueing.

``SWEEP_LOCK_KEY`` (511005002)
    ``ocr_runner.run_sweep``'s per-batch lock, held here for the supervisor's LIFETIME.

The second one is not decoration, and the reason is worth stating because the code used to argue the
opposite. ``ocr_parallel`` deliberately does not take 511005002, on the grounds that "per-document
claiming is a strictly stronger guarantee". That is true only among claim PARTICIPANTS. The
application's own ``ocr-incremental-sweep`` job is not one: it runs ``ocr_runner.run_sweep`` ->
``document_ocr.run_ocr`` and never reads, writes or consults ``ocr_document_claims`` at all. Its
candidate predicate — ``document_ocr.document_id IS NULL OR status IN ('pending','processing')`` — is
the same population this supervisor's initial lane sweeps.

Against a non-participant, per-document claiming is strictly WEAKER than the coarse lock, because a
claim only excludes someone who looks at claims. The legacy ``worker.py`` was accidentally safe: it
performs its OCR THROUGH ``run_sweep``, so it took 511005002 per chunk and the two serialised. A
parallel runner that calls ``run_ocr`` directly takes that lock never — so without this, every 30
minutes the scheduled sweep would OCR documents the workers were mid-claim on, double-bumping
``attempts`` and spawning extraction subprocesses outside ``ocr_throttle``'s admission control. No
duplicate-claim check can see it, because the sweep takes no claim to duplicate.

Holding 511005002 here restores the exclusion the legacy worker had, with no external process, no
configuration change and no change to the scheduler.

Lock order
----------
IDENTITY BEFORE RESOURCE: 511005888 first, then 511005002, and released in reverse.

Deadlock is already impossible — both are ``pg_try_advisory_lock``, which never waits — but the order
is fixed and documented so it STAYS impossible if either ever becomes a blocking acquisition. The
order is also the one that keeps diagnostics honest: taking 511005002 first would make a second
supervisor fail on the sweep lock and report a sweep conflict, when the truth is that a supervisor is
already running. It additionally minimises the window in which a process that is not going to be the
supervisor holds the lock the in-app sweep needs.

Both locks live on the same NullPool session, so ``close()`` is a real disconnect and PostgreSQL frees
both if the process dies. A crash still needs no cleanup.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.jobs import ocr_parallel, ocr_throttle

log = logging.getLogger(__name__)

#: Distinct from worker.py's 511005777 and from the per-batch 511005002: this one says "a supervisor
#: is running", and is what a second supervisor collides with.
SUPERVISOR_LOCK_KEY = 511_005_888

#: ``ocr_runner._OCR_LOCK_KEY``. The application's ocr-incremental-sweep job takes this around every
#: batch and does NOT participate in ocr_document_claims, so it is the only thing that excludes it.
#: Held for the supervisor's lifetime rather than per batch — see the module docstring.
SWEEP_LOCK_KEY = 511_005_002

#: Acquisition order: identity before resource. Released in reverse. See the module docstring.
LIFETIME_LOCK_ORDER = (SUPERVISOR_LOCK_KEY, SWEEP_LOCK_KEY)

#: Classification batch size, identical to worker.py's CLASSIFY_BATCH. Not a new policy.
CLASSIFY_BATCH = 200

DEFAULT_OPS_DIR = r"C:\Client360Data\ocr-parallel"
DEFAULT_IDLE_SLEEP = 60.0
DEFAULT_THROTTLE_SLEEP = 30.0
DEFAULT_BATCH = 10

#: worker.py's ACTIVE predicate, unchanged.
ACTIVE = ("d.status = 'active' AND d.deleted_at IS NULL "
          "AND d.archived = false AND d.archived_at IS NULL")

SQL_NEVER_ATTEMPTED = f"""
SELECT d.id FROM documents d LEFT JOIN document_ocr o ON o.document_id = d.id
WHERE {ACTIVE} AND (o.document_id IS NULL OR o.status IN ('pending', 'processing'))
ORDER BY d.id
"""

SQL_RETRYABLE = f"""
SELECT d.id FROM documents d JOIN document_ocr o ON o.document_id = d.id
WHERE {ACTIVE} AND o.status IN ('failed', 'timed_out') AND o.attempts < :max_attempts
ORDER BY d.id
"""

SQL_UNCLASSIFIED = f"""
SELECT d.id FROM documents d
JOIN document_ocr o ON o.document_id = d.id
LEFT JOIN document_classifications k ON k.document_id = d.id
WHERE {ACTIVE} AND o.status = 'completed' AND k.document_id IS NULL
ORDER BY d.id
"""

SQL_TOTALS = f"""
SELECT
  count(DISTINCT d.id)                                                      AS active,
  count(DISTINCT d.id) FILTER (WHERE o.status = 'completed')                AS ocr_complete,
  count(DISTINCT d.id) FILTER (WHERE o.status IS DISTINCT FROM 'completed') AS ocr_backlog,
  count(DISTINCT d.id) FILTER (WHERE o.status = 'completed'
                               AND k.document_id IS NOT NULL)               AS classified
FROM documents d
LEFT JOIN document_ocr o ON o.document_id = d.id
LEFT JOIN document_classifications k ON k.document_id = d.id
WHERE {ACTIVE}
"""


# --- read-only helpers --------------------------------------------------------------------------

def _read_ids(engine, sql, **params):
    with engine.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return [r[0] for r in c.execute(text(sql), params)]


def totals(engine) -> dict:
    with engine.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return dict(c.execute(text(SQL_TOTALS)).mappings().one())


# --- single instance ----------------------------------------------------------------------------

def _lock_url(engine) -> str:
    """The engine's URL WITH its password.

    ``str(url)`` masks the password as ``***`` — safe for logs, useless for connecting. Anything
    building a second engine from an existing one has to render it explicitly, or it fails
    authentication at the worst possible moment.
    """
    return engine.url.render_as_string(hide_password=False)


def try_lock(conn, key) -> bool:
    """Take one session-level advisory lock on ``conn``. False when someone else holds it.

    ``pg_try_advisory_lock`` never waits, which is what makes the two-lock acquisition deadlock-free
    regardless of who else is contending.
    """
    return bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())


def unlock(conn, key) -> None:
    """Release one advisory lock. Never raises: a failed release must not mask the real error, and
    the session dying releases it anyway."""
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    except Exception:  # noqa: BLE001
        log.debug("advisory unlock %s failed; session teardown will release it", key, exc_info=True)


def acquire_lifetime_locks(conn) -> tuple[bool, list, str | None]:
    """Take every lifetime lock in :data:`LIFETIME_LOCK_ORDER`, all-or-nothing.

    Returns ``(ok, held, failed_key_name)``. On failure the locks already taken are released in
    reverse order before returning, so a refusal never leaves the sweep lock stranded on a supervisor
    that is not going to run.
    """
    held = []
    for key in LIFETIME_LOCK_ORDER:
        if try_lock(conn, key):
            held.append(key)
            continue
        for taken in reversed(held):                 # reverse of acquisition order
            unlock(conn, taken)
        return False, [], _LOCK_NAMES[key]
    return True, held, None


def release_lifetime_locks(conn, held) -> None:
    """Release the lifetime locks in REVERSE acquisition order. Safe to call more than once."""
    for key in reversed(list(held)):
        unlock(conn, key)


_LOCK_NAMES = {SUPERVISOR_LOCK_KEY: "supervisor", SWEEP_LOCK_KEY: "sweep"}


def sweep_lock_held(engine) -> bool:
    """True if something already owns the in-app sweep lock (511005002) right now.

    Probes on its own NullPool session, for the same reason :func:`supervisor_running` does: a trial
    acquisition left behind on a pooled connection would make the next real run refuse itself.
    """
    probe = create_engine(_lock_url(engine), poolclass=NullPool)
    try:
        with probe.connect() as conn:
            if try_lock(conn, SWEEP_LOCK_KEY):
                unlock(conn, SWEEP_LOCK_KEY)
                return False
            return True
    finally:
        probe.dispose()


def supervisor_running(engine) -> bool:
    """True if another supervisor holds the lock right now.

    Probes on its own NullPool session so the trial acquisition cannot be left behind on a pooled
    connection and make the next real run believe a supervisor is already running.
    """
    probe = create_engine(_lock_url(engine), poolclass=NullPool)
    try:
        with probe.connect() as conn:
            if try_lock(conn, SUPERVISOR_LOCK_KEY):
                unlock(conn, SUPERVISOR_LOCK_KEY)
                return False
            return True
    finally:
        probe.dispose()


# --- observability ------------------------------------------------------------------------------

class _Publisher:
    """Heartbeat / state / counters, written atomically. Never raises: losing observability must
    never stop the service."""

    def __init__(self, ops_dir):
        self.dir = Path(ops_dir)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def publish(self, name, payload):
        try:
            path = self.dir / name
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, path)          # atomic: a reader never sees a torn file
        except Exception:  # noqa: BLE001
            pass

    def beat(self, phase, extra=None):
        self.publish("supervisor_heartbeat.json",
                     {"pid": os.getpid(), "phase": phase,
                      "heartbeat_utc": datetime.now(UTC).isoformat(), **(extra or {})})


# --- lane diagnostics -------------------------------------------------------------------------------

def _publish_lane_diagnostics(pub, lane, workers, result) -> None:
    """Record WHY each worker of a lane stopped, and its child exit code.

    ``run_parallel`` already returns ``per_worker`` (each worker's ``stopped_because`` and counters)
    and ``child_exitcodes``; until now the supervisor discarded both. When lane ocr1 retired early on
    2026-09-13 the string ``"lane_empty"`` existed in memory and was thrown away, so the cause had to
    be reconstructed from the claims table and the process tree hours later.

    The scheduled task does not redirect stdout, so logging alone is not durable. This also writes
    ``supervisor_lanes.json`` into the ops directory through the existing atomic publisher. Never
    raises: losing observability must not stop OCR.
    """
    try:
        per_worker = list(result.get("per_worker") or [])
        exitcodes = dict(result.get("child_exitcodes") or {})
        for w in per_worker:
            log.info("lane %s worker %s stopped_because=%s claimed=%s completed=%s failed=%s "
                     "empty_retries=%s throttled_waits=%s",
                     lane, w.get("worker_id"), w.get("stopped_because"), w.get("claimed"),
                     w.get("completed"), w.get("failed"), w.get("empty_retries"),
                     w.get("throttled_waits"))
        log.info("lane %s child exit codes: %s", lane, exitcodes)
        early = [w for w in per_worker if w.get("stopped_because") not in (None, "lane_empty")]
        if early:
            log.warning("lane %s: %d worker(s) stopped for a reason other than an empty lane: %s",
                        lane, len(early),
                        [(w.get("worker_id"), w.get("stopped_because")) for w in early])
        # Group by outcome so an operator can tell a start failure from an abnormal exit from a
        # runtime stall at a glance. Before this, every non-reporting worker was "startup_failed".
        outcomes = {}
        for w in per_worker:
            outcomes.setdefault(w.get("stopped_because") or "reported", []).append(w.get("worker_id"))
        if outcomes:
            log.info("lane %s outcomes: %s", lane,
                     {k: len(v) for k, v in outcomes.items()})
        pub.publish("supervisor_lanes.json",
                    {"lane": lane, "workers_requested": workers,
                     "workers_reported": len(per_worker),
                     "status": result.get("status"),
                     "elapsed_seconds": result.get("elapsed_seconds"),
                     "outcomes": {k: len(v) for k, v in outcomes.items()},
                     "outcome_workers": outcomes,
                     "stalled_workers": result.get("stalled_workers"),
                     "child_exitcodes": exitcodes,
                     "child_errors": result.get("child_errors"),
                     "per_worker": per_worker,
                     "published_utc": datetime.now(UTC).isoformat()})
    except Exception:  # noqa: BLE001 — diagnostics must never break a sweep
        log.debug("could not publish lane diagnostics", exc_info=True)


# --- classification -------------------------------------------------------------------------------

def classify(engine, *, limit_batches=None, batch_size=CLASSIFY_BATCH, pipeline=None,
             document_ids=None) -> int:
    """Classification catch-up, with worker.py's exact semantics: sequential, one batch at a time.

    Deliberately NOT parallel — see the module docstring.
    """
    if pipeline is None:
        from app.services.knowledge_pipeline import run_knowledge_pipeline as pipeline
    done = batches = 0
    scope = set(document_ids) if document_ids is not None else None
    while True:
        candidates = _read_ids(engine, SQL_UNCLASSIFIED)
        if scope is not None:
            candidates = [i for i in candidates if i in scope]
        ids = candidates[:batch_size]
        if not ids:
            break
        summary = pipeline(document_ids=ids, mode="incremental", batch_size=len(ids))
        done += (summary or {}).get("classified", 0)
        batches += 1
        log.info("classify batch %d: candidates=%s classified=%s status=%s",
                 batches, (summary or {}).get("candidates"), (summary or {}).get("classified"),
                 (summary or {}).get("status"))
        if limit_batches and batches >= limit_batches:
            break
    return done


# --- the service ------------------------------------------------------------------------------------

def run_supervisor(*, workers=None, ops_dir=None, max_passes=None, idle_sleep=DEFAULT_IDLE_SLEEP,
                   batch=DEFAULT_BATCH, max_attempts=3, stop_when_drained=True,
                   factory_ref=ocr_parallel._PRODUCTION_FACTORY, extractor=None,
                   pipeline=None, allow_beside_legacy=False, health_urls=None,
                   document_ids=None, min_free_mb=None, max_cpu_percent=None) -> dict:
    """Run the initial -> retry -> classify cycle until every lane is empty.

    Returns accumulated counters. ``max_passes`` bounds the loop for tests.

    ``document_ids`` confines every lane to an explicit set. Production leaves it None and sweeps
    the whole corpus; tests pass their own documents so a corpus-wide sweep cannot reach into
    another module's rows, which is otherwise a real source of cross-test interference.
    """
    from app.db import engine

    workers = ocr_throttle.configured_workers() if workers is None else max(
        1, min(ocr_throttle.DEFAULT_MAX_WORKERS, int(workers)))
    pub = _Publisher(ops_dir or os.getenv("OCR_SUPERVISOR_DIR") or DEFAULT_OPS_DIR)

    # The lock is SESSION level, so it must live on a session this function actually owns. A pooled
    # connection is the wrong home: close() returns it to the pool rather than disconnecting, so the
    # lock's release depends entirely on the explicit unlock running — and a pooled connection handed
    # back with the lock still held makes every later run report "already_running". A NullPool engine
    # means close() really disconnects, and PostgreSQL then frees the lock even if the process dies.
    lock_engine = create_engine(_lock_url(engine), poolclass=NullPool)
    conn = lock_engine.connect()
    # BOTH lifetime locks are taken here, before a single worker process is spawned. A supervisor that
    # cannot hold them starts nothing at all rather than running degraded beside whoever does.
    ok, held, blocked_by = acquire_lifetime_locks(conn)
    if not ok:
        conn.close()
        lock_engine.dispose()
        if blocked_by == "supervisor":
            log.info("another supervisor already holds lock %s; exiting without doing anything",
                     SUPERVISOR_LOCK_KEY)
            return {"status": "already_running", "workers": workers, "passes": 0}
        # The sweep lock is held by the in-app ocr-incremental-sweep or the legacy worker's chunk.
        # Starting anyway is exactly the concurrent-OCR defect this lock exists to prevent.
        log.error("the in-app OCR sweep (or the legacy worker) holds lock %s; refusing to start "
                  "workers beside it", SWEEP_LOCK_KEY)
        return {"status": "sweep_lock_unavailable", "workers": workers, "passes": 0,
                "error": (f"lock {SWEEP_LOCK_KEY} is held by another OCR run "
                          f"(ocr-incremental-sweep or the legacy worker); refusing to start")}

    run = {"status": "completed", "workers": workers, "passes": 0, "initial_completed": 0,
           "retry_completed": 0, "classified": 0, "failed": 0, "timed_out": 0, "skipped": 0,
           "unsupported": 0, "encrypted": 0, "throttled_waits": 0,
           "started_utc": datetime.now(UTC).isoformat()}
    try:
        start = totals(engine)
        log.info("SUPERVISOR START pid=%s workers=%s | active=%s complete=%s backlog=%s classified=%s",
                 os.getpid(), workers, start["active"], start["ocr_complete"],
                 start["ocr_backlog"], start["classified"])
        # Named explicitly so an operator reading the log can see the in-app ocr-incremental-sweep is
        # excluded for as long as this process lives, rather than having to infer it.
        log.info("holding lifetime locks %s (single supervisor) and %s (in-app OCR sweep excluded)",
                 SUPERVISOR_LOCK_KEY, SWEEP_LOCK_KEY)
        pub.beat("starting", {"workers": workers, "start_totals": start,
                              "lifetime_locks": list(held),
                              "sweep_lock_held": SWEEP_LOCK_KEY in held})

        while max_passes is None or run["passes"] < max_passes:
            # Admission is checked BEFORE any lane claims work. A hold never interrupts a document
            # already in flight; it only stops the next claim.
            admission = ocr_throttle.may_claim(health_urls=health_urls,
                                               min_free_mb=min_free_mb,
                                               max_cpu_percent=max_cpu_percent)
            if not admission:
                run["throttled_waits"] += 1
                log.info("holding: %s", admission.reason)
                pub.beat("paused", {"reason": admission.reason, "free_mb": admission.free_mb})
                if max_passes is not None:          # bounded runs must not spin in tests
                    run["status"] = "paused"
                    break
                time.sleep(DEFAULT_THROTTLE_SLEEP)
                continue

            scope = set(document_ids) if document_ids is not None else None
            never = _read_ids(engine, SQL_NEVER_ATTEMPTED)
            retry = _read_ids(engine, SQL_RETRYABLE, max_attempts=max_attempts)
            pending = _read_ids(engine, SQL_UNCLASSIFIED)
            if scope is not None:
                never = [i for i in never if i in scope]
                retry = [i for i in retry if i in scope]
                pending = [i for i in pending if i in scope]
            log.info("PASS %d: never_attempted=%d retryable=%d unclassified=%d",
                     run["passes"] + 1, len(never), len(retry), len(pending))
            pub.beat("pass_start", {"never_attempted": len(never), "retryable": len(retry),
                                    "unclassified": len(pending), "pass": run["passes"] + 1})

            if not never and not retry and not pending:
                log.info("every lane is empty")
                run["status"] = "drained"
                if stop_when_drained:
                    break
                time.sleep(idle_sleep)
                run["passes"] += 1
                continue

            for lane, ids, key in (("initial", never, "initial_completed"),
                                   ("retry", retry, "retry_completed")):
                if not ids:
                    continue
                pub.beat(f"ocr:{lane}", {"documents": len(ids), "workers": workers})
                result = ocr_parallel.run_parallel(
                    workers=workers, mode=lane, batch=batch, document_ids=ids,
                    factory_ref=factory_ref, extractor=extractor,
                    max_attempts=max_attempts, allow_beside_legacy=allow_beside_legacy,
                    min_free_mb=min_free_mb, max_cpu_percent=max_cpu_percent,
                    health_urls=health_urls,
                    # A corpus lane is swept by several workers at once, so an empty claim batch is
                    # far more often lost contention than a drained lane. Without this the first
                    # zero-win batch retires a worker permanently and the lane finishes short-handed.
                    empty_batch_retries=ocr_parallel.LANE_EMPTY_RETRIES)
                _publish_lane_diagnostics(pub, lane, workers, result)
                if result.get("status") == "refused":
                    log.error("STOPPING: %s", result.get("error"))
                    run["status"] = "refused"
                    run["error"] = result.get("error")
                    pub.beat("refused", {"error": result.get("error")})
                    return run
                run[key] += result.get("completed", 0)
                for k in ("failed", "timed_out", "skipped", "unsupported", "encrypted"):
                    run[k] += result.get(k, 0)
                log.info("%s lane: completed=%s failed=%s", lane, result.get("completed"),
                         result.get("failed"))

            pub.beat("classify")
            run["classified"] += classify(engine, pipeline=pipeline, document_ids=document_ids)

            after = totals(engine)
            run["passes"] += 1
            pub.publish("supervisor_state.json",
                        {"db_totals": after, "run_totals": run,
                         "updated_utc": datetime.now(UTC).isoformat()})

            # Nothing left to try and nothing moved: the remainder is blocked (attempts exhausted,
            # unsupported type, unreachable source). Stop rather than spin.
            if after == start and not never and not retry:
                log.info("no further progress possible; remaining work is blocked")
                run["status"] = "blocked"
                break
            start = after

        pub.beat("finished", {"run_totals": run})
        return run
    except Exception as exc:  # noqa: BLE001
        log.exception("supervisor crashed")
        run["status"] = "crashed"
        run["error"] = f"{type(exc).__name__}: {exc}"
        pub.beat("crashed", {"error": run["error"]})
        return run
    finally:
        # Every path — drained, blocked, refused, crashed — releases both, in reverse acquisition
        # order. release_lifetime_locks never raises, so a failed unlock cannot mask the real error.
        release_lifetime_locks(conn, held)
        try:
            conn.close()
            lock_engine.dispose()        # a real disconnect: neither lock can outlive this call
        except Exception:  # noqa: BLE001
            pass


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.jobs.ocr_supervisor",
                                description="Continuous OCR service: initial -> retry -> classify.")
    p.add_argument("--workers", type=int, default=None,
                   help=f"parallel OCR workers (capped at {ocr_throttle.DEFAULT_MAX_WORKERS})")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    p.add_argument("--ops-dir", default=None)
    p.add_argument("--max-passes", type=int, default=None)
    p.add_argument("--keep-running", action="store_true",
                   help="stay alive when every lane is empty instead of exiting")
    p.add_argument("--allow-beside-legacy", action="store_true",
                   help="DANGEROUS: run even while the single-worker runner holds its lock.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)sZ %(levelname)s %(message)s")
    result = run_supervisor(workers=args.workers, batch=args.batch, ops_dir=args.ops_dir,
                            max_passes=args.max_passes,
                            stop_when_drained=not args.keep_running,
                            allow_beside_legacy=args.allow_beside_legacy)
    for k, v in result.items():
        print(f"  {k:20} {v}")
    return 0 if result.get("status") in ("completed", "drained", "blocked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
