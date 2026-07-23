"""Insurance Operations — cases & policies core (Release 0.10.0, Phase 1).

Canonical service for the insurance domain. Enforces record scope (org/person/
household anchored, like benefits), validates against the product catalog, and
publishes every significant lifecycle event into the SHARED Timeline + Audit
infrastructure — there is deliberately no separate insurance history model.

Thin HTTP wrappers live in app/routes/insurance.py; business rules live here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select

from app.db import (
    engagements,
    engine,
    households,
    insurance_cases,
    insurance_coverages,
    insurance_policies,
    insurance_policy_parties,
    insurance_policy_producers,
    insurance_policy_reviews,
    insurance_requirements,
    insurance_riders,
    people,
    relationship_entities,
    service_lines,
    timeline_events,
    users,
)
from app.security.audit import write_audit_event
from app.security.authorization import organization_in_scope, record_in_scope
from app.services import insurance_catalog

# Significant policy status transitions -> (timeline event_type, human title).
# Each is a proportional client-timeline entry; payloads never carry identifiers,
# financials, or party PII (only role/status/carrier reference).
STATUS_EVENTS = {
    "applied": ("insurance_application_submitted", "Application submitted"),
    "underwriting": ("insurance_underwriting_status_changed", "Underwriting status changed"),
    "issued": ("insurance_policy_issued", "Policy issued"),
    "delivered": ("insurance_policy_delivered", "Policy delivered"),
    "in_force": ("insurance_policy_placed_in_force", "Policy placed in force"),
    "reinstated": ("insurance_policy_reinstated", "Policy reinstated"),
    "lapsed": ("insurance_policy_lapsed", "Policy lapsed"),
    "surrendered": ("insurance_policy_surrendered", "Policy surrendered"),
    "replaced": ("insurance_policy_replaced", "Policy replaced"),
    "death_claim": ("insurance_policy_death_claim", "Death claim opened"),
}


class InsuranceError(RuntimeError):
    """Invalid insurance input or state."""


class InsuranceNotFound(InsuranceError):
    """The requested insurance record does not exist."""


def _now():
    return datetime.now(UTC)


# --- authorization -----------------------------------------------------------

def _policy_scope_ok(principal, row, *, write, connection) -> bool:
    """Record-scope check for any org/person/household-anchored insurance row
    (policies and cases; cases have no organization_id, hence .get)."""
    if principal is None:
        return True
    if principal.can("record.write_all") or (not write and principal.can("record.read_all")):
        return True
    org_id = row.get("organization_id")
    if org_id and organization_in_scope(principal, org_id, write=write, connection=connection):
        return True
    for et, eid in (("person", row.get("person_id")), ("household", row.get("household_id"))):
        if eid and record_in_scope(principal, et, eid, write=write, connection=connection):
            return True
    return False


def _require(principal, cap):
    if principal is not None and not principal.can(cap):
        raise PermissionError(f"Missing capability {cap}")


# --- shared timeline + audit (no separate insurance history) -----------------

def _publish(connection, *, action, event_type, title, policy_row=None,
             actor_user_id=None, request_id=None, metadata=None,
             entity_type="insurance_policy"):
    # HTTP callers pass request.state.request_id; system/scheduled callers may not,
    # and audit_events.request_id is NOT NULL, so synthesize one.
    write_audit_event(
        action=action, entity_type=entity_type,
        entity_id=(policy_row or {}).get("id"), actor_user_id=actor_user_id,
        request_id=request_id or f"insurance-{uuid.uuid4()}", metadata=metadata,
    )
    row = policy_row or {}
    if row.get("person_id") or row.get("household_id"):
        connection.execute(timeline_events.insert().values(
            source="insurance", event_type=event_type, title=title,
            person_id=row.get("person_id"), household_id=row.get("household_id"),
            organization_id=row.get("organization_id"),
            external_id=f"insurance-{row.get('id')}-{event_type}-{_now().timestamp()}",
            event_metadata=metadata or {},
        ))


# --- cases -------------------------------------------------------------------

def create_case(principal, *, case_type, household_id=None, person_id=None,
                objective=None, actor_user_id=None, request_id=None):
    """Open an insurance case: creates its 1:1 engagement (AD-2), then the case."""
    _require(principal, "insurance.write")
    if not household_id and not person_id:
        raise InsuranceError("A case needs a household or person.")
    with engine.begin() as c:
        sl = c.execute(select(service_lines.c.id).where(service_lines.c.code == "insurance")).scalar_one()
        eng = c.execute(engagements.insert().values(
            service_line_id=sl, engagement_type=f"insurance_{case_type}", status="open",
            metadata={}, person_id=person_id, household_id=household_id,
        ).returning(engagements.c.id)).scalar_one()
        case_id = c.execute(insurance_cases.insert().values(
            engagement_id=eng, household_id=household_id, person_id=person_id,
            case_type=case_type, status="open", objective=objective,
            created_by_user_id=actor_user_id,
        ).returning(insurance_cases.c.id)).scalar_one()
        _publish(c, action="insurance.case.created", event_type="insurance_case_opened",
                 title="Insurance case opened",
                 policy_row={"id": case_id, "person_id": person_id, "household_id": household_id},
                 actor_user_id=actor_user_id, request_id=request_id,
                 metadata={"case_type": case_type})
        # (D.35) Publish the created business FACT (references only — NO policy numbers / premiums /
        # amounts) in the caller's transaction.
        from app.services.events import publisher
        publisher.publish_safe("insurance.case_created",
                               {"case_id": case_id, "case_type": case_type, "status": "open"},
                               conn=c, producer="insurance.service", subject_ref=f"insurance_case:{case_id}")
    return {"id": case_id, "engagement_id": eng}


# --- policies ----------------------------------------------------------------

def create_policy(principal, *, carrier_id, product_version_id, case_id=None,
                  person_id=None, household_id=None, organization_id=None,
                  policy_number=None, status="proposed", face_amount=None,
                  premium_amount=None, premium_mode=None, actor_user_id=None, request_id=None):
    _require(principal, "insurance.write")
    with engine.begin() as c:
        carrier = c.execute(select(relationship_entities.c.entity_type)
                            .where(relationship_entities.c.id == carrier_id)).scalar_one_or_none()
        if carrier != "insurance_carrier":
            raise InsuranceError("carrier_id must reference an insurance_carrier organization.")
        values = dict(carrier_id=carrier_id, product_version_id=product_version_id, case_id=case_id,
                      person_id=person_id, household_id=household_id, organization_id=organization_id,
                      policy_number=policy_number, status=status, face_amount=face_amount,
                      premium_amount=premium_amount, premium_mode=premium_mode)
        # write scope: the caller must own the subject record they attach the policy to
        if not _policy_scope_ok(principal, values, write=True, connection=c):
            raise PermissionError("Policy subject is outside your record scope.")
        pid = c.execute(insurance_policies.insert().values(**values)
                        .returning(insurance_policies.c.id)).scalar_one()
        _publish(c, action="insurance.policy.created", event_type="insurance_policy_created",
                 title=f"Insurance policy created ({status})",
                 policy_row={"id": pid, "person_id": person_id, "household_id": household_id,
                             "organization_id": organization_id},
                 actor_user_id=actor_user_id, request_id=request_id,
                 metadata={"status": status, "carrier_id": carrier_id})
    return {"id": pid}


def _load_policy(c, policy_id):
    row = c.execute(select(insurance_policies).where(
        insurance_policies.c.id == policy_id)).mappings().one_or_none()
    if row is None:
        raise InsuranceNotFound("Insurance policy not found.")
    return row


def _entity_name(c, entity_type, entity_id):
    """Resolve a display name so the UI shows names, not raw IDs."""
    if not entity_id:
        return None
    if entity_type == "person":
        table, col = people, people.c.full_name
    elif entity_type == "household":
        table, col = households, households.c.name
    elif entity_type == "user":
        table, col = users, users.c.display_name
    else:  # organization (carrier, agency, trust, business)
        table, col = relationship_entities, relationship_entities.c.name
    return c.execute(select(col).where(table.c.id == entity_id)).scalar_one_or_none()


def get_policy(principal, policy_id):
    """Return a policy with its coverages, riders, parties, and producers."""
    _require(principal, "insurance.read")
    with engine.connect() as c:
        policy = _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, policy, write=False, connection=c):
            raise InsuranceNotFound("Insurance policy not found.")  # hide existence
        def children(table):
            return [dict(r) for r in c.execute(select(table).where(
                table.c.policy_id == policy_id)).mappings()]
        parties = children(insurance_policy_parties)
        for p in parties:
            p["party_name"] = _entity_name(c, p["party_entity_type"], p["party_entity_id"])
        producers = children(insurance_policy_producers)
        for p in producers:
            p["producer_name"] = _entity_name(c, p["producer_entity_type"], p["producer_entity_id"])
        # Surface the anchoring client's canonical contact so staff can reach them from the policy.
        client = None
        if policy.get("person_id"):
            client = c.execute(select(
                people.c.id, people.c.full_name, people.c.primary_email, people.c.primary_phone,
            ).where(people.c.id == policy["person_id"])).mappings().first()
        return {
            **dict(policy),
            "carrier_name": _entity_name(c, "organization", policy["carrier_id"]),
            "client_person_id": (client["id"] if client else None),
            "client_name": (client["full_name"] if client else None),
            "client_email": (client["primary_email"] if client else None),
            "client_phone": (client["primary_phone"] if client else None),
            "coverages": children(insurance_coverages),
            "riders": children(insurance_riders),
            "parties": parties,
            "producers": producers,
        }


def list_policies(principal, *, status=None, carrier_id=None, limit=200):
    """The insurance book, filtered to the principal's record scope."""
    _require(principal, "insurance.read")
    query = select(insurance_policies).order_by(insurance_policies.c.id.desc())
    if status:
        query = query.where(insurance_policies.c.status == status)
    if carrier_id:
        query = query.where(insurance_policies.c.carrier_id == carrier_id)
    with engine.connect() as c:
        rows = [r for r in c.execute(query).mappings()
                if _policy_scope_ok(principal, r, write=False, connection=c)]
        out = []
        for r in rows[:limit]:
            d = dict(r)
            d["carrier_name"] = _entity_name(c, "organization", r["carrier_id"])
            out.append(d)
    return out


