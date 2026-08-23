"""Bounded APPLY for business ownership — SAFE_OWNERSHIP only (Release 0.13.0).

``business_resolution`` derives candidate business↔person ownership from structured evidence and
buckets it. This module is the ONLY writer for those proposals, and it is deliberately narrow:

- one ``business_id`` per call — there is no bulk apply,
- the resolver is re-run **immediately before** the write, so a stale preview (a page rendered
  minutes ago, a queued form post) can never authorize a write that the current evidence no longer
  supports,
- the record must be in bucket ``SAFE_OWNERSHIP`` **and** carry ``apply_eligible is True``,
- ownership attaches to the resolver's EFFECTIVE canonical person id. When the resolver proved a
  stale→canonical reconciliation, the write goes to the canonical person and NEVER to the stale one;
  the stale person row itself is left completely untouched (no merge, no deactivate, no delete),
- the write goes through the sanctioned ``organization_service.record_ownership``, which enforces
  ``organization.write`` + organization-anchored record scope and emits the existing
  ``organization.ownership.recorded`` audit event. This module adds no second write path and no
  second authorization model.

Never written here: ownership percentages (always ``None`` — never inferred), associations
(``SAFE_ASSOCIATION_ONLY``), or anything from ``PERSON_IDENTITY_REVIEW`` /
``DUPLICATE_BUSINESS_REVIEW`` / ``AMBIGUOUS`` / ``UNRESOLVED``. Households, business entities, and
documents are read-only from here.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import (
    engine,
    relationship_entities,
    relationship_types,
    relationships,
)
from app.services.business_resolution import resolve_business_relationships
from app.services.organization_service import record_ownership

#: Stamped into ``relationship_ownership.evidence_source`` — names this resolver AND the Wealthbox
#: company/title evidence it derived the edge from, so provenance is traceable from the workspace.
EVIDENCE_SOURCE = "business_resolution:wealthbox.company_name+job_title"

APPLICABLE_BUCKET = "SAFE_OWNERSHIP"
RELATIONSHIP_CODE = "owns"
_REQUIRED_CAPABILITY = "organization.write"


class BusinessResolutionApplyError(RuntimeError):
    """The requested business cannot be applied under the current resolver evidence."""


def _existing_owner_person_ids(conn, business_id):
    """person_ids already holding an active ``owns`` edge into this business. Pure read — this must
    not call ``ensure_person_entity``, which would create the person's entity mirror as a side
    effect of merely *looking*."""
    owner_entity = relationship_entities.alias("owner_entity")
    rows = conn.execute(
        select(owner_entity.c.person_id)
        .select_from(
            relationships
            .join(relationship_types,
                  relationship_types.c.id == relationships.c.relationship_type_id)
            .join(owner_entity, owner_entity.c.id == relationships.c.from_entity_id))
        .where(relationships.c.to_entity_id == business_id,
               relationships.c.active.is_(True),
               relationship_types.c.code == RELATIONSHIP_CODE)
    ).scalars().all()
    return {p for p in rows if p is not None}


def _owner_view(owner, *, already_applied):
    """The audit-facing view of one proposed owner: what the resolver proved, and which person id
    the write will actually land on."""
    return {
        "canonical_person_id": owner["person_id"],
        "person_id": owner["person_id"],
        "name": owner["name"],
        "job_title": owner["job_title"],
        "company_name": owner["company_name"],
        "role": owner["role"],
        "identity_status": owner["identity_status"],
        "requires_reconciliation": owner["requires_reconciliation"],
        # The stale id is reported for the audit trail ONLY. Ownership is never written to it.
        "stale_person_id": owner["source_person_id"],
        "already_applied": already_applied,
        "action": None,
        "relationship_id": None,
        "ownership_id": None,
    }


def _refusal(business_id, record, reason, *, dry_run):
    return {
        "ok": False,
        "applied": False,
        "dry_run": dry_run,
        "business_id": business_id,
        "business_name": record["business_name"] if record else None,
        "bucket": record["bucket"] if record else None,
        "apply_eligible": bool(record["apply_eligible"]) if record else False,
        "reason": reason,
        "owners_considered": [],
        "canonical_person_ids": [],
        "relationships_created": 0,
        "relationships_reused": 0,
        "evidence_source": EVIDENCE_SOURCE,
    }


def _locate(business_id):
    """Re-resolve and return this business's CURRENT record from whichever bucket holds it."""
    report = resolve_business_relationships(business_ids=[business_id])
    for bucket, records in report.items():
        if bucket == "summary":
            continue
        for record in records:
            if record["business_id"] == business_id:
                return record
    return None


