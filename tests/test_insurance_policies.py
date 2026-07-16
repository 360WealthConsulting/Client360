"""Insurance policies core — service, record scope, and shared Timeline/Audit
publication (Release 0.10.0, Phase 1).

Pins policy/case CRUD, the carrier-as-org validation, record-scope enforcement
(org/person/household), rider-compatibility rejection, and that significant
lifecycle events publish into the SHARED timeline_events — with no separate
insurance history table.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, select, text

from app.db import (
    engine,
    households,
    insurance_cases,
    insurance_product_families,
    insurance_product_versions,
    people,
    relationship_entities,
    timeline_events,
    users,
)
from app.db import (
    insurance_product_rider_compatibility as rider_compat,
)
from app.security.models import Principal
from app.services import insurance as ins

FULL = frozenset({"insurance.read", "insurance.write", "record.read_all", "record.write_all"})


def _p(caps=FULL):
    return Principal(1, "a@e.com", "A", caps)


def _sfx():
    return uuid.uuid4().hex


def _carrier(c, name=None):
    return c.execute(relationship_entities.insert().values(
        entity_type="insurance_carrier", name=name or f"Carrier {_sfx()}", details={}, active=True
    ).returning(relationship_entities.c.id)).scalar_one()


def _version(c, carrier_id):
    fam = c.execute(insurance_product_families.insert().values(
        carrier_id=carrier_id, name=f"F {_sfx()}", product_type="term_life", line="life"
    ).returning(insurance_product_families.c.id)).scalar_one()
    return c.execute(insurance_product_versions.insert().values(
        family_id=fam, version_label="1"
    ).returning(insurance_product_versions.c.id)).scalar_one()


def _household(c):
    return c.execute(households.insert().values(name=f"HH {_sfx()}").returning(households.c.id)).scalar_one()


def _person(c, hid):
    return c.execute(people.insert().values(household_id=hid, full_name=f"P {_sfx()}").returning(people.c.id)).scalar_one()


def _user(c):
    sfx = _sfx()
    return c.execute(users.insert().values(
        email=f"u-{sfx}@e.com", normalized_email=f"u-{sfx}@e.com", display_name="U",
        auth_subject=f"sub-{sfx}", status="active"
    ).returning(users.c.id)).scalar_one()


# --- cases -------------------------------------------------------------------

def test_create_case_makes_a_1to1_engagement_and_timeline_event():
    with engine.begin() as c:
        hid = _household(c)
        pid = _person(c, hid)
        uid = _user(c)
    result = ins.create_case(_p(), case_type="new_business", household_id=hid, person_id=pid,
                             actor_user_id=uid)
    with engine.connect() as c:
        case = c.execute(select(insurance_cases).where(insurance_cases.c.id == result["id"])).mappings().one()
        # the engagement was created and is uniquely this case's
        assert case["engagement_id"] == result["engagement_id"]
        tl = c.execute(select(timeline_events).where(
            timeline_events.c.source == "insurance",
            timeline_events.c.event_type == "insurance_case_opened",
            timeline_events.c.household_id == hid)).mappings().all()
    assert len(tl) == 1


# --- policies ----------------------------------------------------------------

def test_create_policy_rejects_a_non_carrier():
    with engine.begin() as c:
        biz = c.execute(relationship_entities.insert().values(
            entity_type="business", name=f"Biz {_sfx()}", details={}, active=True
        ).returning(relationship_entities.c.id)).scalar_one()
        pv = _version(c, _carrier(c))
    with pytest.raises(ins.InsuranceError, match="insurance_carrier"):
        ins.create_policy(_p(), carrier_id=biz, product_version_id=pv, status="proposed")


def test_create_policy_and_get_with_children():
    with engine.begin() as c:
        hid = _household(c)
        carrier = _carrier(c)
        pv = _version(c, carrier)
        uid = _user(c)
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=pv,
                               household_id=hid, status="proposed", face_amount=500000, actor_user_id=uid)
    ins.add_producer(_p(), policy["id"], producer_entity_type="user", producer_entity_id=1,
                     producer_role="writing_agent", split_percentage=100)
    got = ins.get_policy(_p(), policy["id"])
    assert got["status"] == "proposed" and got["face_amount"] is not None
    assert len(got["producers"]) == 1 and "parties" in got and "riders" in got


def test_record_scope_hides_out_of_scope_policy():
    with engine.begin() as c:
        hid = _household(c)
        carrier = _carrier(c)
        pv = _version(c, carrier)
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=pv, household_id=hid)
    scoped = _p(frozenset({"insurance.read", "insurance.write"}))  # no record.read_all, no assignment
    # write is refused, and get hides existence as a 404-equivalent NotFound
    with pytest.raises(PermissionError):
        ins.create_policy(scoped, carrier_id=carrier, product_version_id=pv, household_id=hid)
    with pytest.raises(ins.InsuranceNotFound):
        ins.get_policy(scoped, policy["id"])


def test_missing_capability_is_rejected():
    reader = _p(frozenset({"insurance.read", "record.read_all"}))
    with pytest.raises(PermissionError, match="capability"):
        ins.create_policy(reader, carrier_id=1, product_version_id=1)  # needs insurance.write


# --- shared timeline on lifecycle transition, no separate history ------------

def test_status_change_to_in_force_publishes_a_timeline_event():
    with engine.begin() as c:
        hid = _household(c)
        carrier = _carrier(c)
        pv = _version(c, carrier)
        uid = _user(c)
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=pv, household_id=hid)
    ins.update_policy_status(_p(), policy["id"], "in_force", actor_user_id=uid)
    with engine.connect() as c:
        tl = c.execute(select(timeline_events).where(
            timeline_events.c.source == "insurance",
            timeline_events.c.event_type == "insurance_policy_in_force",
            timeline_events.c.household_id == hid)).mappings().all()
    assert len(tl) == 1


def test_no_separate_insurance_history_table_exists():
    tables = inspect(engine).get_table_names()
    assert not [t for t in tables if t.startswith("insurance_") and
                ("history" in t or t.endswith("_events") or t.endswith("_audit") or "timeline" in t)]


# --- rider compatibility on attach -------------------------------------------

def test_add_rider_rejects_incompatible_and_accepts_compatible():
    with engine.begin() as c:
        carrier = _carrier(c)
        pv = _version(c, carrier)
        c.execute(rider_compat.insert().values(
            product_version_id=pv, rider_type="waiver_of_premium", requirement="available"))
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=pv, household_id=None,
                               person_id=None)
    ins.add_rider(_p(), policy["id"], rider_type="waiver_of_premium")  # compatible
    with pytest.raises(ins.InsuranceError, match="not compatible"):
        ins.add_rider(_p(), policy["id"], rider_type="long_term_care")  # absent -> incompatible


def test_audit_event_written_for_policy_creation():
    with engine.begin() as c:
        carrier = _carrier(c)
        pv = _version(c, carrier)
        uid = _user(c)
    with engine.connect() as c:
        before = c.execute(text("select count(*) from audit_events where action='insurance.policy.created'")).scalar_one()
    ins.create_policy(_p(), carrier_id=carrier, product_version_id=pv, actor_user_id=uid)
    with engine.connect() as c:
        after = c.execute(text("select count(*) from audit_events where action='insurance.policy.created'")).scalar_one()
    assert after == before + 1