def business_policies(principal, organization_id, *, limit=100):
    """Read-only (Phase D.12): policies OWNED BY a business (``insurance_policies.organization_id``),
    filtered to the principal's insurance record scope. Requires ``insurance.read``. Bounded.
    Policy PURPOSE (key-person/buy-sell/etc.) is NOT modeled in this domain — the workspace
    surfaces purpose as unconfirmed rather than guessing it."""
    _require(principal, "insurance.read")
    with engine.connect() as c:
        rows = [r for r in c.execute(
            select(insurance_policies)
            .where(insurance_policies.c.organization_id == organization_id)
            .order_by(insurance_policies.c.id.desc())).mappings()
            if _policy_scope_ok(principal, r, write=False, connection=c)]
        out = []
        for r in rows[:limit]:
            d = dict(r)
            d["carrier_name"] = _entity_name(c, "organization", r["carrier_id"])
            out.append(d)
    return out


def update_policy_status(principal, policy_id, new_status, *, actor_user_id=None, request_id=None):
    _require(principal, "insurance.write")
    with engine.begin() as c:
        policy = _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, policy, write=True, connection=c):
            raise PermissionError("Policy is outside your record scope.")
        c.execute(insurance_policies.update().where(insurance_policies.c.id == policy_id)
                  .values(status=new_status, updated_at=_now()))
        event = STATUS_EVENTS.get(new_status)
        if event:
            event_type, title = event
            _publish(c, action="insurance.policy.status_changed", event_type=event_type, title=title,
                     policy_row=policy, actor_user_id=actor_user_id, request_id=request_id,
                     metadata={"from": policy["status"], "to": new_status})
        # (D.35) Publish the policy-issued business FACT (references only — NO policy number / face
        # amount / premium) on a genuine transition to issued.
        if new_status == "issued" and policy["status"] != "issued":
            from app.services.events import publisher
            publisher.publish_safe("insurance.policy_issued",
                                   {"policy_id": policy_id, "status": "issued",
                                    "carrier_id": policy.get("carrier_id")}, conn=c,
                                   producer="insurance.service", subject_ref=f"insurance_policy:{policy_id}")
    return {"id": policy_id, "status": new_status}


