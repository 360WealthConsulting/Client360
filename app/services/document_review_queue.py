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

from sqlalchemy import text

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


def _ocr_review_rows(conn, *, limit=None):
    """Return terminal OCR/extraction exceptions for still-unassigned docs.

    These rows are intentionally NOT OCR candidates anymore. They are a
    finite human-review queue:
      - skipped + no_readable_text
      - unsupported + password_required/encrypted
      - failed
      - timed_out
      - historical completed rows with zero extracted characters

    No ownership is changed here.
    """
    sql = """
        SELECT
            d.id AS document_id,
            d.original_name AS filename,
            COALESCE(ds.source_path, d.storage_uri) AS source_path,
            d.ocr_status,
            o.status AS detail_status,
            GREATEST(COALESCE(o.char_count, 0), COALESCE(LENGTH(o.text), 0)) AS char_count,
            o.engine,
            o.last_error
        FROM documents d
        LEFT JOIN document_ocr o
          ON o.document_id = d.id
        LEFT JOIN LATERAL (
            SELECT source_path
            FROM document_sources x
            WHERE x.document_id = d.id
            ORDER BY x.id
            LIMIT 1
        ) ds ON TRUE
        WHERE d.deleted_at IS NULL
          AND d.person_id IS NULL
          AND d.household_id IS NULL
          AND d.organization_id IS NULL
          AND (
                d.ocr_status IN ('failed', 'timed_out', 'unsupported', 'skipped')
                OR (
                    d.ocr_status = 'completed'
                    AND GREATEST(COALESCE(o.char_count, 0), COALESCE(LENGTH(o.text), 0)) = 0
                )
          )
        ORDER BY d.id
    """

    params = {}

    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = int(limit)

    rows = []

    for r in conn.execute(text(sql), params).mappings():
        last_error = (r["last_error"] or "").strip()
        status = (r["ocr_status"] or r["detail_status"] or "").strip().lower()
        chars = int(r["char_count"] or 0)

        if "password_required" in last_error or "encrypted" in last_error.lower():
            reason = "password_required"
            explanation = "Encrypted/password-protected document requires staff review."
        elif status == "timed_out":
            reason = "ocr_timed_out"
            explanation = "OCR timed out; document was moved to review instead of retried automatically."
        elif status == "failed":
            reason = "ocr_failed"
            explanation = "OCR failed; document was moved to review instead of retried automatically."
        elif status == "skipped" or chars == 0:
            reason = "no_readable_text"
            explanation = "OCR completed but produced no readable text."
        else:
            reason = "ocr_review"
            explanation = "Document requires OCR/extraction review."

        evidence = [explanation]

        if last_error:
            evidence.append(last_error[:500])

        rows.append({
            "document_id": int(r["document_id"]),
            "filename": r["filename"],
            "source_path": r["source_path"],
            "extraction_class": "ocr",
            "extraction_method": r["engine"] or status or "unknown",
            "confidence": "REVIEW",
            "evidence_classes": [reason],
            "evidence": evidence,
            "contradictions": [],
            "candidates": [],
            "review_reason": reason,
        })

    return rows


def review_queue(*, limit=None, include_high_review=True, ocr=False):
    """READ-ONLY. Returns proposal-review buckets plus terminal OCR-review rows. Each row has
    document_id, filename, source_path, extraction method/class, confidence, evidence, and `candidates`
    (a list of {type,id,name}). MEDIUM: one prominent proposed candidate. AMBIGUOUS: all defensible
    candidates, no default. high_review: HIGH proposals a contradiction sent to review. Writes nothing."""
    medium, ambiguous, high_review = [], [], []
    with engine.connect() as conn:
        ocr_review = _ocr_review_rows(conn, limit=limit)
        ocr_review_ids = {
            int(row["document_id"])
            for row in ocr_review
        }

        ids = _unassigned_ids(conn, limit=limit)
        idx = build_match_indexes(conn)
        for did in ids:
            # Terminal OCR exceptions have already been swept into the
            # explicit review bucket. Do not re-run owner extraction or
            # OCR analysis for them during every review-page refresh.
            if int(did) in ocr_review_ids:
                continue

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
    return {
        "medium": medium,
        "ambiguous": ambiguous,
        "high_review": high_review,
        "ocr_review": ocr_review,
        "medium_count": len(medium),
        "ambiguous_count": len(ambiguous),
        "high_review_count": len(high_review),
        "ocr_review_count": len(ocr_review),
    }


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
