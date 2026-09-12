"""Continuous discovery of new and changed documents.

THE BACKLOG IS NOT CAPPED
-------------------------
``page_size`` here is a PAGINATION size, not a work limit. :func:`discover` keeps requesting pages
until a page comes back short, which is the only honest definition of "the backlog is drained". The
existing sweeps take a ``limit`` that silently truncates the corpus — 30 here, 200 there, 500 in the
review queue — and a truncated sweep looks exactly like a finished one. That is the failure mode this
module is built to not have: the only bound on a discovery run is ``max_pages``, which defaults to
``None`` (unbounded) and exists so a scheduler tick can yield rather than so work can be dropped.

RESUMABLE BY CONSTRUCTION
-------------------------
Discovery walks ``documents.id`` FORWARD from a persisted cursor and commits each page separately. A
process killed mid-walk resumes at the last committed page instead of re-scanning from document 1.
The cursor only ever advances, so an empty pass cannot rewind it.

TWO KINDS OF WORK
-----------------
* NEW documents — anything past the cursor. This is the common case and it is O(new), not O(corpus).
* CHANGED documents — a document behind the cursor whose ``sha256`` no longer matches the hash its
  task was processed for. Those are found by comparing the task's ``content_sha256`` to the document's
  current hash, which is also exactly the test that makes re-processing an UNCHANGED document
  impossible. Idempotency and change detection are the same mechanism seen from two sides.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import documents, engine
from app.services.document_pipeline_continuous import queue
from app.services.document_pipeline_continuous.model import DISCOVERY_CHECKPOINT
from app.services.document_platform.lifecycle import live_document_clause

log = logging.getLogger(__name__)

#: Rows per discovery page. Large enough that walking a 120k-document corpus is a few hundred
#: round trips, small enough that one page is a short transaction.
DEFAULT_PAGE_SIZE = 1000


def discover_new_page(conn, *, page_size: int = DEFAULT_PAGE_SIZE, priority: int = 100,
                      max_attempts: int = 5) -> dict:
    """Enqueue one page of not-yet-seen documents and advance the cursor.

    Returns ``{seen, enqueued, cursor, exhausted}``. ``exhausted`` is True when the page came back
    shorter than ``page_size``, i.e. there is nothing past the cursor right now."""
    checkpoint = queue.read_checkpoint(conn, DISCOVERY_CHECKPOINT)
    cursor = int(checkpoint.get("cursor_document_id") or 0)
    # ``live_document_clause`` is the SHARED definition (app/services/document_platform/lifecycle.py)
    # of what a pipeline may act on. Filtering on ``status`` alone here would have enqueued 50
    # already-retired production documents — see that clause's docstring.
    rows = conn.execute(
        select(documents.c.id, documents.c.sha256)
        .where(documents.c.id > cursor, live_document_clause())
        .order_by(documents.c.id)
        .limit(int(page_size))).mappings().all()

    enqueued = 0
    highest = cursor
    for row in rows:
        highest = max(highest, int(row["id"]))
        if queue.enqueue(conn, int(row["id"]), content_sha256=row["sha256"],
                         priority=priority, max_attempts=max_attempts):
            enqueued += 1
    queue.write_checkpoint(conn, name=DISCOVERY_CHECKPOINT, cursor_document_id=highest,
                           seen=len(rows), enqueued=enqueued)
    return {"seen": len(rows), "enqueued": enqueued, "cursor": highest,
            "exhausted": len(rows) < page_size}


def discover_changed_page(conn, *, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    """Re-queue one page of documents whose content changed since their task last ran.

    Only tasks AT REST are considered — a leased task belongs to a live worker and is left alone (see
    :func:`queue.requeue`). Deleted documents are re-queued by nobody; their tasks are closed out by
    the extract stage the next time they are touched."""
    tasks = queue.tables()["tasks"]
    rows = conn.execute(
        select(tasks.c.document_id, documents.c.sha256)
        .select_from(tasks.join(documents, documents.c.id == tasks.c.document_id))
        .where(tasks.c.state.in_(("succeeded", "blocked", "review")),
               live_document_clause(),
               documents.c.sha256.isnot(None),
               tasks.c.content_sha256.is_distinct_from(documents.c.sha256))
        .order_by(tasks.c.document_id)
        .limit(int(page_size))).mappings().all()

    requeued = 0
    for row in rows:
        if queue.requeue(conn, int(row["document_id"]), content_sha256=row["sha256"],
                         reason="content_changed"):
            requeued += 1
    return {"seen": len(rows), "requeued": requeued, "exhausted": len(rows) < page_size}


def discover(*, page_size: int = DEFAULT_PAGE_SIZE, max_pages: int | None = None,
             priority: int = 100, max_attempts: int = 5, include_changed: bool = True,
             engine_=None) -> dict:
    """Drain discovery to completion: every new document enqueued, every changed document re-queued.

    Each page is its own transaction, so a crash costs at most one page of progress and the next run
    resumes from the committed cursor. ``max_pages=None`` (the default) means "keep going until there
    is nothing left", which is the behaviour the continuous pipeline needs; a caller that must bound
    its own tick passes a page budget and the next tick continues where this one stopped."""
    db = engine_ or engine
    totals = {"pages": 0, "seen": 0, "enqueued": 0, "changed_seen": 0, "requeued": 0,
              "cursor": None, "complete": False}

    while max_pages is None or totals["pages"] < max_pages:
        with db.begin() as conn:
            page = discover_new_page(conn, page_size=page_size, priority=priority,
                                     max_attempts=max_attempts)
        totals["pages"] += 1
        totals["seen"] += page["seen"]
        totals["enqueued"] += page["enqueued"]
        totals["cursor"] = page["cursor"]
        if page["exhausted"]:
            totals["complete"] = True
            break

    if include_changed:
        pages_left = None if max_pages is None else max(0, max_pages - totals["pages"])
        changed = 0
        while pages_left is None or changed < pages_left:
            with db.begin() as conn:
                page = discover_changed_page(conn, page_size=page_size)
            changed += 1
            totals["pages"] += 1
            totals["changed_seen"] += page["seen"]
            totals["requeued"] += page["requeued"]
            if page["exhausted"]:
                break
        else:
            totals["complete"] = False

    if totals["enqueued"] or totals["requeued"]:
        log.info("document pipeline discovery: enqueued=%s requeued=%s cursor=%s complete=%s",
                 totals["enqueued"], totals["requeued"], totals["cursor"], totals["complete"])
    return totals


def backlog_estimate(conn) -> dict:
    """How much work discovery has NOT yet turned into tasks, plus how much it already has.

    ``undiscovered`` is what sits past the cursor; ``pending`` is what is queued or in flight. An
    operator watching a migration wants both: a pending count that falls while ``undiscovered`` stays
    high means discovery, not processing, is the bottleneck."""
    checkpoint = queue.read_checkpoint(conn, DISCOVERY_CHECKPOINT)
    cursor = int(checkpoint.get("cursor_document_id") or 0)
    undiscovered = int(conn.execute(
        select(func.count()).select_from(documents)
        .where(documents.c.id > cursor, live_document_clause())).scalar() or 0)
    return {"cursor_document_id": cursor, "undiscovered": undiscovered,
            "pending": queue.pending_count(conn),
            "documents_seen": int(checkpoint.get("documents_seen") or 0),
            "documents_enqueued": int(checkpoint.get("documents_enqueued") or 0),
            "last_run_at": checkpoint.get("last_run_at")}