# --- policy children ---------------------------------------------------------

def add_coverage(principal, policy_id, *, coverage_type, face_amount=None):
    _require(principal, "insurance.write")
    with engine.begin() as c:
        policy = _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, policy, write=True, connection=c):
            raise PermissionError("Policy is outside your record scope.")
        cid = c.execute(insurance_coverages.insert().values(
            policy_id=policy_id, coverage_type=coverage_type, face_amount=face_amount
        ).returning(insurance_coverages.c.id)).scalar_one()
    return {"id": cid}


def add_rider(principal, policy_id, *, rider_type, description=None, face_amount=None):
    """Attach a rider — rejected if the product version does not allow it."""
    _require(principal, "insurance.write")
    with engine.begin() as c:
        policy = _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, policy, write=True, connection=c):
            raise PermissionError("Policy is outside your record scope.")
        pv = policy["product_version_id"]
        if pv is not None and not insurance_catalog.is_rider_compatible(pv, rider_type, connection=c):
            raise InsuranceError(f"Rider '{rider_type}' is not compatible with this product version.")
        rid = c.execute(insurance_riders.insert().values(
            policy_id=policy_id, rider_type=rider_type, description=description, face_amount=face_amount
        ).returning(insurance_riders.c.id)).scalar_one()
    return {"id": rid}


