"""Retention, legal holds, deletion reviews & remediation cases (Phase D.23).

Extends the Document Platform retention model to non-document records via **metadata only**. It
**references** ``document_retention_policies`` (never a parallel policy table) and derives expiration
deterministically. It **never issues a hard DELETE and never destroys data automatically**: a
deletion/archival request is a review + approval that requires ``governance.review`` (or
``governance.admin``), is blocked by an active legal hold, and — even when approved — records intent
only (execution launches an existing workflow / references a compliance review). No automatic
destruction.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.database.governance_tables import (
    CASE_STATUSES,
    CASE_TYPES,
    GOV_ENTITY_TYPES,
    REQUEST_TYPES,
)
from app.db import document_retention_policies, engine
from app.db import governance_cases as cases_t
from app.db import governance_deletion_requests as deletions_t
from app.db import governance_legal_holds as holds_t
from app.db import governance_retention_assignments as retention_t

from .common import (
    GovernanceError,
    GovernanceNotFound,
    now,
    publish_timeline,
    record_event,
    require_anchor_write,
    write_audit,
)

# --- retention assignments ---------------------------------------------------

def create_retention_assignment(principal, *, entity_type, entity_id, retention_policy_id=None,
                                classification=None, retention_start_event=None, effective_date=None,
                                person_id=None, household_id=None, actor_user_id=None) -> dict:
    if entity_type not in GOV_ENTITY_TYPES:
        raise GovernanceError(f"invalid entity_type {entity_type!r}")
    require_anchor_write(principal, person_id=person_id, household_id=household_id)
    eff = effective_date or date.today()
    expiration = None
    if retention_policy_id is not None:
        with engine.connect() as c:
            policy = c.execute(select(document_retention_policies)
                               .where(document_retention_policies.c.id == retention_policy_id)).mappings().first()
        if policy is None:
            raise GovernanceError("retention policy not found")
        if policy["retention_years"]:
            expiration = date(eff.year + int(policy["retention_years"]), eff.month, eff.day)
    with engine.begin() as c:
        row = c.execute(retention_t.insert().values(
            entity_type=entity_type, entity_id=entity_id, retention_policy_id=retention_policy_id,
            classification=classification, retention_start_event=retention_start_event,
            effective_date=eff, expiration_date=expiration, status="active",
            archival_eligible=False, deletion_eligible=False, person_id=person_id,
            household_id=household_id, created_by_user_id=actor_user_id).returning(*retention_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="retention", entity_id=row["id"],
                     event_type="retention_assigned", actor_user_id=actor_user_id)
        return row


def list_retention_assignments(*, entity_type=None, status=None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(retention_t).order_by(retention_t.c.id.desc())
        if entity_type:
            stmt = stmt.where(retention_t.c.entity_type == entity_type)
        if status:
            stmt = stmt.where(retention_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def review_due_retention(principal, *, actor_user_id=None) -> dict:
    """Deterministically mark expired assignments and compute eligibility (never destroys)."""
    today = date.today()
    with engine.connect() as c:
        due = list(c.scalars(select(retention_t.c.id).where(
            retention_t.c.status == "active", retention_t.c.expiration_date.is_not(None),
            retention_t.c.expiration_date <= today)))
    reviewed = 0
    for rid in due:
        with engine.begin() as c:
            row = c.execute(select(retention_t).where(retention_t.c.id == rid)).mappings().first()
            if row is None:
                continue
            held = _is_under_legal_hold(c, row["entity_type"], row["entity_id"])
            c.execute(retention_t.update().where(retention_t.c.id == rid).values(
                status=("held" if held else "expired"), archival_eligible=not held,
                deletion_eligible=not held, updated_at=now()))
            record_event(c, entity_type="retention", entity_id=rid, event_type="retention_reviewed",
                         to_status=("held" if held else "expired"), actor_user_id=actor_user_id)
        reviewed += 1
    return {"due": len(due), "reviewed": reviewed}


# --- legal holds -------------------------------------------------------------

def _is_under_legal_hold(c, entity_type, entity_id) -> bool:
    return c.scalar(select(holds_t.c.id).where(
        holds_t.c.entity_type == entity_type, holds_t.c.entity_id == entity_id,
        holds_t.c.status == "active").limit(1)) is not None


def is_under_legal_hold(entity_type, entity_id) -> bool:
    with engine.connect() as c:
        return _is_under_legal_hold(c, entity_type, entity_id)


def list_legal_holds(*, status=None):
    with engine.connect() as c:
        stmt = select(holds_t).order_by(holds_t.c.id.desc())
        if status:
            stmt = stmt.where(holds_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def place_legal_hold(principal, *, code, name, entity_type, entity_id, reason=None, person_id=None,
                     household_id=None, approving_compliance_review_id=None, actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip():
        raise GovernanceError("code and name are required")
    if entity_type not in GOV_ENTITY_TYPES:
        raise GovernanceError(f"invalid entity_type {entity_type!r}")
    with engine.begin() as c:
        if c.scalar(select(holds_t.c.id).where(holds_t.c.code == code)) is not None:
            raise GovernanceError(f"legal hold code {code!r} already exists")
        row = c.execute(holds_t.insert().values(
            code=code, name=name.strip(), entity_type=entity_type, entity_id=entity_id, reason=reason,
            status="active", approving_compliance_review_id=approving_compliance_review_id,
            person_id=person_id, household_id=household_id, placed_by_user_id=actor_user_id,
            placed_at=now(), created_by_user_id=actor_user_id).returning(*holds_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="legal_hold", entity_id=row["id"], event_type="legal_hold_placed",
                     to_status="active", actor_user_id=actor_user_id, payload={"entity": entity_type})
    write_audit("governance.legal_hold_placed", entity_type=entity_type, entity_id=entity_id,
                actor_user_id=actor_user_id, metadata={"hold_code": code})
    publish_timeline(row, "legal_hold_placed", title=f"Legal hold: {name}")
    return row


def release_legal_hold(principal, hold_id: int, *, actor_user_id=None) -> dict:
    with engine.begin() as c:
        h = c.execute(select(holds_t).where(holds_t.c.id == hold_id)).mappings().first()
        if h is None:
            raise GovernanceNotFound(str(hold_id))
        row = c.execute(holds_t.update().where(holds_t.c.id == hold_id).values(
            status="released", released_by_user_id=actor_user_id, released_at=now(),
            updated_at=now()).returning(*holds_t.c)).mappings().one()
        record_event(c, entity_type="legal_hold", entity_id=hold_id, event_type="legal_hold_released",
                     from_status="active", to_status="released", actor_user_id=actor_user_id)
        return dict(row)


# --- deletion / archival reviews (no hard delete, ever) ----------------------

def create_deletion_request(principal, *, entity_type, entity_id, request_type="deletion",
                            reason=None, person_id=None, household_id=None, actor_user_id=None) -> dict:
    if request_type not in REQUEST_TYPES:
        raise GovernanceError(f"invalid request_type {request_type!r}")
    if entity_type not in GOV_ENTITY_TYPES:
        raise GovernanceError(f"invalid entity_type {entity_type!r}")
    require_anchor_write(principal, person_id=person_id, household_id=household_id)
    with engine.begin() as c:
        blocked = _is_under_legal_hold(c, entity_type, entity_id)
        row = c.execute(deletions_t.insert().values(
            request_type=request_type, entity_type=entity_type, entity_id=entity_id, reason=reason,
            status="draft", legal_hold_blocked=blocked, person_id=person_id, household_id=household_id,
            created_by_user_id=actor_user_id).returning(*deletions_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="deletion_request", entity_id=row["id"],
                     event_type="deletion_requested", to_status="draft", actor_user_id=actor_user_id,
                     payload={"request_type": request_type, "blocked": blocked})
        return row


def set_deletion_status(principal, request_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in ("submitted", "under_review", "cancelled"):
        raise GovernanceError(f"invalid transition to {status!r}")
    with engine.begin() as c:
        req = c.execute(select(deletions_t).where(deletions_t.c.id == request_id)).mappings().first()
        if req is None:
            raise GovernanceNotFound(str(request_id))
        row = c.execute(deletions_t.update().where(deletions_t.c.id == request_id)
                        .values(status=status, updated_at=now()).returning(*deletions_t.c)).mappings().one()
        record_event(c, entity_type="deletion_request", entity_id=request_id,
                     event_type=f"deletion_{status}", from_status=req["status"], to_status=status,
                     actor_user_id=actor_user_id)
        return dict(row)


def review_deletion_request(principal, request_id: int, *, decision, compliance_review_id=None,
                            evidence_reference=None, actor_user_id=None) -> dict:
    """Approve or reject a deletion/archival request. **Approval requires governance.review or
    governance.admin**, is refused when the entity is under an active legal hold, and NEVER performs
    a hard delete — it records the approved review only."""
    if decision not in ("approved", "rejected"):
        raise GovernanceError("decision must be 'approved' or 'rejected'")
    if decision == "approved" and not (principal.can("governance.review") or principal.can("governance.admin")):
        raise GovernanceError("deletion approval requires governance.review or governance.admin")
    with engine.begin() as c:
        req = c.execute(select(deletions_t).where(deletions_t.c.id == request_id)).mappings().first()
        if req is None:
            raise GovernanceNotFound(str(request_id))
        req = dict(req)
        if decision == "approved" and _is_under_legal_hold(c, req["entity_type"], req["entity_id"]):
            raise GovernanceError("cannot approve: entity is under an active legal hold")
        ts = now()
        values = {"status": decision, "reviewed_by_user_id": actor_user_id, "reviewed_at": ts,
                  "compliance_review_id": compliance_review_id, "evidence_reference": evidence_reference,
                  "updated_at": ts}
        if decision == "approved":
            values["approved_by_user_id"] = actor_user_id
            values["approved_at"] = ts
        row = c.execute(deletions_t.update().where(deletions_t.c.id == request_id)
                        .values(**values).returning(*deletions_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="deletion_request", entity_id=request_id,
                     event_type=f"deletion_{decision}", from_status=req["status"], to_status=decision,
                     actor_user_id=actor_user_id)
    if decision == "approved":
        write_audit("governance.deletion_approved", entity_type=req["entity_type"],
                    entity_id=req["entity_id"], actor_user_id=actor_user_id,
                    metadata={"request_id": request_id})
        publish_timeline(row, "deletion_approved", title="Deletion approved")
    return row


def execute_deletion(principal, request_id: int, *, actor_user_id=None) -> dict:
    """Record that an APPROVED deletion/archival review was executed. This performs **no hard
    delete** — canonical destruction (if any) is out of scope and would be carried out by a
    dedicated, separately-approved process. Governance records intent + provenance only."""
    with engine.begin() as c:
        req = c.execute(select(deletions_t).where(deletions_t.c.id == request_id)).mappings().first()
        if req is None:
            raise GovernanceNotFound(str(request_id))
        if dict(req)["status"] != "approved":
            raise GovernanceError("only an approved request may be marked executed")
        row = c.execute(deletions_t.update().where(deletions_t.c.id == request_id)
                        .values(status="executed", executed_at=now(), updated_at=now())
                        .returning(*deletions_t.c)).mappings().one()
        record_event(c, entity_type="deletion_request", entity_id=request_id,
                     event_type="deletion_executed", from_status="approved", to_status="executed",
                     actor_user_id=actor_user_id, payload={"note": "metadata only; no hard delete"})
        return dict(row)


def list_deletion_requests(*, status=None, request_type=None):
    with engine.connect() as c:
        stmt = select(deletions_t).order_by(deletions_t.c.id.desc())
        if status:
            stmt = stmt.where(deletions_t.c.status == status)
        if request_type:
            stmt = stmt.where(deletions_t.c.request_type == request_type)
        return [dict(r) for r in c.execute(stmt).mappings()]


# --- remediation cases / exceptions / certifications -------------------------

def create_case(principal, *, code, title, case_type="remediation", finding_id=None, entity_type=None,
                entity_id=None, person_id=None, household_id=None, description=None, expires_at=None,
                actor_user_id=None) -> dict:
    code = (code or "").strip()
    if not code or not (title or "").strip():
        raise GovernanceError("code and title are required")
    if case_type not in CASE_TYPES:
        raise GovernanceError(f"invalid case_type {case_type!r}")
    with engine.begin() as c:
        if c.scalar(select(cases_t.c.id).where(cases_t.c.code == code)) is not None:
            raise GovernanceError(f"case code {code!r} already exists")
        row = c.execute(cases_t.insert().values(
            code=code, title=title.strip(), case_type=case_type, finding_id=finding_id,
            entity_type=entity_type, entity_id=entity_id, status="open", description=description,
            expires_at=expires_at, person_id=person_id, household_id=household_id,
            created_by_user_id=actor_user_id).returning(*cases_t.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="case", entity_id=row["id"], event_type=f"{case_type}_opened",
                     to_status="open", actor_user_id=actor_user_id)
        return row


def set_case_status(principal, case_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in CASE_STATUSES:
        raise GovernanceError(f"invalid status {status!r}")
    with engine.begin() as c:
        case = c.execute(select(cases_t).where(cases_t.c.id == case_id)).mappings().first()
        if case is None:
            raise GovernanceNotFound(str(case_id))
        case = dict(case)
        values = {"status": status, "updated_at": now()}
        if status in ("resolved", "closed"):
            values["resolved_at"] = now()
        row = c.execute(cases_t.update().where(cases_t.c.id == case_id)
                        .values(**values).returning(*cases_t.c)).mappings().one()
        record_event(c, entity_type="case", entity_id=case_id, event_type=f"case_{status}",
                     from_status=case["status"], to_status=status, actor_user_id=actor_user_id)
        row = dict(row)
    if status == "resolved" and case["case_type"] == "remediation":
        publish_timeline(row, "remediation_completed", title=case["title"])
    return row


def list_cases(*, case_type=None, status=None):
    with engine.connect() as c:
        stmt = select(cases_t).order_by(cases_t.c.id.desc())
        if case_type:
            stmt = stmt.where(cases_t.c.case_type == case_type)
        if status:
            stmt = stmt.where(cases_t.c.status == status)
        return [dict(r) for r in c.execute(stmt).mappings()]


def metrics(principal) -> dict:
    with engine.connect() as c:
        holds = c.scalar(select(func.count()).select_from(holds_t)
                         .where(holds_t.c.status == "active")) or 0
        pending = c.scalar(select(func.count()).select_from(deletions_t)
                           .where(deletions_t.c.status.in_(("submitted", "under_review")))) or 0
        open_cases = c.scalar(select(func.count()).select_from(cases_t)
                              .where(cases_t.c.status.in_(("open", "in_progress")))) or 0
    return {"active_legal_holds": holds, "pending_deletion_reviews": pending, "open_cases": open_cases}
