"""The worker: claim a document, walk it through the stages, and never hold more than it needs.

ONE TRANSACTION PER STAGE
-------------------------
Each stage runs in its own short transaction that also persists the stage transition. That pairing is
what makes a restart RESUME: the moment ``extract`` commits, the task row says the document is past
extraction, so a process killed during ``classify`` comes back and re-runs ``classify`` only. A single
transaction spanning the whole document would be simpler and would throw away every stage's progress
on any failure.

The OCR stage is the exception, and deliberately so: it is split into plan / execute / settle
(:mod:`.stages`) so the minutes spent inside the engine are spent holding NO database connection. An
idle-in-transaction connection across a 400-page scan blocks vacuum and takes pool capacity away from
the staff-facing application.

WHAT A WORKER GUARANTEES
------------------------
* It touches only documents it holds a live lease on. Every transition is conditioned on
  ``lease_owner = <me>``, so a worker whose lease was reclaimed while it was busy writes nothing.
* It never dies of one bad document. Every failure is classified, recorded against the task, and the
  loop continues.
* It stops when asked. ``stop_event`` is checked between documents and between stages, and shutdown
  releases the worker's claims so a planned restart does not wait out the leases.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime

from app.db import engine as default_engine
from app.services.document_pipeline_continuous import backpressure, queue, stages
from app.services.document_pipeline_continuous.model import (
    OUTCOME_REVIEW,
    STAGE_DONE,
    STAGE_OCR,
    STATE_REVIEW,
    STATE_SUCCEEDED,
    PipelineNotInstalled,
)

log = logging.getLogger(__name__)

#: How long an idle worker waits before asking for work again. Short enough to feel continuous, long
#: enough that an empty queue is not a spin loop against the database.
DEFAULT_IDLE_SLEEP_SECONDS = 5.0
#: How long a worker waits when backpressure says "not now".
DEFAULT_PRESSURE_SLEEP_SECONDS = 15.0


class Worker:
    """One pipeline worker. Safe to run several per process (threads) or one per process."""

    def __init__(self, *, worker_id: str | None = None, engine_=None, lease_seconds: int | None = None,
                 batch_size: int = 1, max_attempts: int = 5, idle_sleep: float = DEFAULT_IDLE_SLEEP_SECONDS,
                 pressure_sleep: float = DEFAULT_PRESSURE_SLEEP_SECONDS, stage_kwargs: dict | None = None,
                 pressure_limits: dict | None = None, actor_user_id=None,
                 request_id: str = "document-pipeline"):
        self.engine = engine_ or default_engine
        self.worker_id = worker_id or queue.new_worker_id(pid=os.getpid())
        self.lease_seconds = lease_seconds or queue.DEFAULT_LEASE_SECONDS
        self.batch_size = max(1, int(batch_size))
        self.max_attempts = max(1, int(max_attempts))
        self.idle_sleep = float(idle_sleep)
        self.pressure_sleep = float(pressure_sleep)
        self.stage_kwargs = dict(stage_kwargs or {})
        self.pressure_limits = dict(pressure_limits or {})
        self.actor_user_id = actor_user_id
        self.request_id = request_id
        self.counters = {"claimed": 0, "completed": 0, "review": 0, "retried": 0, "blocked": 0,
                         "reclaimed": 0}

    # --- lifecycle ---------------------------------------------------------------------------

    def register(self) -> None:
        with self.engine.begin() as conn:
            queue.register_worker(conn, worker_id=self.worker_id, pid=os.getpid(), state="running")

    def shutdown(self) -> None:
        """Clean stop: hand back everything this worker holds, then mark it stopped.

        Releasing first matters. A restart that only marks itself stopped leaves its documents
        invisible until their leases lapse, which turns a five-second service restart into a
        ten-minute hole in throughput."""
        try:
            with self.engine.begin() as conn:
                released = queue.release_worker_tasks(conn, worker_id=self.worker_id)
                queue.stop_worker(conn, worker_id=self.worker_id)
            if released:
                log.info("worker %s released %s task(s) on shutdown", self.worker_id, released)
        except Exception:      # noqa: BLE001 — a shutdown must not raise out of a service stop
            log.exception("worker %s failed to release its tasks on shutdown", self.worker_id)

    def _heartbeat(self, *, current_document_id=None, state="running", **deltas) -> None:
        try:
            with self.engine.begin() as conn:
                queue.heartbeat_worker(conn, worker_id=self.worker_id, state=state,
                                       current_document_id=current_document_id, **deltas)
        except Exception:      # noqa: BLE001 — a missed heartbeat is a monitoring gap, not a failure
            log.debug("worker %s heartbeat failed", self.worker_id, exc_info=True)

    # --- one pass ----------------------------------------------------------------------------

    def run_once(self) -> dict:
        """Reclaim lapsed leases, claim a batch, process it. Returns what happened this pass."""
        result = {"claimed": 0, "processed": 0, "deferred": None, "reclaimed": 0}

        with self.engine.begin() as conn:
            result["reclaimed"] = queue.reclaim_expired_leases(conn)
        self.counters["reclaimed"] += result["reclaimed"]

        pressure = backpressure.assess(engine_=self.engine, **self.pressure_limits)
        if not pressure["ok"]:
            result["deferred"] = pressure["reason"]
            self._heartbeat(state="running")
            return result

        with self.engine.begin() as conn:
            tasks = queue.claim(conn, worker_id=self.worker_id, limit=self.batch_size,
                                lease_seconds=self.lease_seconds)
        result["claimed"] = len(tasks)
        self.counters["claimed"] += len(tasks)
        if tasks:
            self._heartbeat(claimed=len(tasks))

        for task in tasks:
            self.process(task)
            result["processed"] += 1
        return result

    # --- one document ------------------------------------------------------------------------

    def process(self, task: dict) -> dict:
        """Walk one leased task through its remaining stages. Never raises."""
        task = dict(task)
        task["stage"] = stages.first_stage_of(task)
        document_id = int(task["document_id"])
        self._heartbeat(current_document_id=document_id)

        while task["stage"] != STAGE_DONE:
            try:
                result = self._run_one_stage(task)
            except BaseException as exc:      # noqa: BLE001 — classified and recorded below
                self._record_failure(task, exc)
                self._heartbeat(current_document_id=None, failed=1)
                return {"document_id": document_id, "stopped_at": task["stage"], "failed": True}
            if result is None:                # lease lost — another worker owns this document now
                log.info("worker %s lost its lease on document %s mid-stage",
                         self.worker_id, document_id)
                self._heartbeat(current_document_id=None)
                return {"document_id": document_id, "stopped_at": task["stage"], "lease_lost": True}
            if result.next_stage == STAGE_DONE:
                self.counters["completed"] += 1
                if result.outcome == OUTCOME_REVIEW:
                    self.counters["review"] += 1
                self._heartbeat(current_document_id=None, completed=1)
                return {"document_id": document_id, "outcome": result.outcome, "completed": True}
            task["stage"] = result.next_stage
        return {"document_id": document_id, "completed": True}

    def _run_one_stage(self, task) -> stages.StageResult | None:
        """Execute the task's current stage and persist the transition atomically with it.

        Returns the stage result, or None when the lease was lost (the transition's
        ``lease_owner = <me>`` guard did not match, so nothing was written)."""
        if task["stage"] == STAGE_OCR:
            return self._run_ocr_stage(task)
        with self.engine.begin() as conn:
            result = stages.run_stage(conn, task, actor_user_id=self.actor_user_id,
                                      request_id=self.request_id, **self.stage_kwargs)
            return result if self._persist(conn, task, result) else None

    def _run_ocr_stage(self, task) -> stages.StageResult | None:
        """OCR without holding a transaction across the engine call (see the module docstring)."""
        kwargs = {k: v for k, v in self.stage_kwargs.items()
                  if k in ("extractor", "factory_ref", "isolate", "defer_to_legacy_sweep")}
        defer = kwargs.pop("defer_to_legacy_sweep", True)
        with self.engine.begin() as conn:
            plan = stages.plan_ocr(conn, task, defer_to_legacy_sweep=defer)

        summary = stages.execute_ocr(plan, **kwargs) if plan.action == "run" else None

        # The engine call can outlive the lease on a pathological document. Renewing here, before the
        # settle transaction, keeps a slow-but-alive worker from being reclaimed out from under itself.
        with self.engine.begin() as conn:
            queue.renew_lease(conn, int(task["id"]), worker_id=self.worker_id,
                              lease_seconds=self.lease_seconds)
        with self.engine.begin() as conn:
            result = stages.settle_ocr(conn, plan, summary)
            return result if self._persist(conn, task, result) else None

    def _persist(self, conn, task, result: stages.StageResult) -> bool:
        """Write the stage transition. False means the lease was lost and nothing was written."""
        if result.next_stage == STAGE_DONE:
            state = STATE_REVIEW if result.outcome == OUTCOME_REVIEW else STATE_SUCCEEDED
            return queue.finish(conn, int(task["id"]), worker_id=self.worker_id,
                                outcome=result.outcome, state=state, note=result.note)
        return queue.advance(conn, int(task["id"]), worker_id=self.worker_id,
                             next_stage=result.next_stage, note=result.note,
                             lease_seconds=self.lease_seconds,
                             # The OCR stage reports which document it copied text from; that is
                             # retry lineage and belongs on the task row, not only in the note.
                             reused_from=result.detail.get("reused_from"))

    def _record_failure(self, task, exc: BaseException) -> None:
        """Classify a stage failure and either schedule a retry or file a blocker. Never raises."""
        error_class, reason_code = stages.classify_error(exc)
        attempts = int(task.get("attempts") or 1)
        exhausted = attempts >= int(task.get("max_attempts") or self.max_attempts)
        permanent = error_class == "permanent"
        try:
            with self.engine.begin() as conn:
                if permanent or exhausted:
                    queue.block(conn, int(task["id"]), worker_id=self.worker_id,
                                document_id=int(task["document_id"]), stage=task["stage"],
                                reason_code=reason_code if permanent else "attempts_exhausted",
                                detail=f"{reason_code}: {exc}", attempts=attempts,
                                actor_user_id=self.actor_user_id, request_id=self.request_id)
                    self.counters["blocked"] += 1
                else:
                    queue.retry_later(conn, int(task["id"]), worker_id=self.worker_id,
                                      error=f"{reason_code}: {exc}", error_class=error_class,
                                      attempts=attempts)
                    self.counters["retried"] += 1
        except Exception:      # noqa: BLE001 — recording a failure must not become a second failure
            log.exception("worker %s could not record the failure of document %s",
                          self.worker_id, task.get("document_id"))
        log.warning("document %s failed at %s (%s/%s, %s): %s", task.get("document_id"),
                    task.get("stage"), attempts, task.get("max_attempts"), error_class, exc)

    # --- continuous loop ---------------------------------------------------------------------

    def run_forever(self, *, stop_event: threading.Event | None = None,
                    max_passes: int | None = None) -> dict:
        """Claim and process until asked to stop. This is the "continuous" in continuous pipeline.

        ``max_passes`` bounds the loop for tests and for a scheduler tick that must return; production
        leaves it None and stops through ``stop_event``."""
        stop_event = stop_event or threading.Event()
        self.register()
        passes = 0
        try:
            while not stop_event.is_set() and (max_passes is None or passes < max_passes):
                passes += 1
                try:
                    result = self.run_once()
                except PipelineNotInstalled:
                    raise
                except Exception:      # noqa: BLE001 — one bad pass must not end the worker
                    log.exception("worker %s pass failed", self.worker_id)
                    stop_event.wait(self.idle_sleep)
                    continue
                if result["deferred"]:
                    stop_event.wait(self.pressure_sleep)
                elif not result["claimed"]:
                    stop_event.wait(self.idle_sleep)
        finally:
            self.shutdown()
        return {"worker_id": self.worker_id, "passes": passes, **self.counters}


def run_pool(*, workers: int = 2, stop_event: threading.Event | None = None,
             max_passes: int | None = None, **worker_kwargs) -> list[dict]:
    """Run ``workers`` workers in threads until ``stop_event`` is set, and collect their counters.

    Threads rather than processes: the expensive part of OCR already runs in its own child process
    (``document_ocr`` subprocess isolation), so the worker threads themselves are mostly waiting on
    the database and on that child. Processes would buy nothing here and would multiply the connection
    pool by the worker count."""
    stop_event = stop_event or threading.Event()
    started = datetime.now(UTC)
    results: list[dict] = []
    lock = threading.Lock()

    def _run(index: int):
        worker = Worker(worker_id=queue.new_worker_id(pid=os.getpid(), started=started) + f"#{index}",
                        **worker_kwargs)
        summary = worker.run_forever(stop_event=stop_event, max_passes=max_passes)
        with lock:
            results.append(summary)

    threads = [threading.Thread(target=_run, args=(i,), name=f"docpipe-worker-{i}", daemon=True)
               for i in range(max(1, int(workers)))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def drain(*, max_passes: int | None = None, **worker_kwargs) -> dict:
    """Process the queue until it is empty, in one worker, then return.

    This is how a scheduler tick drives the pipeline without owning a thread pool: it is the same
    worker and the same guarantees.

    ``max_passes`` defaults to None — UNBOUNDED — and the loop ends when there is nothing left to
    claim. A default pass budget would look like a safety net and behave like a work limit: with the
    default batch size of one document per pass, "1000 passes" is "1000 documents", which is exactly
    the silent truncation this pipeline exists to not have. A caller that genuinely needs to return
    within a time budget (the scheduler tick) passes a number, and the next call resumes from the
    untouched queue."""
    worker = Worker(**worker_kwargs)
    worker.register()
    passes = 0
    try:
        while max_passes is None or passes < max_passes:
            passes += 1
            result = worker.run_once()
            if result["deferred"] or not result["claimed"]:
                break
            time.sleep(0)      # yield, so a drain inside a thread is not a starvation loop
    finally:
        worker.shutdown()
    return {"worker_id": worker.worker_id, "passes": passes,
            "exhausted_budget": max_passes is not None and passes >= max_passes,
            **worker.counters}