_PARTY_EVENTS = {
    "owner": ("insurance_ownership_changed", "Ownership changed"),
    "beneficiary": ("insurance_beneficiary_changed", "Beneficiary changed"),
}


def add_party(principal, policy_id, *, party_role, party_entity_type, party_entity_id,
              share_percentage=None, designation=None, is_primary_insured=False,
              actor_user_id=None, request_id=None):
    _require(principal, "insurance.write")
    with engine.begin() as c:
        policy = _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, policy, write=True, connection=c):
            raise PermissionError("Policy is outside your record scope.")
        pid = c.execute(insurance_policy_parties.insert().values(
            policy_id=policy_id, party_role=party_role, party_entity_type=party_entity_type,
            party_entity_id=party_entity_id, share_percentage=share_percentage,
            designation=designation, is_primary_insured=is_primary_insured,
        ).returning(insurance_policy_parties.c.id)).scalar_one()
        # Ownership/beneficiary changes are significant lifecycle events. The payload
        # carries only the role and party TYPE — never the party's identity or share —
        # to stay proportional and avoid PII leakage on the shared timeline.
        event = _PARTY_EVENTS.get(party_role)
        if event:
            event_type, title = event
            _publish(c, action=f"insurance.party.{party_role}_changed",
                     event_type=event_type, title=title, policy_row=policy,
                     actor_user_id=actor_user_id, request_id=request_id,
                     metadata={"role": party_role, "party_entity_type": party_entity_type})
    return {"id": pid}


def add_producer(principal, policy_id, *, producer_entity_type, producer_entity_id,
                 producer_role, split_percentage=None):
    _require(principal, "insurance.write")
    with engine.begin() as c:
        policy = _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, policy, write=True, connection=c):
            raise PermissionError("Policy is outside your record scope.")
        pid = c.execute(insurance_policy_producers.insert().values(
            policy_id=policy_id, producer_entity_type=producer_entity_type,
            producer_entity_id=producer_entity_id, producer_role=producer_role,
            split_percentage=split_percentage,
        ).returning(insurance_policy_producers.c.id)).scalar_one()
    return {"id": pid}


# ============================================================================
# Phase 2 — NON-REGULATED new-business plumbing (behind the AD-5 gate line).
# Nothing below evaluates suitability, replacement/1035, licensing, or CE, makes
# a recommendation, or performs an automated compliance approval. These are
# operational status/checklist tracking only; staff enter the values.
# ============================================================================

CASE_STATUS_EVENTS = {
    "fact_find": ("insurance_case_fact_find", "Fact finding started"),
    "proposed": ("insurance_case_proposed", "Proposal presented"),
    "underwriting": ("insurance_case_underwriting", "Case in underwriting"),
    "issued": ("insurance_case_issued", "Case issued"),
    "declined": ("insurance_case_declined", "Case declined"),
    "closed": ("insurance_case_closed", "Case closed"),
}

_REQUIREMENT_OPEN = ("requested", "received")


def _load_case(c, case_id):
    row = c.execute(select(insurance_cases).where(insurance_cases.c.id == case_id)).mappings().one_or_none()
    if row is None:
        raise InsuranceNotFound("Insurance case not found.")
    return row


def get_case(principal, case_id):
    _require(principal, "insurance.read")
    with engine.connect() as c:
        case = _load_case(c, case_id)
        if not _policy_scope_ok(principal, case, write=False, connection=c):
            raise InsuranceNotFound("Insurance case not found.")
        policies = [dict(r) for r in c.execute(select(insurance_policies).where(
            insurance_policies.c.case_id == case_id)).mappings()]
        requirements = [dict(r) for r in c.execute(select(insurance_requirements).where(
            insurance_requirements.c.case_id == case_id)).mappings()]
        return {**dict(case), "policies": policies, "requirements": requirements}


