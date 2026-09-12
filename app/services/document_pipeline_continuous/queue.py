"""The durable work queue: enqueue, claim, advance, retry, block.

WHY A QUEUE AND NOT A SWEEP
---------------------------
The existing OCR/analysis paths are sweeps: each invocation re-selects candidates from the top of the
corpus and works forward until it runs out of batches. A sweep cannot answer "which document is being
processed right now, by whom, and how far did it get" — so it cannot resume mid-corpus after a restart,
and two overlapping runs process the same document twice. This module replaces that with one durable
row per document whose transitions are the pipeline's memory.

MUTUAL EXCLUSION
----------------
``document_pipeline_tasks.document_id`` is UNIQUE, so a document has exactly one task row, and
:func:`claim` takes it with ``FOR UPDATE SKIP LOCKED`` inside the same statement that flips the row to
``leased`` and stamps ``lease_owner``/``lease_expires_at``. Two workers claiming concurrently cannot
both get the row: the loser's ``SKIP LOCKED`` steps over it and it claims the next one instead. The
lease — not the lock — is what survives the transaction, so a worker that dies holding a row does not
strand it: :func:`reclaim_expired_leases` returns it to the queue once the lease lapses.

IDEMPOTENCY
-----------
A task records the ``content_sha256`` it was enqueued for. Enqueueing the same document again is a
no-op while its content is unchanged (``ON CONFLICT DO NOTHING``); enqueueing it after its bytes change
re-queues it from the first stage. Re-running a stage is safe because every stage's own write is an
upsert against the document, not an append.

AUDIT SCOPE
-----------
Queue transitions are operational state and are NOT written to the audit hash chain. The chain
serialises writers on a per-chain advisory lock (``app/security/audit.py``), so auditing every lease
renewal would make the chain the pipeline's throughput ceiling and turn parallel workers into a queue
of one. What IS audited is every write that changes what the firm believes: ownership links (audited
inside ``households.resolve_document_ownership``), and blocker/review entries (audited here).
"""
from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.db import documents
from app.services.document_pipeline_continuous.model import (
    DISCOVERY_CHECKPOINT,
    OUTCOME_BLOCKED,
    STAGE_DISCOVERED,
    STAGE_DONE,
    STATE_BLOCKED,
    STATE_LEASED,
    STATE_QUEUED,
    STATE_SUCCEEDED,
    tables,
)

#: Retry backoff. The first retry waits ``RETRY_BASE_SECONDS``; each further attempt doubles, capped at
#: ``RETRY_CAP_SECONDS``. Deliberately pure and deterministic — a retry schedule you cannot predict is a
#: retry schedule you cannot test, and jitter buys nothing here because tasks are claimed in batches
#: rather than woken individually.
RETRY_BASE_SECONDS = 30
RETRY_CAP_SECONDS = 3600

#: A claimed task's lease. The worker renews it every heartbeat; if the worker dies, the lease lapses
#: and the task returns to the queue. Long enough that a genuinely slow document is not stolen from a
#: live worker, short enough that a crash is not a multi-hour hole.
DEFAULT_LEASE_SECONDS = 600


def retry_delay_seconds(attempts: int, *, base: int = RETRY_BASE_SECONDS,
                        cap: int = RETRY_CAP_SECONDS) -> int:
    """Seconds to wait before the ``attempts``-th retry. Exponential, capped, never negative."""
    if attempts <= 1:
        return base
    return min(cap, base * (2 ** (attempts - 1)))


def _now() -> datetime:
    return datetime.now(UTC)


def new_worker_id(*, pid: int, host: str | None = None, started: datetime | None = None) -> str:
    """A worker identity that is stable for the life of the process and unique across hosts, so two
    machines running the pipeline cannot silently share a lease owner."""
    stamp = (started or _now()).strftime("%Y%m%dT%H%M%SZ")
    return f"{host or socket.gethostname()}:{pid}:{stamp}"


# --- enqueue -------------------------------------------------------------------------------------

