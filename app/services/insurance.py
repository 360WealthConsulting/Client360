"""Insurance Operations — cases & policies core (Release 0.10.0, Phase 1).

Canonical service for the insurance domain. Enforces record scope (org/person/
household anchored, like benefits), validates against the product catalog, and
publishes every significant lifecycle event into the SHARED Timeline + Audit
infrastructure — there is deliberately no separate insurance history model.

Thin HTTP wrappers live in app/routes/insurance.py; business rules live here.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import (
    engagements,
    engine,
    insurance_cases,
    insurance_coverages,
    insurance_policies,
    insurance_policy_parties,
    insurance_policy_producers,
    insurance_riders,
    relationship_entities,
    service_lines,
    timeline_events,
)
from app.security.audit import write_audit_event
from app.security.authorization import organization_in_scope, record_in_scope
from app.services import insurance_catalog

# Significant policy lifecycle transitions that are worth a client-timeline entry.
_TIMELINE_STATUSES = {"applied", "underwriting", "in_force", "lapsed", "surrendered",
                      "replaced", "death_claim"}


class InsuranceError(RuntimeError):
    """Invalid insurance input or state."""


class InsuranceNotFound(InsuranceError):
    """The requested insurance record does not exist."""


def _now():
    return datetime.now(UTC)


# --- authorization -----------------------------------------------------------

def _policy_scope_ok(principal, row, *, write, connection) -> bool:
    if principal is None:
        return True
    if principal.can("record.write_all") or (not write and principal.can("record.read_all")):
        return True
    if row["organization_id"] and organization_in_scope(
            principal, row["organization_id"], write=write, connection=connection):
        return True
    for et, eid in (("person", row["person_id"]), ("household", row["household_id"])):
        if eid and record_in_scope(principal, et, eid, write=write, connection=connection):
            return True
    return False


def _require(principal, cap):
    if principal is not None and not principal.can(cap):
        raise PermissionError(f"Missing capability {cap}")


# --- shared timeline + audit (no separate insurance history) -----------------

def _publish(connection, *, action, event_type, title, policy_row=None,
             actor_user_id=None, request_id=None, metadata=None):
    # HTTP callers pass request.state.request_id; system/scheduled callers may not,
    # and audit_events.request_id is NOT NULL, so synthesize one.
    write_audit_event(
        action=action, entity_type="insurance_policy",
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
        return {
            **dict(policy),
            "coverages": children(insurance_coverages),
            "riders": children(insurance_riders),
            "parties": children(insurance_policy_parties),
            "producers": children(insurance_policy_producers),
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
    return [dict(r) for r in rows[:limit]]


def update_policy_status(principal, policy_id, new_status, *, actor_user_id=None, request_id=None):
    _require(principal, "insurance.write")
    with engine.begin() as c:
        policy = _load_policy(c, policy_id)
        if not _policy_scope_ok(principal, policy, write=True, connection=c):
            raise PermissionError("Policy is outside your record scope.")
        c.execute(insurance_policies.update().where(insurance_policies.c.id == policy_id)
                  .values(status=new_status, updated_at=_now()))
        if new_status in _TIMELINE_STATUSES:
            _publish(c, action="insurance.policy.status_changed",
                     event_type=f"insurance_policy_{new_status}",
                     title=f"Insurance policy {new_status.replace('_', ' ')}",
                     policy_row=policy, actor_user_id=actor_user_id, request_id=request_id,
                     metadata={"from": policy["status"], "to": new_status})
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


def add_party(principal, policy_id, *, party_role, party_entity_type, party_entity_id,
              share_percentage=None, designation=None, is_primary_insured=False):
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