def list_cases(principal, *, status=None, limit=200):
    _require(principal, "insurance.read")
    query = select(insurance_cases).order_by(insurance_cases.c.id.desc())
    if status:
        query = query.where(insurance_cases.c.status == status)
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(query).mappings()
                if _policy_scope_ok(principal, r, write=False, connection=c)]
    return rows[:limit]


def update_case_status(principal, case_id, new_status, *, actor_user_id=None, request_id=None):
    """Advance a case through its operational pipeline. No regulated logic."""
    _require(principal, "insurance.write")
    with engine.begin() as c:
        case = _load_case(c, case_id)
        if not _policy_scope_ok(principal, case, write=True, connection=c):
            raise PermissionError("Case is outside your record scope.")
        c.execute(insurance_cases.update().where(insurance_cases.c.id == case_id)
                  .values(status=new_status, updated_at=_now()))
        event = CASE_STATUS_EVENTS.get(new_status)
        if event:
            event_type, title = event
            _publish(c, action="insurance.case.status_changed", event_type=event_type, title=title,
                     policy_row=case, actor_user_id=actor_user_id, request_id=request_id,
                     metadata={"from": case["status"], "to": new_status})
        # (D.35) Publish the application-status-changed business FACT (references only) on a genuine change.
        if case["status"] != new_status:
            from app.services.events import publisher
            publisher.publish_safe("insurance.application_status_changed",
                                   {"case_id": case_id, "from_status": case["status"], "to_status": new_status},
                                   conn=c, producer="insurance.service", subject_ref=f"insurance_case:{case_id}")
    return {"id": case_id, "status": new_status}


def set_underwriting_status(principal, policy_id, underwriting_status, *, actor_user_id=None, request_id=None):
    """Record the carrier's underwriting status. Tracking only — the platform does
    not derive or decide underwriting; staff enter what the carrier reports."""
    _require(principal, "insurance.write")
    with engine.begin() as c:
        policy = _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, policy, write=True, connection=c):
            raise PermissionError("Policy is outside your record scope.")
        c.execute(insurance_policies.update().where(insurance_policies.c.id == policy_id)
                  .values(underwriting_status=underwriting_status, updated_at=_now()))
        _publish(c, action="insurance.policy.underwriting_status_changed",
                 event_type="insurance_underwriting_status_changed", title="Underwriting status changed",
                 policy_row=policy, actor_user_id=actor_user_id, request_id=request_id,
                 metadata={"underwriting_status": underwriting_status})
    return {"id": policy_id, "underwriting_status": underwriting_status}


# --- requirements (operational checklist; not a compliance determination) ----

def request_requirement(principal, *, requirement_type, case_id=None, policy_id=None,
                        description=None, due_date=None, document_id=None,
                        actor_user_id=None, request_id=None):
    _require(principal, "insurance.write")
    if not case_id and not policy_id:
        raise InsuranceError("A requirement needs a case or policy.")
    with engine.begin() as c:
        anchor = _load_case(c, case_id) if case_id else _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, anchor, write=True, connection=c):
            raise PermissionError("Requirement anchor is outside your record scope.")
        rid = c.execute(insurance_requirements.insert().values(
            case_id=case_id, policy_id=policy_id, requirement_type=requirement_type,
            status="requested", description=description, due_date=due_date,
            document_id=document_id, requested_by_user_id=actor_user_id, requested_date=_now().date(),
        ).returning(insurance_requirements.c.id)).scalar_one()
        _publish(c, action="insurance.requirement.requested",
                 event_type="insurance_requirement_requested", title="Requirement requested",
                 policy_row=anchor, actor_user_id=actor_user_id, request_id=request_id,
                 metadata={"requirement_type": requirement_type})
    return {"id": rid}


def satisfy_requirement(principal, requirement_id, *, document_id=None, actor_user_id=None, request_id=None):
    _require(principal, "insurance.write")
    with engine.begin() as c:
        req = c.execute(select(insurance_requirements).where(
            insurance_requirements.c.id == requirement_id)).mappings().one_or_none()
        if req is None:
            raise InsuranceNotFound("Requirement not found.")
        anchor = (_load_case(c, req["case_id"]) if req["case_id"]
                  else _load_policy(c, req["policy_id"]))
        if not _policy_scope_ok(principal, anchor, write=True, connection=c):
            raise PermissionError("Requirement is outside your record scope.")
        values = {"status": "satisfied", "satisfied_date": _now().date(), "updated_at": _now()}
        if document_id is not None:
            values["document_id"] = document_id
        c.execute(insurance_requirements.update().where(
            insurance_requirements.c.id == requirement_id).values(**values))
        _publish(c, action="insurance.requirement.satisfied",
                 event_type="insurance_requirement_satisfied", title="Requirement satisfied",
                 policy_row=anchor, actor_user_id=actor_user_id, request_id=request_id,
                 metadata={"requirement_type": req["requirement_type"]})
    return {"id": requirement_id, "status": "satisfied"}


