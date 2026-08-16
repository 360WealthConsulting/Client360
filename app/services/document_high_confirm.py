"""Phase 2 — guarded bulk confirm of validated HIGH owner proposals.

Two operations, both reusing the Phase-1 single-source-of-truth `evaluate_high` and the EXISTING
per-document write path (`households.resolve_document_ownership`):

* preview_high_confirm() — READ-ONLY live re-evaluation of the currently-clean HIGH set (what the admin
  sees before confirming). It trusts NO cached CSV: every document is re-checked at preview time.
* confirm_documents() — for a caller-selected list of document ids, re-evaluates EACH one again and, only
  for those still cleanly HIGH, assigns ownership through resolve_document_ownership. That service performs
  the atomic all-NULL-AND-not-reject recheck in the write statement (stale/double-confirm safe), writes the
  same `document.ownership_resolved` audit the per-document Confirm path writes, and never overwrites an
  existing owner. This module creates no clients, merges no records, and moves/deletes no files. It writes
  ownership ONLY, one document at a time, and reports assigned / skipped / failed.
"""
from __future__ import annotations

from app.db import engine
from app.services.document_high_validation import (
    _unassigned_ids,
    build_match_indexes,
    build_report_row,
    evaluate_high,
)

_OWNER_KWARGS = {"person": "person_id", "household": "household_id", "organization": "organization_id"}


def preview_high_confirm(*, limit=None, ocr=False):
    """READ-ONLY. Live-re-evaluates every unassigned document and splits the HIGH set into `eligible`
    (clean, selectable for bulk confirm) and `review` (HIGH but a contradiction fired — NOT selectable).
    Returns {eligible:[row], review:[row], eligible_count, review_count}. Writes nothing."""
    eligible, review = [], []
    with engine.connect() as conn:
        ids = _unassigned_ids(conn, limit=limit)
        idx = build_match_indexes(conn)
        for did in ids:
            ev = evaluate_high(conn, did, idx, ocr=ocr)
            if ev["status"] == "eligible":
                eligible.append(build_report_row(conn, did, ev["proposal"], [], idx))
            elif ev["status"] == "excluded":
                review.append(build_report_row(conn, did, ev["proposal"], ev["contradictions"], idx))
    return {"eligible": eligible, "review": review,
            "eligible_count": len(eligible), "review_count": len(review)}


def _reason(ev):
    if ev["status"] == "ineligible":
        return ev.get("reason") or "not_eligible"
    if ev["status"] == "not_high":
        return f"no_longer_high:{ev.get('reason')}"
    if ev["status"] == "excluded":
        return "contradiction:" + ",".join(ev.get("contradictions") or [])
    return ev["status"]


def confirm_documents(document_ids, *, actor_user_id=None, request_id=None):
    """Assign ownership for the selected documents that are STILL cleanly HIGH. Each document is
    re-evaluated immediately before its atomic write; ineligible/stale/contradiction/owned documents are
    skipped, not forced. Never overwrites ownership, never creates a client, never moves a file. Returns
    {assigned, skipped, failed, *_count}. `assigned` entries carry document_id + owner (from the live
    re-evaluation; the client-submitted owner is never trusted)."""
    from app.services.households import resolve_document_ownership

    assigned, skipped, failed = [], [], []
    seen = set()
    with engine.connect() as conn:
        idx = build_match_indexes(conn)
        for did in document_ids:
            if did in seen:                                   # de-dupe repeated selections
                continue
            seen.add(did)
            try:
                ev = evaluate_high(conn, did, idx)            # RE-CHECK: eligible + HIGH + contradiction + owner
                if ev["status"] != "eligible":
                    skipped.append({"document_id": did, "reason": _reason(ev)})
                    continue
                p = ev["proposal"]
                etype = p.get("proposed_entity_type")
                eid = p.get("proposed_entity_id")
                col = _OWNER_KWARGS.get(etype)
                if col is None:
                    skipped.append({"document_id": did, "reason": f"unknown_owner_type:{etype}"})
                    continue
                # Atomic, self-rechecking write (all-NULL AND not-reject in the WHERE), audited by the
                # service exactly like the per-document Confirm path. Never overwrites an existing owner.
                result = resolve_document_ownership(did, actor_user_id=actor_user_id,
                                                    request_id=request_id, **{col: eid})
                if result.get("assigned"):
                    dest = result.get("destination") or {}
                    assigned.append({"document_id": did, "entity_type": dest.get("entity_type") or etype,
                                     "entity_id": eid,
                                     "entity_name": dest.get("entity_name") or p.get("proposed_entity_name")})
                else:
                    skipped.append({"document_id": did, "reason": result.get("reason", "not_eligible")})
            except Exception as exc:                          # noqa: BLE001 — one bad doc never aborts the rest
                failed.append({"document_id": did, "error": str(exc)[:200]})
    return {"assigned": assigned, "skipped": skipped, "failed": failed,
            "assigned_count": len(assigned), "skipped_count": len(skipped), "failed_count": len(failed)}