def enqueue(conn, document_id: int, *, content_sha256: str | None = None, priority: int = 100,
            max_attempts: int = 5) -> bool:
    """Create a task for one document. Returns True if a task was created, False if one already exists.

    Never resets an existing task — a document already in flight must not be dragged back to the first
    stage by a discovery pass that ran a second late. Re-queueing after a content change is
    :func:`requeue_changed`'s job, which is explicit about what it is overwriting."""
    t = tables()["tasks"]
    result = conn.execute(text(f"""
        INSERT INTO {t.name} (document_id, content_sha256, stage, state, priority, max_attempts)
        VALUES (:document_id, :sha, :stage, :state, :priority, :max_attempts)
        ON CONFLICT ON CONSTRAINT uq_document_pipeline_task_document DO NOTHING
    """), {"document_id": document_id, "sha": content_sha256, "stage": STAGE_DISCOVERED,
           "state": STATE_QUEUED, "priority": priority, "max_attempts": max_attempts})
    return bool(result.rowcount)


def requeue(conn, document_id: int, *, content_sha256: str | None = None,
            reason: str = "content_changed") -> bool:
    """Send a finished task back to the start because its document changed underneath it.

    Only touches tasks at rest (``succeeded``/``blocked``/``review``). A ``leased`` task is left alone:
    its worker is mid-document, and yanking the row would produce exactly the double-processing the
    lease exists to prevent — the next discovery pass picks it up once the worker lets go."""
    t = tables()["tasks"]
    result = conn.execute(text(f"""
        UPDATE {t.name}
           SET stage = :stage, state = :queued, content_sha256 = :sha,
               attempts = 0, stage_attempts = 0, available_at = now(),
               lease_owner = NULL, leased_at = NULL, lease_expires_at = NULL,
               outcome = NULL, last_error = NULL, last_error_class = NULL,
               reused_ocr_from_document_id = NULL,
               started_at = NULL, completed_at = NULL,
               stage_history = stage_history || CAST(:entry AS jsonb),
               updated_at = now()
         WHERE document_id = :document_id
           AND state IN ('succeeded', 'blocked', 'review')
    """), {"document_id": document_id, "sha": content_sha256, "stage": STAGE_DISCOVERED,
           "queued": STATE_QUEUED,
           "entry": json.dumps([{"at": _now().isoformat(), "event": "requeued", "reason": reason}])})
    return bool(result.rowcount)


# --- claim / lease -------------------------------------------------------------------------------

def claim(conn, *, worker_id: str, limit: int = 1,
          lease_seconds: int = DEFAULT_LEASE_SECONDS) -> list[dict]:
    """Atomically lease up to ``limit`` runnable tasks to ``worker_id``.

    Runnable means ``state='queued'`` and ``available_at <= now()`` — the backoff clock and the queue
    are the same mechanism. ``FOR UPDATE SKIP LOCKED`` is what makes concurrent claims safe AND fast:
    a second worker never waits behind the first, it simply takes different rows. Returns the leased
    rows as dicts (empty when there is nothing to do)."""
    t = tables()["tasks"]
    rows = conn.execute(text(f"""
        WITH claimable AS (
            SELECT id
              FROM {t.name}
             WHERE state = :queued
               AND available_at <= now()
             ORDER BY priority, available_at, id
             LIMIT :limit
             FOR UPDATE SKIP LOCKED
        )
        UPDATE {t.name} AS task
           SET state = :leased,
               lease_owner = :worker_id,
               leased_at = now(),
               lease_expires_at = now() + make_interval(secs => :lease_seconds),
               attempts = task.attempts + 1,
               stage_attempts = task.stage_attempts + 1,
               started_at = COALESCE(task.started_at, now()),
               updated_at = now()
          FROM claimable
         WHERE task.id = claimable.id
        RETURNING task.id, task.document_id, task.stage, task.state, task.attempts,
                  task.stage_attempts, task.max_attempts, task.content_sha256,
                  task.lease_expires_at, task.priority
    """), {"queued": STATE_QUEUED, "leased": STATE_LEASED, "worker_id": worker_id,
           "limit": int(limit), "lease_seconds": int(lease_seconds)}).mappings().all()
    return [dict(row) for row in rows]