def list_requirements(principal, *, case_id=None, policy_id=None, open_only=False):
    _require(principal, "insurance.read")
    if not case_id and not policy_id:
        raise InsuranceError("list_requirements needs a case_id or policy_id anchor.")
    query = select(insurance_requirements)
    if case_id:
        query = query.where(insurance_requirements.c.case_id == case_id)
    if policy_id:
        query = query.where(insurance_requirements.c.policy_id == policy_id)
    if open_only:
        query = query.where(insurance_requirements.c.status.in_(_REQUIREMENT_OPEN))
    with engine.connect() as c:
        anchor = _load_case(c, case_id) if case_id else _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, anchor, write=False, connection=c):
            raise InsuranceNotFound("Requirement anchor not found.")  # hide existence
        return [dict(r) for r in c.execute(query).mappings()]


# ============================================================================
# Phase 3 — NON-REGULATED in-force servicing: policy reviews as a first-class
# state machine, feeding the obligation calendar. Nothing below determines
# suitability, replacement/1035, licensing, or CE, or makes a compliance
# conclusion. A review records an operational servicing touchpoint; review_type
# is a scheduling category and outcome_note is a free-text servicing summary,
# never a determination. Suitability reviews remain behind the AD-5 gate.
# ============================================================================

REVIEW_EVENTS = {
    "scheduled": ("insurance_review_scheduled", "Policy review scheduled"),
    "in_progress": ("insurance_review_started", "Policy review started"),
    "completed": ("insurance_review_completed", "Policy review completed"),
    "deferred": ("insurance_review_deferred", "Policy review deferred"),
    "overdue": ("insurance_review_overdue", "Policy review overdue"),
    "cancelled": ("insurance_review_cancelled", "Policy review cancelled"),
}
_REVIEW_TYPES = ("annual", "inforce", "servicing")
_REVIEW_OPEN = ("due", "scheduled", "in_progress")


def _load_review(c, review_id):
    row = c.execute(select(insurance_policy_reviews).where(
        insurance_policy_reviews.c.id == review_id)).mappings().one_or_none()
    if row is None:
        raise InsuranceNotFound("Insurance review not found.")
    return row


def _review_anchor(c, review):
    """The policy or case a review hangs off — carries the record-scope anchor
    (person/household/organization) and the timeline subject."""
    if review["policy_id"]:
        return _load_policy(c, review["policy_id"])
    return _load_case(c, review["case_id"])


def _publish_review(c, review, anchor, *, action, status, actor_user_id, request_id, metadata=None):
    event = REVIEW_EVENTS.get(status)
    if not event:
        return
    event_type, title = event
    anchor_row = {"id": review["id"], "person_id": anchor.get("person_id"),
                  "household_id": anchor.get("household_id"),
                  "organization_id": anchor.get("organization_id")}
    _publish(c, action=action, event_type=event_type, title=title, policy_row=anchor_row,
             actor_user_id=actor_user_id, request_id=request_id,
             entity_type="insurance_policy_review", metadata=metadata)


def _review_view(c, review, anchor=None):
    d = dict(review)
    d["reviewer_name"] = _entity_name(c, "user", review["reviewer_user_id"])
    d["subject_type"] = "policy" if review["policy_id"] else "case"
    d["subject_id"] = review["policy_id"] or review["case_id"]
    d["subject_name"] = None
    if anchor:
        d["subject_name"] = (_entity_name(c, "household", anchor.get("household_id"))
                             or _entity_name(c, "person", anchor.get("person_id")))
    return d