def apply_business_resolution(*, principal, business_id, dry_run=True, request_id=None) -> dict:
    """Apply the SAFE_OWNERSHIP ownership proposals for exactly ONE business.

    Returns a structured result in every non-authorization case (refusals included) so the caller
    can render a clear success/failure without exception handling. Raises ``PermissionError`` when
    the principal lacks ``organization.write`` (up front) or the organization record scope (raised
    from ``record_ownership``); raises ``BusinessResolutionApplyError`` for a malformed request.
    """
    if not principal.can(_REQUIRED_CAPABILITY):
        raise PermissionError(f"Missing capability: {_REQUIRED_CAPABILITY}")
    if not isinstance(business_id, int) or isinstance(business_id, bool) or business_id <= 0:
        raise BusinessResolutionApplyError("A single positive business_id is required")

    # Re-resolve NOW. The caller's page may have been rendered against evidence that has since
    # changed bucket; only the live classification may authorize a write.
    record = _locate(business_id)
    if record is None:
        return _refusal(business_id, None, "business is not a known business entity", dry_run=dry_run)
    if record["bucket"] != APPLICABLE_BUCKET:
        return _refusal(business_id, record,
                        f"bucket is {record['bucket']}, only {APPLICABLE_BUCKET} may be applied",
                        dry_run=dry_run)
    if record["apply_eligible"] is not True:
        return _refusal(business_id, record, "record is not apply_eligible", dry_run=dry_run)

    owners = record["owners"]
    if not owners:
        return _refusal(business_id, record, "no owner proposals to apply", dry_run=dry_run)
    # Defence in depth: the bucket already guarantees this, but never write an edge whose role or
    # identity is anything other than a resolved, explicitly-owning person.
    for owner in owners:
        if (owner["role"] != "EXPLICIT_OWNERSHIP"
                or owner["identity_status"] not in ("ok", "reconcile")
                or not owner["person_id"]):
            return _refusal(business_id, record,
                            "owner proposal failed the explicit-ownership/identity re-check",
                            dry_run=dry_run)

    with engine.connect() as conn:
        already = _existing_owner_person_ids(conn, business_id)

    considered = [_owner_view(o, already_applied=o["person_id"] in already) for o in owners]
    created = reused = 0
    for view in considered:
        pre_existing = view["already_applied"]
        if dry_run:
            view["action"] = "would_reuse" if pre_existing else "would_create"
        else:
            written = record_ownership(
                principal=principal,
                owned_organization_id=business_id,
                owner_person_id=view["canonical_person_id"],
                relationship_code=RELATIONSHIP_CODE,
                ownership_percentage=None,          # never inferred
                voting_percentage=None,
                ownership_type=None,
                is_direct=True,
                evidence_source=EVIDENCE_SOURCE,
                request_id=request_id,
            )
            view["relationship_id"] = written["relationship_id"]
            view["ownership_id"] = written["ownership_id"]
            view["action"] = "reused" if pre_existing else "created"
        if pre_existing:
            reused += 1
        else:
            created += 1

    return {
        "ok": True,
        "applied": not dry_run,
        "dry_run": dry_run,
        "business_id": business_id,
        "business_name": record["business_name"],
        "bucket": record["bucket"],
        "apply_eligible": True,
        "reason": None,
        "owners_considered": considered,
        "canonical_person_ids": [v["canonical_person_id"] for v in considered],
        "relationships_created": created,
        "relationships_reused": reused,
        "evidence_source": EVIDENCE_SOURCE,
    }


def review_overview() -> dict:
    """READ-ONLY review data for the admin surface: post-hardening bucket counts plus every
    SAFE_OWNERSHIP candidate annotated with its current applied/not-applied state. Writes nothing
    and triggers no ``ensure_*`` side effects."""
    report = resolve_business_relationships()
    candidates = []
    with engine.connect() as conn:
        for record in report[APPLICABLE_BUCKET]:
            already = _existing_owner_person_ids(conn, record["business_id"])
            owners = [_owner_view(o, already_applied=o["person_id"] in already)
                      for o in record["owners"]]
            applied_count = sum(1 for o in owners if o["already_applied"])
            candidates.append({
                "business_id": record["business_id"],
                "business_name": record["business_name"],
                "bucket": record["bucket"],
                "apply_eligible": record["apply_eligible"],
                "owners": owners,
                "reconciliations": record["reconciliations"],
                "household_associations": record["household_associations"],
                "notes": record["notes"],
                "workspace_url": f"/business/{record['business_id']}",
                "applied_state": ("applied" if owners and applied_count == len(owners)
                                  else "partial" if applied_count else "not_applied"),
            })
    candidates.sort(key=lambda c: (c["applied_state"] != "not_applied", c["business_name"] or ""))
    return {"summary": report["summary"], "candidates": candidates,
            "evidence_source": EVIDENCE_SOURCE}
