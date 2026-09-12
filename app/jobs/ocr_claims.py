"""Atomic per-document work claiming for parallel OCR sweeps.

WHY THIS EXISTS
---------------
``worker.py`` holds one session-level advisory lock (511005777) for its entire life, so a second
copy exits immediately and the corpus is swept by exactly one process. That lock is the only thing
preventing two workers from OCR-ing the same document, and it costs all parallelism to get it.

This module replaces it with a claim per document. The guarantees, and what provides each:

* **No duplicate processing** — a claim is one ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING``
  statement. Concurrent claims for the same document serialise on the primary key; the loser's
  ``DO UPDATE`` is gated on the lease having expired, so it updates nothing and the document is not
  returned to it. Exactly one worker ever holds a live claim.
* **Short claims** — claiming touches only this table, never ``document_ocr``, and holds no lock
  across OCR. The transaction is a single statement measured in milliseconds regardless of how long
  the document then takes.
* **Stale-worker recovery** — every claim carries a lease. A worker that is killed, crashes, or
  loses power writes nothing; its lease simply lapses and the next claimer takes the document over.
  No cleanup process is required for correctness.
* **One slow document cannot block others** — a lease covers one document. A worker grinding
  through a 300-page scan holds exactly that one claim; every other worker keeps claiming freely.
* **Completed documents are never reprocessed** — the candidate query excludes anything already
  ``completed``, and a claim row in state ``done`` is never handed out again.
* **Idempotent writes** — ``claim_seq`` increments on every (re)claim. A worker whose lease was
  stolen mid-document can be detected at write time (:func:`claim_is_current`) and its late result
  discarded, so a torn handover cannot produce two conflicting writes.

WHAT IT DOES NOT DO
-------------------
It does not OCR anything, does not write ``document_ocr``, and does not change candidate selection
semantics: the mode predicates below mirror ``worker.py``'s ``SQL_NEVER_ATTEMPTED`` and
``SQL_RETRYABLE`` exactly, so the initial and retry lanes keep their current meaning.
"""
from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass

from sqlalchemy import text

#: A claim is abandoned this long after its last heartbeat. Must comfortably exceed the OCR document
#: budget (default 300s) or a slow-but-healthy worker would have its document stolen mid-page.
DEFAULT_LEASE_SECONDS = 900

#: Heartbeat cadence. Well under the lease so a brief stall does not look like a death.
DEFAULT_HEARTBEAT_SECONDS = 60

#: The worker's scope predicate, identical to worker.py's ACTIVE. Deliberately stricter than the
#: stock sweep's ``status <> 'deleted'``.
ACTIVE = ("d.status = 'active' AND d.deleted_at IS NULL "
          "AND d.archived = false AND d.archived_at IS NULL")

#: Mirrors worker.py SQL_NEVER_ATTEMPTED / SQL_RETRYABLE. Changing these changes lane meaning.
_MODE_PREDICATE = {
    "initial": "(o.document_id IS NULL OR o.status IN ('pending', 'processing'))",
    "retry": "(o.status IN ('failed', 'timed_out') AND o.attempts < :max_attempts)",
}


def new_worker_id(prefix: str = "ocr") -> str:
    """A worker identity that is unique per process and readable in the claims table."""
    return f"{prefix}-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Claim:
    """One claimed document. ``seq`` identifies this particular tenure over the document."""
    document_id: int
    seq: int


# --- claiming ---------------------------------------------------------------------------------

_CLAIM_SQL = """
WITH candidate AS (
    SELECT d.id
      FROM documents d
      LEFT JOIN document_ocr o ON o.document_id = d.id
      LEFT JOIN ocr_document_claims c ON c.document_id = d.id
     WHERE {active}
       AND {mode_predicate}
       AND (
             c.document_id IS NULL                                   -- never claimed
          OR (c.state = 'claimed' AND c.lease_expires_at < now())    -- lease lapsed: recoverable
           )
     -- This LIMIT is the number of documents that will actually be CLAIMED, so it must equal the
     -- caller's limit exactly. Selecting a wider window to absorb contention would claim documents
     -- the caller never receives and leave them locked until their lease lapsed.
     ORDER BY d.id
     LIMIT :limit
)
INSERT INTO ocr_document_claims AS t
       (document_id, worker_id, state, claim_seq, claimed_at, heartbeat_at, lease_expires_at)
SELECT candidate.id, :worker_id, 'claimed', 1, now(), now(),
       now() + make_interval(secs => :lease_seconds)
  FROM candidate
    ON CONFLICT (document_id) DO UPDATE
       SET worker_id        = EXCLUDED.worker_id,
           state            = 'claimed',
           claim_seq        = t.claim_seq + 1,
           claimed_at       = EXCLUDED.claimed_at,
           heartbeat_at     = EXCLUDED.heartbeat_at,
           lease_expires_at = EXCLUDED.lease_expires_at,
           outcome          = NULL
     -- The whole race is decided here. A row that is done, or whose lease is still live, is NOT
     -- updated and therefore NOT returned, so the losing worker never sees the document.
     WHERE t.state <> 'done'
       AND t.lease_expires_at < now()
 RETURNING t.document_id, t.claim_seq
"""


