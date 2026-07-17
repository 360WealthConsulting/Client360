"""Insurance new-business pipeline — NON-REGULATED plumbing (Release 0.10.0, Phase 2).

Pins case progression, requirement tracking, underwriting-status tracking,
operational reporting, and their shared Timeline/Audit events. Also asserts the
compliance-gated behaviors are absent: no suitability / replacement / 1035 /
licensing / CE determination, recommendation, or approval logic ships here.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import (
    engine,
    households,
    insurance_policies,
    insurance_product_families,
    insurance_product_versions,
    relationship_entities,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services import insurance as ins
from app.services import insurance_reporting

FULL = frozenset({"insurance.read", "insurance.write", "record.read_all", "record.write_all"})


def _p(caps=FULL):
    return Principal(1, "a@e.com", "A", caps)


def _sfx():
    return uuid.uuid4().hex


def _setup(c):
    hid = c.execute(households.insert().values(name=f"HH {_sfx()}").returning(households.c.id)).scalar_one()
    carrier = c.execute(relationship_entities.insert().values(
        entity_type="insurance_carrier", name=f"C {_sfx()}", details={}, active=True
    ).returning(relationship_entities.c.id)).scalar_one()
    fam = c.execute(insurance_product_families.insert().values(
        carrier_id=carrier, name=f"F {_sfx()}", product_type="term_life", line="life"
    ).returning(insurance_product_families.c.id)).scalar_one()
    pv = c.execute(insurance_product_versions.insert().values(
        family_id=fam, version_label="1").returning(insurance_product_versions.c.id)).scalar_one()
    uid = c.execute(users.insert().values(
        email=f"u-{_sfx()}@e.com", normalized_email=f"u-{_sfx()}@e.com", display_name="U",
        auth_subject=f"s-{_sfx()}", status="active").returning(users.c.id)).scalar_one()
    return hid, carrier, pv, uid


# --- case progression --------------------------------------------------------

def test_case_status_progression_publishes_timeline_events():
    with engine.begin() as c:
        hid, carrier, pv, uid = _setup(c)
    case = ins.create_case(_p(), case_type="new_business", household_id=hid, actor_user_id=uid)
    for st in ("fact_find", "proposed", "underwriting", "issued"):
        ins.update_case_status(_p(), case["id"], st, actor_user_id=uid)
    with engine.connect() as c:
        events = set(c.execute(select(timeline_events.c.event_type).where(
            timeline_events.c.source == "insurance",
            timeline_events.c.household_id == hid,
            timeline_events.c.event_type.like("insurance_case_%"))).scalars())
    assert {"insurance_case_opened", "insurance_case_fact_find", "insurance_case_proposed",
            "insurance_case_underwriting", "insurance_case_issued"} <= events


def test_get_case_returns_policies_and_requirements():
    with engine.begin() as c:
        hid, carrier, pv, uid = _setup(c)
    case = ins.create_case(_p(), case_type="new_business", household_id=hid, actor_user_id=uid)
    ins.create_policy(_p(), carrier_id=carrier, product_version_id=pv, case_id=case["id"], household_id=hid)
    ins.request_requirement(_p(), requirement_type="aps", case_id=case["id"], actor_user_id=uid)
    got = ins.get_case(_p(), case["id"])
    assert len(got["policies"]) == 1 and len(got["requirements"]) == 1


# --- underwriting status (tracking, not a decision) --------------------------

def test_underwriting_status_is_tracked_and_published():
    with engine.begin() as c:
        hid, carrier, pv, uid = _setup(c)
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=pv, household_id=hid)
    ins.set_underwriting_status(_p(), policy["id"], "pending_requirements", actor_user_id=uid)
    with engine.connect() as c:
        val = c.execute(select(insurance_policies.c.underwriting_status).where(
            insurance_policies.c.id == policy["id"])).scalar_one()
        ev = c.execute(select(timeline_events).where(
            timeline_events.c.event_type == "insurance_underwriting_status_changed",
            timeline_events.c.household_id == hid)).mappings().all()
    assert val == "pending_requirements" and len(ev) == 1


# --- requirements ------------------------------------------------------------

def test_requirement_request_and_satisfy_publish_events():
    with engine.begin() as c:
        hid, carrier, pv, uid = _setup(c)
    case = ins.create_case(_p(), case_type="new_business", household_id=hid, actor_user_id=uid)
    req = ins.request_requirement(_p(), requirement_type="medical_exam", case_id=case["id"], actor_user_id=uid)
    ins.satisfy_requirement(_p(), req["id"], actor_user_id=uid)
    with engine.connect() as c:
        events = set(c.execute(select(timeline_events.c.event_type).where(
            timeline_events.c.household_id == hid,
            timeline_events.c.event_type.like("insurance_requirement_%"))).scalars())
    assert events == {"insurance_requirement_requested", "insurance_requirement_satisfied"}
    open_reqs = ins.list_requirements(_p(), case_id=case["id"], open_only=True)
    assert open_reqs == []  # satisfied is no longer open


def test_requirement_list_enforces_scope():
    with engine.begin() as c:
        hid, carrier, pv, uid = _setup(c)
    case = ins.create_case(_p(), case_type="new_business", household_id=hid, actor_user_id=uid)
    ins.request_requirement(_p(), requirement_type="aps", case_id=case["id"], actor_user_id=uid)
    scoped = _p(frozenset({"insurance.read"}))  # no record.read_all, no assignment
    with pytest.raises(ins.InsuranceNotFound):
        ins.list_requirements(scoped, case_id=case["id"])


# --- operational reporting ---------------------------------------------------

def test_pipeline_report_counts_within_scope():
    with engine.begin() as c:
        hid, carrier, pv, uid = _setup(c)
    case = ins.create_case(_p(), case_type="new_business", household_id=hid, actor_user_id=uid)
    ins.create_policy(_p(), carrier_id=carrier, product_version_id=pv, case_id=case["id"], household_id=hid)
    ins.request_requirement(_p(), requirement_type="aps", case_id=case["id"], actor_user_id=uid)
    report = insurance_reporting.pipeline_report(_p())
    assert report["case_count"] >= 1 and report["policy_count"] >= 1
    assert report["open_requirements"] >= 1
    assert "cases_by_status" in report and "policies_by_status" in report


# --- the compliance gate: no regulated logic ships in Phase 2 ----------------

def test_no_regulated_determination_functions_exist_in_the_service():
    # The gated behaviors: any function that determines, scores, recommends,
    # validates, or approves a regulated concept. None ship in Phase 2.
    import inspect as _inspect
    defined = {n for n, obj in vars(ins).items()
               if _inspect.isfunction(obj) and obj.__module__ == ins.__name__}
    regulated = {
        "determine_suitability", "evaluate_suitability", "suitability_score",
        "recommend_replacement", "evaluate_1035", "recommend_1035",
        "validate_license", "validate_licensing", "check_ce", "evaluate_ce",
        "approve_compliance", "compliance_decision", "regulatory_decision",
    }
    leaked = defined & regulated
    assert leaked == set(), f"regulated logic leaked into Phase 2: {leaked}"
    # and no function name announces a determination/recommendation verb
    verbs = ("determine", "recommend", "certif")
    assert not [n for n in defined for v in verbs if v in n], "a determination/recommendation verb leaked"


def test_reporting_has_no_compliance_metrics():
    # operational pipeline only — no suitability/replacement/licensing/CE rates
    report_keys = {"case_count", "cases_by_status", "policy_count", "policies_by_status", "open_requirements"}
    with engine.begin() as c:
        _setup(c)
    report = insurance_reporting.pipeline_report(_p())
    assert set(report) == report_keys
