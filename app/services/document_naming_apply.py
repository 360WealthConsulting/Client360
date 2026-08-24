"""Reviewed apply of canonical document display names — SAFE preview rows ONLY.

Writes exactly one column, ``documents.display_name``. Nothing else about a document is touched:
``original_name``, ``stored_name``, ``storage_path``, ``storage_uri``, ``sha256``, ownership,
category, tags and status are all left exactly as they are. **No physical file is renamed or moved**,
and nothing in SharePoint or OneDrive is contacted — the stored file is located by
``storage_path``/``storage_uri``, which this module never reads or changes. A display name is a label
on top of provenance, never a replacement for it.

Gates, all enforced immediately before the write:

* the naming resolver is re-run live, so a stale preview can never authorise a write,
* only ``bucket == "SAFE"`` may be written — REVIEW, UNCHANGED and SKIP never receive an automatic
  display name (UNCHANGED deliberately gets nothing: the read-time fallback to ``original_name``
  already produces the right result),
* ``collision`` must be false,
* the proposed name must be non-empty,
* a row that already carries a DIFFERENT display name is reported as a conflict and left alone —
  a human decision is never overwritten.

Idempotent: re-applying an identical name is a no-op that writes nothing and emits no audit event.
Dry-run is the default.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db import documents, engine
from app.security.audit import write_audit_event
from app.services.document_normalization_preview import build_preview

APPLICABLE_BUCKET = "SAFE"
_REQUIRED_CAPABILITY = "documents.edit"

#: outcome codes for every considered row
APPLIED = "applied"
UNCHANGED_ALREADY_SET = "already_set"
CONFLICT_EXISTING_NAME = "conflict_existing_display_name"
REFUSED_BUCKET = "refused_not_safe"
REFUSED_COLLISION = "refused_collision"
REFUSED_EMPTY = "refused_empty_name"
NOT_IN_PREVIEW = "not_in_preview"


def _rid(request_id):
    """audit_events.request_id is NOT NULL; mint one when the caller has no request context
    (same convention as organization_service)."""
    return request_id or f"docname-{uuid.uuid4()}"


class DocumentNamingApplyError(RuntimeError):
    """The request itself is malformed (bad ids, no selection mode)."""


def _eligible(row):
    """(ok, reason) for one live preview row. Order matters: report the most specific refusal."""
    if row["bucket"] != APPLICABLE_BUCKET:
        return False, REFUSED_BUCKET
    if row.get("collision"):
        return False, REFUSED_COLLISION
    if not (row.get("proposed_display_name") or "").strip():
        return False, REFUSED_EMPTY
    return True, None


def apply_display_names(*, principal, document_ids=None, safe_all=False, dry_run=True,
                        limit=None, request_id=None) -> dict:
    """Apply canonical display names to SAFE documents. Returns a structured result in every case.

    Exactly one selection mode: ``document_ids`` (a bounded, explicit list) or ``safe_all=True``.
    Raises ``PermissionError`` without ``documents.edit``; raises ``DocumentNamingApplyError`` for a
    malformed request. ``dry_run`` defaults to True — a caller must ask for the write.
    """
    if not principal.can(_REQUIRED_CAPABILITY):
        raise PermissionError(f"Missing capability: {_REQUIRED_CAPABILITY}")
    ids = [int(d) for d in (document_ids or [])]
    if bool(ids) == bool(safe_all):
        raise DocumentNamingApplyError(
            "Choose exactly one selection mode: explicit document_ids OR safe_all=True")

    # Re-resolve live. The preview a caller looked at may be minutes or days old.
    report = build_preview(limit=limit)
    by_id = {r["document_id"]: r for r in report["rows"]}
    targets = ids if ids else [r["document_id"] for r in report[  # noqa: SIM108 - explicit is clearer
        "rows"] if r["bucket"] == APPLICABLE_BUCKET]

    considered, counts = [], {k: 0 for k in (
        APPLIED, UNCHANGED_ALREADY_SET, CONFLICT_EXISTING_NAME, REFUSED_BUCKET,
        REFUSED_COLLISION, REFUSED_EMPTY, NOT_IN_PREVIEW)}

    with engine.connect() as conn:
        existing = {r["id"]: r["display_name"] for r in conn.execute(
            select(documents.c.id, documents.c.display_name)
            .where(documents.c.id.in_(targets or [-1]))).mappings()} if targets else {}

    for document_id in targets:
        row = by_id.get(document_id)
        if row is None:
            considered.append({"document_id": document_id, "outcome": NOT_IN_PREVIEW,
                               "proposed_display_name": None, "bucket": None})
            counts[NOT_IN_PREVIEW] += 1
            continue
        ok, reason = _eligible(row)
        proposed = (row.get("proposed_display_name") or "").strip()
        entry = {"document_id": document_id, "bucket": row["bucket"],
                 "proposed_display_name": proposed or None,
                 "current_display_name": existing.get(document_id),
                 "owner": row.get("owner"), "owner_type": row.get("owner_type"),
                 "current_filename": row.get("current_filename"), "outcome": None}
        if not ok:
            entry["outcome"] = reason
            counts[reason] += 1
            considered.append(entry)
            continue
        current = (existing.get(document_id) or "").strip()
        if current == proposed:                       # idempotent: nothing to do, nothing audited
            entry["outcome"] = UNCHANGED_ALREADY_SET
            counts[UNCHANGED_ALREADY_SET] += 1
            considered.append(entry)
            continue
        if current:                                   # a human already named it — never overwrite
            entry["outcome"] = CONFLICT_EXISTING_NAME
            counts[CONFLICT_EXISTING_NAME] += 1
            considered.append(entry)
            continue
        entry["outcome"] = APPLIED
        counts[APPLIED] += 1
        considered.append(entry)
        if not dry_run:
            with engine.begin() as conn:
                conn.execute(documents.update()
                             .where(documents.c.id == document_id,
                                    documents.c.display_name.is_(None))
                             .values(display_name=proposed))
            write_audit_event(action="document.display_name.set", entity_type="document",
                              entity_id=document_id, actor_user_id=principal.user_id,
                              request_id=_rid(request_id),
                              metadata={"display_name": proposed,
                                        "bucket": row["bucket"], "source": "document_naming_preview"})

    return {
        "ok": True, "dry_run": dry_run, "applied": 0 if dry_run else counts[APPLIED],
        "would_apply": counts[APPLIED] if dry_run else 0,
        "considered": len(considered), "counts": counts, "rows": considered,
        "preview_totals": report["counts"],
    }
