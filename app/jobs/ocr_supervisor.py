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

Single instance
---------------
A session-level advisory lock held for the supervisor's whole life, on its own key. A second copy
exits immediately rather than queueing, and the lock is released by PostgreSQL when the process
dies, so a crash needs no cleanup.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.jobs import ocr_parallel, ocr_throttle

log = logging.getLogger(__name__)

#: Distinct from worker.py's 511005777 and from the per-batch 511005002: this one says "a supervisor
#: is running", and is what a second supervisor collides with.
SUPERVISOR_LOCK_KEY = 511_005_888

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

def supervisor_running(engine) -> bool:
    """True if another supervisor holds the lock right now."""
    with engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                           {"k": SUPERVISOR_LOCK_KEY}).scalar()
        if got:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SUPERVISOR_LOCK_KEY})
            return False
        return True


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


# --- classification -------------------------------------------------------------------------------

def classify(engine, *, limit_batches=None, batch_size=CLASSIFY_BATCH, pipeline=None) -> int:
    """Classification catch-up, with worker.py's exact semantics: sequential, one batch at a time.

    Deliberately NOT parallel — see the module docstring.
    """
    if pipeline is None:
        from app.services.knowledge_pipeline import run_knowledge_pipeline as pipeline
    done = batches = 0
    while True:
        ids = _read_ids(engine, SQL_UNCLASSIFIED)[:batch_size]
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
                   pipeline=None, allow_beside_legacy=False, health_urls=None) -> dict:
    """Run the initial -> retry -> classify cycle until every lane is empty.

    Returns accumulated counters. ``max_passes`` bounds the loop for tests.
    """
    from app.db import engine

    workers = ocr_throttle.configured_workers() if workers is None else max(
        1, min(ocr_throttle.DEFAULT_MAX_WORKERS, int(workers)))
    pub = _Publisher(ops_dir or os.getenv("OCR_SUPERVISOR_DIR") or DEFAULT_OPS_DIR)

    conn = engine.connect()
    got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                       {"k": SUPERVISOR_LOCK_KEY}).scalar()
    if not got:
        conn.close()
        log.info("another supervisor already holds the lock; exiting without doing anything")
        return {"status": "already_running", "workers": workers, "passes": 0}

    run = {"status": "completed", "workers": workers, "passes": 0, "initial_completed": 0,
           "retry_completed": 0, "classified": 0, "failed": 0, "timed_out": 0, "skipped": 0,
           "unsupported": 0, "encrypted": 0, "throttled_waits": 0,
           "started_utc": datetime.now(UTC).isoformat()}
    try:
        start = totals(engine)
        log.info("SUPERVISOR START pid=%s workers=%s | active=%s complete=%s backlog=%s classified=%s",
                 os.getpid(), workers, start["active"], start["ocr_complete"],
                 start["ocr_backlog"], start["classified"])
        pub.beat("starting", {"workers": workers, "start_totals": start})

        while max_passes is None or run["passes"] < max_passes:
            # Admission is checked BEFORE any lane claims work. A hold never interrupts a document
            # already in flight; it only stops the next claim.
            admission = ocr_throttle.may_claim(health_urls=health_urls)
            if not admission:
                run["throttled_waits"] += 1
                log.info("holding: %s", admission.reason)
                pub.beat("paused", {"reason": admission.reason, "free_mb": admission.free_mb})
                if max_passes is not None:          # bounded runs must not spin in tests
                    run["status"] = "paused"
                    break
                time.sleep(DEFAULT_THROTTLE_SLEEP)
                continue

            never = _read_ids(engine, SQL_NEVER_ATTEMPTED)
            retry = _read_ids(engine, SQL_RETRYABLE, max_attempts=max_attempts)
            pending = _read_ids(engine, SQL_UNCLASSIFIED)
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
                    max_attempts=max_attempts, allow_beside_legacy=allow_beside_legacy)
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
            run["classified"] += classify(engine, pipeline=pipeline)

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
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SUPERVISOR_LOCK_KEY})
            conn.close()
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