def claim_batch(conn, *, worker_id, mode="initial", limit=25,
                lease_seconds=DEFAULT_LEASE_SECONDS, max_attempts=3):
    """Atomically claim up to ``limit`` documents. Returns exactly the claims won.

    Under contention the winner count can be lower than ``limit``: two workers may select the same
    candidates, but only one can take each. That is the design working, not an error — call again.
    Every document this statement claims is returned to the caller, so a claim can never be taken
    and then forgotten.
    """
    if mode not in _MODE_PREDICATE:
        raise ValueError(f"unknown claim mode {mode!r}")
    sql = _CLAIM_SQL.format(active=ACTIVE, mode_predicate=_MODE_PREDICATE[mode])
    params = {"worker_id": worker_id, "lease_seconds": float(lease_seconds), "limit": int(limit)}
    if mode == "retry":
        params["max_attempts"] = int(max_attempts)
    rows = conn.execute(text(sql), params).fetchall()
    return [Claim(document_id=r[0], seq=r[1]) for r in rows]


def heartbeat(conn, *, worker_id, document_ids, lease_seconds=DEFAULT_LEASE_SECONDS) -> int:
    """Extend the lease on documents this worker still holds. Returns rows refreshed.

    Only refreshes claims this worker still owns: if a lease was already stolen, the row no longer
    matches and the count comes back short, which is how a worker learns it lost a document.
    """
    if not document_ids:
        return 0
    result = conn.execute(text("""
        UPDATE ocr_document_claims
           SET heartbeat_at = now(),
               lease_expires_at = now() + make_interval(secs => :lease_seconds)
         WHERE worker_id = :worker_id
           AND state = 'claimed'
           AND document_id = ANY(:ids)
    """), {"worker_id": worker_id, "ids": list(document_ids),
           "lease_seconds": float(lease_seconds)})
    return result.rowcount or 0


def claim_is_current(conn, *, worker_id, claim: Claim) -> bool:
    """True if this worker still holds exactly this tenure of the document.

    Called immediately before persisting a result. A worker that was slow enough to lose its lease
    returns False here and drops its result instead of overwriting the new holder's work.
    """
    row = conn.execute(text("""
        SELECT 1 FROM ocr_document_claims
         WHERE document_id = :doc AND worker_id = :worker
           AND claim_seq = :seq AND state = 'claimed'
    """), {"doc": claim.document_id, "worker": worker_id, "seq": claim.seq}).first()
    return row is not None


def complete(conn, *, worker_id, claim: Claim, outcome) -> bool:
    """Mark a claim finished. Terminal: a ``done`` row is never handed out again."""
    result = conn.execute(text("""
        UPDATE ocr_document_claims
           SET state = 'done', outcome = :outcome, heartbeat_at = now()
         WHERE document_id = :doc AND worker_id = :worker AND claim_seq = :seq
           AND state = 'claimed'
    """), {"doc": claim.document_id, "worker": worker_id, "seq": claim.seq,
           "outcome": (str(outcome)[:100] if outcome is not None else None)})
    return bool(result.rowcount)


def release(conn, *, worker_id, document_ids) -> int:
    """Give claims back without completing them — a clean shutdown, or a throttle-driven stop.

    Expires the lease immediately rather than deleting the row, so the document is instantly
    claimable and the handover stays visible in the table.
    """
    if not document_ids:
        return 0
    result = conn.execute(text("""
        UPDATE ocr_document_claims
           SET state = 'released', lease_expires_at = now() - make_interval(secs => 1)
         WHERE worker_id = :worker_id AND state = 'claimed' AND document_id = ANY(:ids)
    """), {"worker_id": worker_id, "ids": list(document_ids)})
    return result.rowcount or 0


# --- observability ----------------------------------------------------------------------------

def stale_claims(conn, *, limit=100):
    """Claims whose lease has lapsed: workers that died holding work. Reclaim is automatic; this is
    for operators who want to see that it happened."""
    return [dict(r) for r in conn.execute(text("""
        SELECT document_id, worker_id, claim_seq, claimed_at, heartbeat_at, lease_expires_at
          FROM ocr_document_claims
         WHERE state = 'claimed' AND lease_expires_at < now()
         ORDER BY lease_expires_at
         LIMIT :limit
    """), {"limit": limit}).mappings()]


def reconcile(conn) -> dict:
    """Claim-table totals next to the OCR truth, so global counts can be proven to line up."""
    row = conn.execute(text(f"""
        SELECT
          (SELECT count(*) FROM ocr_document_claims)                                AS claims_total,
          (SELECT count(*) FROM ocr_document_claims WHERE state = 'claimed')        AS claimed,
          (SELECT count(*) FROM ocr_document_claims WHERE state = 'done')           AS done,
          (SELECT count(*) FROM ocr_document_claims WHERE state = 'released')       AS released,
          (SELECT count(*) FROM ocr_document_claims
            WHERE state = 'claimed' AND lease_expires_at < now())                   AS stale,
          (SELECT count(*) FROM documents d JOIN document_ocr o ON o.document_id = d.id
            WHERE {ACTIVE} AND o.status = 'completed')                              AS ocr_completed,
          (SELECT count(*) FROM ocr_document_claims c
             JOIN documents d ON d.id = c.document_id
             JOIN document_ocr o ON o.document_id = c.document_id
            WHERE c.state = 'done' AND o.status = 'completed')                      AS done_and_completed
    """)).mappings().first()
    return dict(row)


def duplicate_live_claims(conn) -> list:
    """Always empty: the primary key makes two live claims on one document unrepresentable. Kept as
    an explicit invariant the tests assert against rather than a comment nobody can run."""
    return [dict(r) for r in conn.execute(text("""
        SELECT document_id, count(*) AS n
          FROM ocr_document_claims
         WHERE state = 'claimed'
         GROUP BY document_id
        HAVING count(*) > 1
    """)).mappings()]