def renew_lease(conn, task_id: int, *, worker_id: str,
                lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    """Extend this worker's lease on a task it still holds. False means the lease was lost (reclaimed
    while the worker was busy), which the worker must treat as "stop touching this document"."""
    t = tables()["tasks"]
    result = conn.execute(text(f"""
        UPDATE {t.name}
           SET lease_expires_at = now() + make_interval(secs => :lease_seconds), updated_at = now()
         WHERE id = :task_id AND state = :leased AND lease_owner = :worker_id
    """), {"task_id": task_id, "leased": STATE_LEASED, "worker_id": worker_id,
           "lease_seconds": int(lease_seconds)})
    return bool(result.rowcount)


def reclaim_expired_leases(conn, *, limit: int = 500) -> int:
    """Return tasks whose lease lapsed to the queue, and count the attempt against them.

    This is the pipeline's crash recovery. A worker killed mid-document (a service restart, a machine
    reboot, an OOM) leaves its rows in ``leased`` with a lease that stops being renewed; once the lease
    is in the past any other worker may take the row. The attempt already counted at claim time stands,
    so a document that reliably kills its worker exhausts its attempts and becomes a blocker rather
    than an infinite restart loop."""
    t = tables()["tasks"]
    result = conn.execute(text(f"""
        WITH expired AS (
            SELECT id FROM {t.name}
             WHERE state = :leased AND lease_expires_at IS NOT NULL AND lease_expires_at < now()
             ORDER BY lease_expires_at
             LIMIT :limit
             FOR UPDATE SKIP LOCKED
        )
        UPDATE {t.name} AS task
           SET state = :queued,
               lease_owner = NULL, leased_at = NULL, lease_expires_at = NULL,
               available_at = now(),
               last_error_class = 'lease_expired',
               stage_history = task.stage_history || CAST(:entry AS jsonb),
               updated_at = now()
          FROM expired
         WHERE task.id = expired.id
    """), {"leased": STATE_LEASED, "queued": STATE_QUEUED, "limit": int(limit),
           "entry": json.dumps([{"at": _now().isoformat(), "event": "lease_expired"}])})
    return int(result.rowcount or 0)


# --- stage transitions ---------------------------------------------------------------------------

def advance(conn, task_id: int, *, worker_id: str, next_stage: str, note: str | None = None,
            lease_seconds: int = DEFAULT_LEASE_SECONDS, reused_from: int | None = None) -> bool:
    """Record that the current stage finished and move the task to ``next_stage``, keeping the lease.

    The stage is persisted the moment it completes, which is what makes a restart resume rather than
    restart: a worker killed during ``ownership`` comes back to a task already past ``extract``,
    ``ocr`` and ``classify``, and re-runs only the stage that did not finish.

    ``reused_from`` records the document this one's OCR text was copied from, as a real foreign key.
    ``document_ocr.engine`` also says ``'reused:<id>'``, but that is one row per DOCUMENT and is
    overwritten the next time the document is extracted — so it answers "where does the text on this
    document come from now", not "what did this task do". The task's own column is the retry lineage,
    it survives a later re-OCR, and being a foreign key it cannot dangle. COALESCE so a later stage
    advancing the same task does not erase it."""
    t = tables()["tasks"]
    entry = {"at": _now().isoformat(), "event": "advance", "to": next_stage}
    if note:
        entry["note"] = note
    result = conn.execute(text(f"""
        UPDATE {t.name}
           SET stage = :next_stage, stage_attempts = 0,
               lease_expires_at = now() + make_interval(secs => :lease_seconds),
               reused_ocr_from_document_id =
                   COALESCE(CAST(:reused_from AS integer), reused_ocr_from_document_id),
               stage_history = stage_history || CAST(:entry AS jsonb),
               updated_at = now()
         WHERE id = :task_id AND state = :leased AND lease_owner = :worker_id
    """), {"task_id": task_id, "next_stage": next_stage, "leased": STATE_LEASED,
           "worker_id": worker_id, "lease_seconds": int(lease_seconds),
           "reused_from": reused_from, "entry": json.dumps([entry])})
    return bool(result.rowcount)


def finish(conn, task_id: int, *, worker_id: str, outcome: str,
           state: str = STATE_SUCCEEDED, note: str | None = None) -> bool:
    """Complete a task terminally with the ownership verdict the last stage reached."""
    t = tables()["tasks"]
    entry = {"at": _now().isoformat(), "event": "finish", "outcome": outcome, "state": state}
    if note:
        entry["note"] = note
    result = conn.execute(text(f"""
        UPDATE {t.name}
           SET state = :state, stage = :done, outcome = :outcome,
               lease_owner = NULL, leased_at = NULL, lease_expires_at = NULL,
               completed_at = now(),
               stage_history = stage_history || CAST(:entry AS jsonb),
               updated_at = now()
         WHERE id = :task_id AND state = :leased AND lease_owner = :worker_id
    """), {"task_id": task_id, "state": state, "done": STAGE_DONE, "outcome": outcome,
           "leased": STATE_LEASED, "worker_id": worker_id, "entry": json.dumps([entry])})
    return bool(result.rowcount)


def retry_later(conn, task_id: int, *, worker_id: str, error: str, error_class: str = "transient",
                attempts: int | None = None) -> bool:
    """Release a task back to the queue with exponential backoff after a transient failure."""
    t = tables()["tasks"]
    delay = retry_delay_seconds(attempts if attempts is not None else 1)
    entry = {"at": _now().isoformat(), "event": "retry", "class": error_class, "in_seconds": delay}
    result = conn.execute(text(f"""
        UPDATE {t.name}
           SET state = :queued,
               lease_owner = NULL, leased_at = NULL, lease_expires_at = NULL,
               available_at = now() + make_interval(secs => :delay),
               last_error = :error, last_error_class = :error_class,
               stage_history = stage_history || CAST(:entry AS jsonb),
               updated_at = now()
         WHERE id = :task_id AND state = :leased AND lease_owner = :worker_id
    """), {"task_id": task_id, "queued": STATE_QUEUED, "leased": STATE_LEASED, "delay": delay,
           "error": (error or "")[:2000], "error_class": error_class, "worker_id": worker_id,
           "entry": json.dumps([entry])})
    return bool(result.rowcount)


def block(conn, task_id: int, *, worker_id: str, document_id: int, stage: str, reason_code: str,
          detail: str | None = None, attempts: int = 0, actor_user_id=None,
          request_id: str | None = None) -> bool:
    """Move a permanently-failed task out of the queue and into the visible blocker queue.

    A blocked task is not retried. That is the point: a document that cannot be processed is an
    operational fact someone has to see and act on, and a queue that keeps re-attempting it hides that
    fact behind a failure count that never stops climbing."""
    t = tables()["tasks"]
    entry = {"at": _now().isoformat(), "event": "blocked", "reason": reason_code}
    moved = conn.execute(text(f"""
        UPDATE {t.name}
           SET state = :blocked, outcome = :outcome,
               lease_owner = NULL, leased_at = NULL, lease_expires_at = NULL,
               completed_at = now(),
               last_error = :detail, last_error_class = :reason_code,
               stage_history = stage_history || CAST(:entry AS jsonb),
               updated_at = now()
         WHERE id = :task_id AND state = :leased AND lease_owner = :worker_id
    """), {"task_id": task_id, "blocked": STATE_BLOCKED, "leased": STATE_LEASED,
           "outcome": OUTCOME_BLOCKED, "detail": (detail or "")[:2000], "reason_code": reason_code,
           "worker_id": worker_id, "entry": json.dumps([entry])})
    if not moved.rowcount:
        return False
    record_blocker(conn, document_id=document_id, stage=stage, reason_code=reason_code,
                   detail=detail, attempts=attempts, actor_user_id=actor_user_id,
                   request_id=request_id)
    return True


# --- blocker queue -------------------------------------------------------------------------------

def record_blocker(conn, *, document_id: int, stage: str, reason_code: str, detail: str | None = None,
                   attempts: int = 0, actor_user_id=None, request_id: str | None = None) -> None:
    """Open (or refresh) the single blocker row for a document, and audit it.

    One row per document: a document blocked twice updates its row rather than adding a second, so the
    blocker count is a count of blocked DOCUMENTS and stays a number an operator can act on."""
    b = tables()["blockers"]
    conn.execute(text(f"""
        INSERT INTO {b.name} (document_id, stage, reason_code, detail, attempts, status)
        VALUES (:document_id, :stage, :reason_code, :detail, :attempts, 'open')
        ON CONFLICT ON CONSTRAINT uq_document_pipeline_blocker_document
        DO UPDATE SET stage = EXCLUDED.stage, reason_code = EXCLUDED.reason_code,
                      detail = EXCLUDED.detail, attempts = EXCLUDED.attempts,
                      status = 'open', resolved_at = NULL, resolved_by_user_id = NULL,
                      last_seen_at = now()
    """), {"document_id": document_id, "stage": stage, "reason_code": reason_code,
           "detail": (detail or "")[:2000], "attempts": int(attempts)})
    _audit(conn, action="document_pipeline.blocked", entity_id=document_id,
           actor_user_id=actor_user_id, request_id=request_id,
           metadata={"stage": stage, "reason_code": reason_code, "attempts": int(attempts)})


def resolve_blocker(conn, *, document_id: int, status: str = "resolved", actor_user_id=None,
                    note: str | None = None, request_id: str | None = None) -> bool:
    """Close a blocker after a human dealt with it. Does NOT re-queue the document — re-queueing is an
    explicit, separately audited act (:func:`requeue`), because "I looked at it" and "process it
    again" are different decisions and conflating them re-runs work nobody asked for."""
    b = tables()["blockers"]
    result = conn.execute(text(f"""
        UPDATE {b.name}
           SET status = :status, resolved_at = now(), resolved_by_user_id = :actor,
               resolution_note = :note, last_seen_at = now()
         WHERE document_id = :document_id AND status = 'open'
    """), {"document_id": document_id, "status": status, "actor": actor_user_id, "note": note})
    if result.rowcount:
        _audit(conn, action=f"document_pipeline.blocker_{status}", entity_id=document_id,
               actor_user_id=actor_user_id, request_id=request_id, metadata={"note": note})
    return bool(result.rowcount)


def open_blockers(conn, *, limit: int = 100, offset: int = 0) -> list[dict]:
    """The visible blocker queue: open blockers newest-first, with the document they belong to."""
    b = tables()["blockers"]
    rows = conn.execute(text(f"""
        SELECT blocker.document_id, blocker.stage, blocker.reason_code, blocker.detail,
               blocker.attempts, blocker.first_seen_at, blocker.last_seen_at,
               document.original_name
          FROM {b.name} AS blocker
          JOIN documents AS document ON document.id = blocker.document_id
         WHERE blocker.status = 'open'
         ORDER BY blocker.last_seen_at DESC, blocker.document_id
         LIMIT :limit OFFSET :offset
    """), {"limit": int(limit), "offset": int(offset)}).mappings().all()
    return [dict(row) for row in rows]


# --- ownership review lane -----------------------------------------------------------------------

def record_review(conn, *, document_id: int, lane: str, reason_code: str, evidence=None,
                  candidates=None, actor_user_id=None, request_id: str | None = None) -> None:
    """Open (or refresh) this document's row in THE ownership review queue, and audit it.

    ``evidence`` and ``candidates`` must already be sanitised by the caller — this queue is read by
    staff and must never carry raw document text or a full SSN/TIN."""
    r = tables()["reviews"]
    conn.execute(text(f"""
        INSERT INTO {r.name} (document_id, lane, reason_code, evidence, candidates, status)
        VALUES (:document_id, :lane, :reason_code, CAST(:evidence AS jsonb),
                CAST(:candidates AS jsonb), 'open')
        ON CONFLICT ON CONSTRAINT uq_document_pipeline_review_document
        DO UPDATE SET lane = EXCLUDED.lane, reason_code = EXCLUDED.reason_code,
                      evidence = EXCLUDED.evidence, candidates = EXCLUDED.candidates,
                      status = 'open', resolved_at = NULL, resolved_by_user_id = NULL,
                      updated_at = now()
    """), {"document_id": document_id, "lane": lane, "reason_code": reason_code,
           "evidence": json.dumps(evidence or []), "candidates": json.dumps(candidates or [])})
    _audit(conn, action="document_pipeline.review_opened", entity_id=document_id,
           actor_user_id=actor_user_id, request_id=request_id,
           metadata={"lane": lane, "reason_code": reason_code})


def resolve_review(conn, *, document_id: int, status: str = "resolved", actor_user_id=None,
                   note: str | None = None, request_id: str | None = None) -> bool:
    """Close a review row once a human decided. The ownership write itself goes through the existing
    ``households.resolve_document_ownership`` path — this only closes the queue entry."""
    r = tables()["reviews"]
    result = conn.execute(text(f"""
        UPDATE {r.name}
           SET status = :status, resolved_at = now(), resolved_by_user_id = :actor,
               resolution_note = :note, updated_at = now()
         WHERE document_id = :document_id AND status = 'open'
    """), {"document_id": document_id, "status": status, "actor": actor_user_id, "note": note})
    if result.rowcount:
        _audit(conn, action=f"document_pipeline.review_{status}", entity_id=document_id,
               actor_user_id=actor_user_id, request_id=request_id, metadata={"note": note})
    return bool(result.rowcount)


def open_reviews(conn, *, lane: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    """The one ownership review queue, newest-first, optionally narrowed to a lane."""
    r = tables()["reviews"]
    clause = "AND review.lane = :lane" if lane else ""
    rows = conn.execute(text(f"""
        SELECT review.document_id, review.lane, review.reason_code, review.evidence,
               review.candidates, review.opened_at, review.updated_at, document.original_name
          FROM {r.name} AS review
          JOIN documents AS document ON document.id = review.document_id
         WHERE review.status = 'open' {clause}
         ORDER BY review.updated_at DESC, review.document_id
         LIMIT :limit OFFSET :offset
    """), {"lane": lane, "limit": int(limit), "offset": int(offset)}).mappings().all()
    return [dict(row) for row in rows]


# --- checkpoints ---------------------------------------------------------------------------------

def read_checkpoint(conn, name: str = DISCOVERY_CHECKPOINT) -> dict:
    """The discovery resume point. Created on first read if the seed row is somehow absent."""
    c = tables()["checkpoints"]
    row = conn.execute(select(c).where(c.c.name == name)).mappings().first()
    if row is None:
        conn.execute(text(f"""
            INSERT INTO {c.name} (name) VALUES (:name)
            ON CONFLICT ON CONSTRAINT uq_document_pipeline_checkpoint_name DO NOTHING
        """), {"name": name})
        row = conn.execute(select(c).where(c.c.name == name)).mappings().first()
    return dict(row)


def write_checkpoint(conn, *, name: str = DISCOVERY_CHECKPOINT, cursor_document_id: int | None = None,
                     seen: int = 0, enqueued: int = 0, error: str | None = None) -> None:
    """Advance the discovery cursor. The cursor only ever moves FORWARD — a pass that found nothing
    must not rewind it, or discovery would re-walk the corpus from wherever the last empty pass
    started and the backlog would never drain."""
    c = tables()["checkpoints"]
    conn.execute(text(f"""
        UPDATE {c.name}
           SET cursor_document_id = GREATEST(cursor_document_id, COALESCE(:cursor, 0)),
               cursor_updated_at = now(),
               documents_seen = documents_seen + :seen,
               documents_enqueued = documents_enqueued + :enqueued,
               last_run_at = now(), last_error = :error, updated_at = now()
         WHERE name = :name
    """), {"name": name, "cursor": cursor_document_id, "seen": int(seen),
           "enqueued": int(enqueued), "error": (error or None)})


# --- worker registry -----------------------------------------------------------------------------

def register_worker(conn, *, worker_id: str, pid: int, host: str | None = None,
                    state: str = "starting") -> None:
    """Record (or re-record) a worker. A restarted process with the same identity updates its row
    rather than leaving a ghost behind."""
    w = tables()["workers"]
    conn.execute(text(f"""
        INSERT INTO {w.name} (worker_id, host, pid, state)
        VALUES (:worker_id, :host, :pid, :state)
        ON CONFLICT ON CONSTRAINT uq_document_pipeline_worker
        DO UPDATE SET host = EXCLUDED.host, pid = EXCLUDED.pid, state = EXCLUDED.state,
                      started_at = now(), last_heartbeat_at = now(), stopped_at = NULL
    """), {"worker_id": worker_id, "host": host or socket.gethostname(), "pid": int(pid),
           "state": state})


def heartbeat_worker(conn, *, worker_id: str, state: str = "running",
                     current_document_id: int | None = None, claimed: int = 0,
                     completed: int = 0, failed: int = 0) -> None:
    """The liveness signal the stall detector reads. Advanced between documents AND during one, so a
    worker grinding through a 400-page scan reads as alive rather than as frozen."""
    w = tables()["workers"]
    conn.execute(text(f"""
        UPDATE {w.name}
           SET last_heartbeat_at = now(), state = :state, current_document_id = :current,
               claimed_total = claimed_total + :claimed,
               completed_total = completed_total + :completed,
               failed_total = failed_total + :failed
         WHERE worker_id = :worker_id
    """), {"worker_id": worker_id, "state": state, "current": current_document_id,
           "claimed": int(claimed), "completed": int(completed), "failed": int(failed)})


def stop_worker(conn, *, worker_id: str) -> None:
    """Mark a worker stopped on a clean shutdown, so the stall detector does not report a deliberate
    stop as a stall."""
    w = tables()["workers"]
    conn.execute(text(f"""
        UPDATE {w.name}
           SET state = 'stopped', stopped_at = now(), last_heartbeat_at = now(),
               current_document_id = NULL
         WHERE worker_id = :worker_id
    """), {"worker_id": worker_id})


def release_worker_tasks(conn, *, worker_id: str) -> int:
    """Return every task this worker holds to the queue. Called on a clean shutdown so a planned
    restart does not wait out the lease before its work becomes claimable again."""
    t = tables()["tasks"]
    result = conn.execute(text(f"""
        UPDATE {t.name}
           SET state = :queued, lease_owner = NULL, leased_at = NULL, lease_expires_at = NULL,
               available_at = now(), updated_at = now()
         WHERE state = :leased AND lease_owner = :worker_id
    """), {"queued": STATE_QUEUED, "leased": STATE_LEASED, "worker_id": worker_id})
    return int(result.rowcount or 0)


# --- helpers -------------------------------------------------------------------------------------

def task_for_document(conn, document_id: int) -> dict | None:
    t = tables()["tasks"]
    row = conn.execute(select(t).where(t.c.document_id == document_id)).mappings().first()
    return dict(row) if row else None


def pending_count(conn) -> int:
    """Documents still to process: queued (including backed-off retries) plus in flight."""
    t = tables()["tasks"]
    return int(conn.execute(select(func.count()).select_from(t).where(
        t.c.state.in_((STATE_QUEUED, STATE_LEASED)))).scalar() or 0)


def completed_document_with_text(conn, sha256: str, *, exclude_document_id: int) -> dict | None:
    """Another canonical document with the SAME content whose text extraction already completed.

    This is the deduplication seam. The firm's corpus carries the same W-2 or 1099 filed under several
    folders; SHA-256 says they are byte-identical, so OCR'ing the second copy can only produce the text
    already stored for the first. Reusing it turns the most expensive stage in the pipeline into a
    single-row copy."""
    if not sha256:
        return None
    row = conn.execute(text("""
        SELECT ocr.document_id, ocr.text, ocr.engine, ocr.page_count, ocr.char_count
          FROM document_ocr AS ocr
          JOIN documents AS document ON document.id = ocr.document_id
         WHERE document.sha256 = :sha
           AND document.id <> :exclude
           AND ocr.status = 'completed'
           AND ocr.text IS NOT NULL
           AND COALESCE(ocr.char_count, 0) > 0
         ORDER BY ocr.char_count DESC, ocr.document_id
         LIMIT 1
    """), {"sha": sha256, "exclude": int(exclude_document_id)}).mappings().first()
    return dict(row) if row else None


def _audit(conn, *, action: str, entity_id, actor_user_id=None, request_id=None, metadata=None):
    """Append one audit-chain entry inside the caller's transaction. Never raises: an audit failure
    must not roll back the operational write it describes, and the failure is visible in the log."""
    try:
        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type="document", entity_id=entity_id,
                          actor_user_id=actor_user_id,
                          request_id=request_id or "document-pipeline",
                          metadata=metadata or {}, conn=conn)
    except Exception:      # noqa: BLE001 — see docstring
        import logging
        logging.getLogger(__name__).exception("pipeline audit write failed for %s", action)


def document_content_hash(conn, document_id: int) -> str | None:
    return conn.execute(select(documents.c.sha256).where(documents.c.id == document_id)).scalar()


def stale_lease_cutoff(seconds: int) -> datetime:
    return _now() - timedelta(seconds=seconds)


__all__ = [
    "DEFAULT_LEASE_SECONDS", "RETRY_BASE_SECONDS", "RETRY_CAP_SECONDS",
    "advance", "block", "claim", "completed_document_with_text", "document_content_hash",
    "enqueue", "finish", "heartbeat_worker", "new_worker_id", "open_blockers", "open_reviews",
    "pending_count", "read_checkpoint", "reclaim_expired_leases", "record_blocker", "record_review",
    "register_worker", "release_worker_tasks", "renew_lease", "requeue", "resolve_blocker",
    "resolve_review", "retry_delay_seconds", "retry_later", "stale_lease_cutoff", "stop_worker",
    "task_for_document", "write_checkpoint",
]
