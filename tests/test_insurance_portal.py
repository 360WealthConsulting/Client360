"""Insurance policyholder portal surface (Release 0.10.0, Phase 7).

Pins that the surface REUSES the existing portal framework (permission-scoped grants, out-of-
scope 404) to give a policyholder a read-only view of their OWN policies, and that it NEVER
exposes producers, commissions/compensation, licensing, or exceptions — client-facing exception
visibility stays out of scope. Also confirms insurance exceptions cannot reach the client
action-needed surface (the shared client_action_items is hard-scoped to domain='tax').
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.db import (
    engine,
    exceptions,
    households,
    insurance_product_families,
    insurance_product_versions,
    people,
    relationship_entities,
    users,
)
from app.portal.service import (
    accept_invitation,
    client_action_needed,
    create_portal_session,
    dashboard,
    invite_portal_account,
    resolve_portal_session,
)
from app.security.models import Principal
from app.services import insurance as ins
from app.services import insurance_commissions as com
from app.services import insurance_detectors as det
from app.services import insurance_portal as ip

INSURANCE_PERM = {"messages": True, "documents": True, "insurance": True}


def _staff():
    return Principal(1, "s@e.com", "S", frozenset({
        "insurance.read", "insurance.write", "insurance.commissions.read",
        "insurance.commissions.write", "record.read_all", "record.write_all",
        "exception.read", "exception.write"}))


def _sfx():
    return uuid.uuid4().hex[:12]


def _seed():
    s = _sfx()
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=f"HH {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hid, full_name=f"P {s}", active=True).returning(people.c.id)).scalar_one()
        uid = c.execute(users.insert().values(
            email=f"staff-{s}@e.com", normalized_email=f"staff-{s}@e.com", display_name="Staff",
            auth_subject=f"staff-{s}", status="active").returning(users.c.id)).scalar_one()
    return hid, pid, uid, s


def _carrier_version():
    s = _sfx()
    with engine.begin() as c:
        carrier = c.execute(relationship_entities.insert().values(
            entity_type="insurance_carrier", name=f"Carrier {s}", details={}, active=True
        ).returning(relationship_entities.c.id)).scalar_one()
        fam = c.execute(insurance_product_families.insert().values(
            carrier_id=carrier, name=f"F {s}", product_type="term_life", line="life"
        ).returning(insurance_product_families.c.id)).scalar_one()
        ver = c.execute(insurance_product_versions.insert().values(
            family_id=fam, version_label="1").returning(insurance_product_versions.c.id)).scalar_one()
    return carrier, ver


def _policyholder(person_id, household_id, user_id, suffix, permissions):
    account_id, invitation = invite_portal_account(
        person_id=person_id, household_id=household_id, email=f"ph-{suffix}@e.com",
        display_name="Policyholder", access_type="self", invited_by_user_id=user_id,
        permissions=permissions)
    accept_invitation(invitation, f"subj-{suffix}", True)
    token = create_portal_session(account_id, device_fingerprint=f"dev-{uuid.uuid4()}", device_name="B")
    return resolve_portal_session(token)


def _policy(person_id, household_id, **kw):
    carrier, ver = _carrier_version()
    return ins.create_policy(_staff(), carrier_id=carrier, product_version_id=ver,
                             person_id=person_id, household_id=household_id, status="in_force",
                             **kw)["id"], carrier


# --- policyholder sees own policy (permission-scoped) -------------------------

def test_policyholder_sees_own_policy_summary_and_detail():
    hid, pid, uid, s = _seed()
    policy_id, _carrier = _policy(pid, hid, policy_number=f"POL-{s}", face_amount=100000, premium_amount=1200)
    ins.add_coverage(_staff(), policy_id, coverage_type="base", face_amount=100000)
    ins.add_party(_staff(), policy_id, party_role="beneficiary", party_entity_type="person",
                  party_entity_id=pid, share_percentage=100)
    principal = _policyholder(pid, hid, uid, s, INSURANCE_PERM)

    policies = ip.portal_policies(principal)
    assert len(policies) == 1
    assert policies[0]["policy_number"] == f"POL-{s}" and policies[0]["status"] == "in_force"
    assert policies[0]["carrier_name"]

    detail = ip.portal_policy_detail(principal, policy_id)
    assert detail["id"] == policy_id
    assert len(detail["coverages"]) == 1 and detail["coverages"][0]["coverage_type"] == "base"
    assert any(p["party_role"] == "beneficiary" for p in detail["parties"])


def test_no_insurance_permission_sees_nothing():
    hid, pid, uid, s = _seed()
    policy_id, _c = _policy(pid, hid, policy_number=f"POL-{s}")
    principal = _policyholder(pid, hid, uid, s, {"messages": True, "documents": True})  # no insurance perm
    assert ip.portal_policies(principal) == []
    assert ip.portal_policy_detail(principal, policy_id) is None  # -> route 404


# --- out-of-scope 404 (never disclose existence) -----------------------------

def test_out_of_scope_policy_is_hidden():
    # policyholder A with an insurance grant
    h_a, p_a, u_a, s_a = _seed()
    principal_a = _policyholder(p_a, h_a, u_a, s_a, INSURANCE_PERM)
    # a policy belonging to a DIFFERENT household/person B
    h_b, p_b, _u_b, s_b = _seed()
    policy_b, _c = _policy(p_b, h_b, policy_number=f"POL-{s_b}")

    assert ip.portal_policy_detail(principal_a, policy_b) is None      # 404, not disclosed
    assert all(p["id"] != policy_b for p in ip.portal_policies(principal_a))


# --- proportional disclosure: no producers / commissions / internals ---------

def test_detail_never_exposes_producers_commissions_or_internals():
    hid, pid, uid, s = _seed()
    policy_id, _c = _policy(pid, hid, policy_number=f"POL-{s}", premium_amount=1000)
    # attach a producer and generate a commission — neither may ever surface to the client
    with engine.begin() as c:
        producer_uid = c.execute(users.insert().values(
            email=f"prod-{s}@e.com", normalized_email=f"prod-{s}@e.com", display_name="Producer",
            auth_subject=f"prod-{s}", status="active").returning(users.c.id)).scalar_one()
    ins.add_producer(_staff(), policy_id, producer_entity_type="user",
                     producer_entity_id=producer_uid, producer_role="writing_agent", split_percentage=100)
    com.generate_expected(_staff(), policy_id=policy_id, basis_amount=500)
    principal = _policyholder(pid, hid, uid, s, INSURANCE_PERM)

    detail = ip.portal_policy_detail(principal, policy_id)
    # the detail is a fixed portal-safe projection — producers/commissions/metadata are absent
    assert set(detail) == {"id", "policy_number", "status", "issue_date", "face_amount",
                           "premium_amount", "premium_mode", "carrier_name",
                           "coverages", "riders", "parties"}
    blob = repr(detail).lower()
    assert "producer" not in blob and "commission" not in blob and "split" not in blob


# --- client-facing exception visibility stays out of scope -------------------

def test_insurance_exceptions_never_reach_the_client_action_surface():
    hid, pid, uid, s = _seed()
    policy_id, _c = _policy(pid, hid, policy_number=f"POL-{s}")
    # raise a person/household-anchored insurance review exception for this policyholder
    ins.schedule_review(_staff(), review_type="annual", due_date=date.today() - timedelta(days=10),
                        policy_id=policy_id)
    det.run_insurance_review_scan(actor_user_id=uid)
    with engine.connect() as c:
        raised = c.execute(select(exceptions.c.id).where(
            exceptions.c.dedupe_key.like("ins:review_overdue:%"),
            exceptions.c.household_id == hid)).scalars().all()
    assert raised, "the insurance review exception was raised (firm-internal)"

    principal = _policyholder(pid, hid, uid, s, INSURANCE_PERM)
    # the client action-needed surface is hard-scoped to domain='tax' — no insurance items appear
    assert client_action_needed(principal) == []


# --- dashboard integration ---------------------------------------------------

def test_dashboard_includes_insurance_policies():
    hid, pid, uid, s = _seed()
    _policy(pid, hid, policy_number=f"POL-{s}")
    principal = _policyholder(pid, hid, uid, s, INSURANCE_PERM)
    data = dashboard(principal)
    assert "insurance_policies" in data
    assert len(data["insurance_policies"]) == 1