def schedule_review(principal, *, review_type, due_date, policy_id=None, case_id=None,
                    scheduled_date=None, reviewer_user_id=None, notes=None,
                    actor_user_id=None, request_id=None):
    """Place a servicing review on the calendar. Operational scheduling only."""
    _require(principal, "insurance.write")
    if not policy_id and not case_id:
        raise InsuranceError("A review needs a policy or case.")
    if review_type not in _REVIEW_TYPES:
        raise InsuranceError(f"Unsupported review_type: {review_type}")
    with engine.begin() as c:
        anchor = _load_policy(c, policy_id) if policy_id else _load_case(c, case_id)
        if not _policy_scope_ok(principal, anchor, write=True, connection=c):
            raise PermissionError("Review subject is outside your record scope.")
        status = "scheduled" if scheduled_date else "due"
        rid = c.execute(insurance_policy_reviews.insert().values(
            policy_id=policy_id, case_id=case_id, review_type=review_type, status=status,
            due_date=due_date, scheduled_date=scheduled_date, reviewer_user_id=reviewer_user_id,
            outcome_note=notes, created_by_user_id=actor_user_id,
        ).returning(insurance_policy_reviews.c.id)).scalar_one()
        review = {"id": rid, "policy_id": policy_id, "case_id": case_id}
        _publish_review(c, review, anchor, action="insurance.review.scheduled",
                        status="scheduled", actor_user_id=actor_user_id, request_id=request_id,
                        metadata={"review_type": review_type})
    return {"id": rid, "status": status}


def update_review_status(principal, review_id, new_status, *, scheduled_date=None,
                         actor_user_id=None, request_id=None):
    """Advance a review through its operational states (schedule / start / defer / cancel)."""
    _require(principal, "insurance.write")
    if new_status not in ("scheduled", "in_progress", "deferred", "cancelled"):
        raise InsuranceError(f"Unsupported review status transition: {new_status}")
    with engine.begin() as c:
        review = _load_review(c, review_id)
        anchor = _review_anchor(c, review)
        if not _policy_scope_ok(principal, anchor, write=True, connection=c):
            raise PermissionError("Review is outside your record scope.")
        values = {"status": new_status, "updated_at": _now()}
        if scheduled_date is not None:
            values["scheduled_date"] = scheduled_date
        c.execute(insurance_policy_reviews.update().where(
            insurance_policy_reviews.c.id == review_id).values(**values))
        _publish_review(c, review, anchor, action="insurance.review.status_changed",
                        status=new_status, actor_user_id=actor_user_id, request_id=request_id,
                        metadata={"from": review["status"], "to": new_status})
    return {"id": review_id, "status": new_status}


def _materialize_next_review(c, review, next_due, actor_user_id):
    """Idempotently open the next annual occurrence (obligation-calendar recurrence)."""
    key = (f"ins:review:{review['policy_id'] or 0}:{review['case_id'] or 0}:"
           f"{review['review_type']}:{next_due.isoformat()}")
    existing = c.execute(select(insurance_policy_reviews.c.id).where(
        insurance_policy_reviews.c.materialization_key == key)).scalar_one_or_none()
    if existing:
        return existing
    return c.execute(insurance_policy_reviews.insert().values(
        policy_id=review["policy_id"], case_id=review["case_id"], review_type=review["review_type"],
        status="due", due_date=next_due, materialization_key=key, created_by_user_id=actor_user_id,
    ).returning(insurance_policy_reviews.c.id)).scalar_one()


def complete_review(principal, review_id, *, completed_date=None, next_review_date=None,
                    outcome_note=None, actor_user_id=None, request_id=None):
    """Mark a servicing review complete. outcome_note is a free-text operational
    summary — NOT a suitability or compliance determination."""
    _require(principal, "insurance.write")
    with engine.begin() as c:
        review = _load_review(c, review_id)
        anchor = _review_anchor(c, review)
        if not _policy_scope_ok(principal, anchor, write=True, connection=c):
            raise PermissionError("Review is outside your record scope.")
        c.execute(insurance_policy_reviews.update().where(
            insurance_policy_reviews.c.id == review_id).values(
            status="completed", completed_date=completed_date or _now().date(),
            next_review_date=next_review_date, outcome_note=outcome_note, updated_at=_now()))
        _publish_review(c, review, anchor, action="insurance.review.completed",
                        status="completed", actor_user_id=actor_user_id, request_id=request_id,
                        metadata={"review_type": review["review_type"]})
        next_id = None
        if next_review_date and review["review_type"] == "annual":
            next_id = _materialize_next_review(c, review, next_review_date, actor_user_id)
    return {"id": review_id, "status": "completed", "next_review_id": next_id}


