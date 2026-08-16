"""Phase 3 — efficient per-document review queue for MEDIUM + AMBIGUOUS (and HIGH-review) proposals.

A READ-ONLY consolidated queue over the genuinely-unassigned documents whose owner proposal is MEDIUM or
AMBIGUOUS — the cases that need a human choice — plus, for completeness, the HIGH proposals a contradiction
kicked out to review (per-document only, never bulk-selectable). Each row carries the evidence and the
defensible candidate owners so the admin can decide from the review page itself, then approve through the
EXISTING atomic write path. Nothing here assigns ownership, creates a client, merges records, or touches
the 109 validated HIGH bulk-confirm set or the new-entity proposals.

Approval reuses ``households.resolve_document_ownership`` (atomic all-NULL-AND-not-reject recheck in the
write statement, never overwrites an owner, writes the ``document.ownership_resolved`` audit) exactly like
the existing per-document manual resolution — plus the same record-scope check the unassigned-resolution
route applies. It never re-evaluates into a different owner: it assigns only the candidate the admin
clicked, and only if the document is still unowned.
"""
from __future__ import annotations

from app.db import engine
from app.security.audit import write_audit_event
from app.services.document_high_validation import (
    _contradictions,
    _unassigned_ids,
    build_report_row,
)
from app.services.document_owner_proposal import build_match_indexes, propose_document_owner


def _row_with_candidates(conn, did, proposal, contradictions, idx, candidates):
    row = build_report_row(conn, did, proposal, contradictions, idx)
    row["candidates"] = candidates
    return row


def review_queue(*, limit=None, include_high_review=True, ocr=False):
    """READ-ONLY. Returns {medium:[row], ambiguous:[row], high_review:[row], *_count}. Each row has
    document_id, filename, source_path, extraction method/class, confidence, evidence, and `candidates`
    (a list of {type,id,name}). MEDIUM: one prominent proposed candidate. AMBIGUOUS: all defensible
    candidates, no default. high_review: HIGH proposals a contradiction sent to review. Writes nothing."""
    medium, ambiguous, high_review = [], [], []
    with engine.connect() as conn:
        ids = _unassigned_ids(conn, limit=limit)
        idx = build_match_indexes(conn)
        for did in ids:
            proposal = propose_document_owner(did, conn=conn, idx=idx, with_text=True, ocr=ocr)
            if not proposal.get("eligible"):
                continue
            text = proposal.pop("text", "")
            conf = proposal.get("confidence")
            if conf == "MEDIUM":
                cand = [{"type": proposal.get("proposed_entity_type"),
                         "id": proposal.get("proposed_entity_id"),
                         "name": proposal.get("proposed_entity_name")}]
                medium.append(_row_with_candidates(conn, did, proposal, [], idx, cand))
            elif conf == "AMBIGUOUS":
                cands = [{"type": "person", "id": c.get("person_id"), "name": c.get("name")}
                         for c in (proposal.get("best_candidates") or []) if c.get("person_id")]
                ambiguous.append(_row_with_candidates(conn, did, proposal, [], idx, cands))
            elif conf == "HIGH" and include_high_review:
                contradictions, _sig = _contradictions(proposal, text, proposal.get("source_folder"), idx)
                if contradictions:                         # HIGH kicked out to review (never bulk-selectable)
                    cand = [{"type": proposal.get("proposed_entity_type"),
                             "id": proposal.get("proposed_entity_id"),
                             "name": proposal.get("proposed_entity_name")}]
                    high_review.append(_row_with_candidates(conn, did, proposal, contradictions, idx, cand))
    return {"medium": medium, "ambiguous": ambiguous, "high_review": high_review,
            "medium_count": len(medium), "ambiguous_count": len(ambiguous),
            "high_review_count": len(high_review)}


def approve_ownership(document_id, entity_type, entity_id, *, principal, request_id=None):
    """Assign ONE reviewed document to the admin-chosen candidate, through the existing atomic write path.
    Same record-scope gate as the unassigned-resolution route; never overwrites an existing owner; never
    creates or merges. Returns {ok, reason?, destination?}. Writes the same ownership-resolution audit as
    the per-document Confirm path (document.ownership_resolved from the service + document.single_resolved
    here for parity)."""
    from app.security.authorization import record_in_scope
    from app.services.households import resolve_document_ownership

    if entity_type not in ("person", "household", "organization"):
        return {"ok": False, "reason": "invalid_entity_type"}
    if entity_type == "organization":
        if not principal.can("record.write_all"):
            return {"ok": False, "reason": "not_permitted"}
    elif not record_in_scope(principal, entity_type, entity_id, write=True):
        return {"ok": False, "reason": "out_of_scope"}

    col = {"person": "person_id", "household": "household_id", "organization": "organization_id"}[entity_type]
    result = resolve_document_ownership(document_id, actor_user_id=principal.user_id,
                                        request_id=request_id, **{col: entity_id})
    if not result.get("assigned"):
        return {"ok": False, "reason": result.get("reason", "not_eligible")}
    dest = result.get("destination") or {}
    write_audit_event(action="document.single_resolved", entity_type="document",
                      entity_id=str(document_id), actor_user_id=principal.user_id,
                      request_id=request_id, metadata={"destination": dest, "review_queue": True})
    return {"ok": True, "destination": dest}
