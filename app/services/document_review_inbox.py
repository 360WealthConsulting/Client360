"""Unified staff review inbox — a single landing that shows the top-line document-review backlog and links
to every review lane, so a reviewer no longer has to know five separate URLs (readiness-audit gap).

Deliberately CHEAP and read-only: it reports counts that are simple SQL aggregates (the unowned-document
backlog + the OCR state distribution — directly reflecting the ingestion output). It does NOT re-run the
expensive per-document proposal engines that the individual lane pages use; each lane computes its own
detailed HIGH/MEDIUM/AMBIGUOUS breakdown on demand when opened."""
from __future__ import annotations

from sqlalchemy import and_, func, select

from app.db import document_ocr, documents, engine
from app.services.document_deferral import deferred_counts
from app.services.document_owner_proposal import PERMANENT_REJECT_DOCUMENT_IDS
from app.services.document_platform.lifecycle import not_deferred_clause

# The five human-review lanes and where each lives (labels + URLs only — no recompute here).
REVIEW_LANES = [
    {"key": "unassigned", "label": "Unassigned documents", "url": "/admin/documents/unassigned",
     "desc": "Unowned documents awaiting folder/document assignment."},
    {"key": "review_queue", "label": "Review queue", "url": "/admin/documents/review-queue",
     "desc": "MEDIUM / AMBIGUOUS / HIGH-review owner proposals to approve."},
    {"key": "high_confirm", "label": "HIGH bulk-confirm", "url": "/admin/documents/high-confirm",
     "desc": "Clean HIGH-confidence proposals to confirm in bulk."},
    {"key": "entity_proposals", "label": "New-entity proposals", "url": "/admin/documents/entity-proposals",
     "desc": "Corroborated new person/household/business to approve, reject, or map to an existing entity."},
    {"key": "context_review", "label": "Context review", "url": "/admin/documents/context-review",
     "desc": "NO_MATCH documents resolvable from surrounding folder context."},
    {"key": "deferred_ownership", "label": "Deferred ownership",
     "url": "/admin/documents/unassigned?lane=deferred",
     "desc": "Valid documents whose owner is not currently provable. Parked, not lost — "
             "searchable, fully preserved, and promotable the moment evidence appears."},
]


def inbox_summary():
    """Cheap, read-only top-line backlog: the unowned-document count and the OCR state distribution.

    ``unassigned_documents`` = active, non-archived documents with no person/household/organization owner,
    not on the permanent-reject list, and NOT parked in the deferred-ownership lane — the ACTIONABLE
    human-review backlog, i.e. the work a reviewer can actually finish.

    ``deferred_ownership`` is reported separately, never folded into the line above and never hidden:
    a deferred document is one whose owner is not currently provable (see
    ``app.services.document_deferral``), so counting it as outstanding work made the backlog a number
    that could not go down. ``deferred_by_reason`` breaks it out so the split is auditable rather than
    a single opaque figure, and ``unassigned_total`` preserves the pre-existing meaning for anyone
    reconciling against an older report.

    ``ocr`` buckets reflect the running ingestion (``pending`` includes documents with no OCR row yet
    plus pending/processing rows)."""
    reject = tuple(PERMANENT_REJECT_DOCUMENT_IDS) or (-1,)
    with engine.connect() as conn:
        unowned = and_(
            documents.c.person_id.is_(None), documents.c.household_id.is_(None),
            documents.c.organization_id.is_(None), documents.c.status != "deleted",
            documents.c.archived.is_(False), documents.c.id.notin_(reject))
        unassigned = conn.execute(
            select(func.count()).select_from(documents)
            .where(unowned, not_deferred_clause())).scalar() or 0
        unassigned_total = conn.execute(
            select(func.count()).select_from(documents).where(unowned)).scalar() or 0
        deferred = deferred_counts(conn)
        total_docs = conn.execute(select(func.count()).select_from(documents)
                                  .where(documents.c.status != "deleted")).scalar() or 0
        ocr_rows = dict(conn.execute(
            select(document_ocr.c.status, func.count()).group_by(document_ocr.c.status)).all())
    with_ocr = sum(ocr_rows.values())
    ocr = {
        "completed": ocr_rows.get("completed", 0),
        "failed": ocr_rows.get("failed", 0),
        "timed_out": ocr_rows.get("timed_out", 0),
        "unsupported": ocr_rows.get("unsupported", 0),
        "pending": max(0, total_docs - with_ocr) + ocr_rows.get("pending", 0) + ocr_rows.get("processing", 0),
    }
    deferred_total = deferred.pop("total", 0)
    return {"unassigned_documents": unassigned, "unassigned_total": unassigned_total,
            "deferred_ownership": deferred_total, "deferred_by_reason": deferred,
            "total_documents": total_docs, "ocr": ocr, "lanes": REVIEW_LANES}