def get_review(principal, review_id):
    _require(principal, "insurance.read")
    with engine.connect() as c:
        review = _load_review(c, review_id)
        anchor = _review_anchor(c, review)
        if not _policy_scope_ok(principal, anchor, write=False, connection=c):
            raise InsuranceNotFound("Insurance review not found.")  # hide existence
        return _review_view(c, review, anchor)


def list_reviews(principal, *, policy_id=None, case_id=None, status=None, limit=200):
    """Servicing reviews within the principal's record scope (the reviews board)."""
    _require(principal, "insurance.read")
    query = select(insurance_policy_reviews).order_by(insurance_policy_reviews.c.due_date)
    if policy_id:
        query = query.where(insurance_policy_reviews.c.policy_id == policy_id)
    if case_id:
        query = query.where(insurance_policy_reviews.c.case_id == case_id)
    if status:
        query = query.where(insurance_policy_reviews.c.status == status)
    out = []
    with engine.connect() as c:
        for review in c.execute(query).mappings():
            anchor = _review_anchor(c, review)
            if not _policy_scope_ok(principal, anchor, write=False, connection=c):
                continue
            out.append(_review_view(c, review, anchor))
            if len(out) >= limit:
                break
    return out


# Review statuses that represent an OPEN (not yet done) servicing review.
_OPEN_REVIEW_STATUSES = ("due", "scheduled", "in_progress", "overdue")


def reviews_due_for_people(person_ids, *, within_days=45, limit=200, today=None):
    """Open insurance servicing reviews that are due (or within `within_days` of
    due) for a **set** of person ids — the authoritative read behind the Advisor
    Intelligence "insurance review opportunity" (Phase D.5C). It reuses the
    EXISTING review cadence (`insurance_policy_reviews.due_date`/`status`); it does
    NOT compute a new cadence, and it performs NO coverage/replacement/suitability
    analysis. The review's client is resolved from its policy/case anchor
    (`person_id`); `person_ids` scopes the read (`None` = record.read_all, empty =
    `[]`), so it never returns a review for an inaccessible person. Returns id,
    review_type, status, due_date, person_id, household_id, policy_id, case_id."""
    if person_ids is not None and len(person_ids) == 0:
        return []
    today = today or date.today()
    anchor_person = func.coalesce(insurance_policies.c.person_id, insurance_cases.c.person_id)
    anchor_household = func.coalesce(insurance_policies.c.household_id, insurance_cases.c.household_id)
    stmt = (
        select(
            insurance_policy_reviews.c.id, insurance_policy_reviews.c.review_type,
            insurance_policy_reviews.c.status, insurance_policy_reviews.c.due_date,
            insurance_policy_reviews.c.policy_id, insurance_policy_reviews.c.case_id,
            anchor_person.label("person_id"), anchor_household.label("household_id"),
        )
        .select_from(
            insurance_policy_reviews
            .outerjoin(insurance_policies, insurance_policies.c.id == insurance_policy_reviews.c.policy_id)
            .outerjoin(insurance_cases, insurance_cases.c.id == insurance_policy_reviews.c.case_id))
        .where(
            insurance_policy_reviews.c.status.in_(_OPEN_REVIEW_STATUSES),
            insurance_policy_reviews.c.due_date.is_not(None),
            insurance_policy_reviews.c.due_date <= today + timedelta(days=within_days),
            anchor_person.is_not(None),
        )
    )
    if person_ids is not None:
        stmt = stmt.where(anchor_person.in_(tuple(person_ids)))
    stmt = stmt.order_by(insurance_policy_reviews.c.due_date.asc(),
                         insurance_policy_reviews.c.id.asc()).limit(limit)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def client_policy_summary(person_id, household_id=None):
    """Read-only factual summary of a client's insurance policies (person, or the
    household). Counts policies and sums face amount. No in-force status filtering:
    the "in force" status vocabulary is a business decision, so this stays factual
    (Client 360 summary, Phase D.2). Keyed by person/household, so it only ever
    reflects the requested client."""
    conds = [insurance_policies.c.person_id == person_id]
    if household_id:
        conds.append(insurance_policies.c.household_id == household_id)
    with engine.connect() as conn:
        row = conn.execute(
            select(
                func.count().label("n"),
                func.coalesce(func.sum(insurance_policies.c.face_amount), 0).label("face"),
            ).where(or_(*conds))
        ).mappings().first()
    return {"policy_count": row["n"] or 0, "total_face": row["face"] or 0}
