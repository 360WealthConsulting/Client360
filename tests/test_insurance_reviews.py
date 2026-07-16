"""Insurance in-force reviews — NON-REGULATED servicing plumbing (Release 0.10.0, Phase 3).

Pins the review state machine, the obligation-calendar overdue detector (idempotent,
auto-resolving through the SHARED Exception Engine — no second engine), operational
review metrics, record-scope enforcement, and the shared Timeline/Audit events. Also
asserts the compliance-gated behaviors stay absent: no suitability / replacement / 1035 /
licensing / CE determination, recommendation, or approval logic ships in the review path.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.db import (
    engine,
    exception_types,
    exceptions,
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
from app.services import insurance_detectors as det
from app.services import insurance_reporting

FULL = frozenset({"insurance.read", "insurance.write", "record.read_all", "record.write_all",
                  "exception.read", "exception.write"})


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


def _policy(c=None):
    if c is None:
        with engine.begin() as conn:
            return _policy(conn)
    hid, carrier, pv, uid = _setup(c)
    pid = c.execute(insurance_policies.insert().values(
        carrier_id=carrier, product_version_id=pv, household_id=hid, status="in_force"
    ).returning(insurance_policies.c.id)).scalar_one()
    return {"policy_id": pid, "household_id": hid, "uid": uid}


# --- review state machine ----------------------------------------------------

def test_schedule_review_publishes_timeline_event():
    ctx = _policy()
    ins.schedule_review(_p(), review_type="annual", policy_id=ctx["policy_id"],
                        due_date=date(2026, 1, 1), scheduled_date=date(2026, 1, 5), actor_user_id=ctx["uid"])
    with engine.connect() as c:
        events = set(c.execute(select(timeline_events.c.event_type).where(
            timeline_events.c.household_id == ctx["household_id"],
            timeline_events.c.event_type.like("insurance_review_%"))).scalars())
    assert "insurance_review_scheduled" in events


def test_complete_review_records_outcome_and_publishes():
    ctx = _policy()
    r = ins.schedule_review(_p(), review_type="inforce", policy_id=ctx["policy_id"],
                            due_date=date(2026, 1, 1), actor_user_id=ctx["uid"])
    ins.complete_review(_p(), r["id"], outcome_note="Coverage confirmed adequate by client.",
                        actor_user_id=ctx["uid"])
    got = ins.get_review(_p(), r["id"])
    assert got["status"] == "completed" and got["outcome_note"]
    with engine.connect() as c:
        events = set(c.execute(select(timeline_events.c.event_type).where(
            timeline_events.c.household_id == ctx["household_id"],
            timeline_events.c.event_type.like("insurance_review_%"))).scalars())
    assert "insurance_review_completed" in events


def test_complete_annual_review_materializes_next_occurrence_idempotently():
    ctx = _policy()
    r = ins.schedule_review(_p(), review_type="annual", policy_id=ctx["policy_id"],
                            due_date=date(2026, 1, 1), actor_user_id=ctx["uid"])
    out = ins.complete_review(_p(), r["id"], next_review_date=date(2027, 1, 1), actor_user_id=ctx["uid"])
    assert out["next_review_id"] is not None
    # The next occurrence is a fresh 'due' review one year out.
    nxt = ins.get_review(_p(), out["next_review_id"])
    assert nxt["status"] == "due" and str(nxt["due_date"]) == "2027-01-01"


def test_update_review_status_defer_publishes():
    ctx = _policy()
    r = ins.schedule_review(_p(), review_type="servicing", policy_id=ctx["policy_id"],
                            due_date=date(2026, 1, 1), actor_user_id=ctx["uid"])
    ins.update_review_status(_p(), r["id"], "deferred", actor_user_id=ctx["uid"])
    assert ins.get_review(_p(), r["id"])["status"] == "deferred"


# --- obligation calendar: overdue detector -----------------------------------

def test_overdue_scan_flips_review_and_raises_exception():
    ctx = _policy()
    r = ins.schedule_review(_p(), review_type="annual", policy_id=ctx["policy_id"],
                            due_date=date(2020, 1, 1), actor_user_id=ctx["uid"])  # long past due
    result = det.run_insurance_review_scan(actor_user_id=ctx["uid"])
    assert result["reviews_marked_overdue"] >= 1
    assert ins.get_review(_p(), r["id"])["status"] == "overdue"
    with engine.connect() as c:
        code = c.execute(select(exception_types.c.code).select_from(
            exceptions.join(exception_types, exceptions.c.exception_type_id == exception_types.c.id)).where(
            exceptions.c.dedupe_key == f"ins:review_overdue:{r['id']}")).scalar_one_or_none()
    assert code == "INS_REVIEW_OVERDUE"


def test_overdue_scan_is_idempotent():
    ctx = _policy()
    r = ins.schedule_review(_p(), review_type="annual", policy_id=ctx["policy_id"],
                            due_date=date(2020, 1, 1), actor_user_id=ctx["uid"])
    det.run_insurance_review_scan(actor_user_id=ctx["uid"])
    second = det.run_insurance_review_scan(actor_user_id=ctx["uid"])
    # Second run neither re-flips nor double-raises.
    assert second["reviews_marked_overdue"] == 0 and second["exceptions_opened"] == 0
    with engine.connect() as c:
        n = c.execute(select(exceptions.c.id).where(
            exceptions.c.dedupe_key == f"ins:review_overdue:{r['id']}")).scalars().all()
    assert len(n) == 1  # exactly one exception, no duplicate


def test_completing_overdue_review_auto_resolves_exception():
    ctx = _policy()
    r = ins.schedule_review(_p(), review_type="annual", policy_id=ctx["policy_id"],
                            due_date=date(2020, 1, 1), actor_user_id=ctx["uid"])
    det.run_insurance_review_scan(actor_user_id=ctx["uid"])
    ins.complete_review(_p(), r["id"], actor_user_id=ctx["uid"])
    result = det.run_insurance_review_scan(actor_user_id=ctx["uid"])
    assert result["exceptions_resolved"] >= 1
    with engine.connect() as c:
        status = c.execute(select(exceptions.c.status).where(
            exceptions.c.dedupe_key == f"ins:review_overdue:{r['id']}")).scalar_one()
    assert status in ("resolved", "cancelled")


# --- reporting metrics -------------------------------------------------------

def test_review_report_operational_metrics_only():
    ctx = _policy()
    ins.schedule_review(_p(), review_type="annual", policy_id=ctx["policy_id"],
                        due_date=date(2020, 1, 1), actor_user_id=ctx["uid"])
    det.run_insurance_review_scan(actor_user_id=ctx["uid"])
    report = insurance_reporting.review_report(_p())
    assert set(report) == {"total", "by_status", "completed", "overdue", "deferred", "completion_rate"}
    assert report["overdue"] >= 1


# --- record scope ------------------------------------------------------------

def test_reviews_enforce_record_scope():
    ctx = _policy()
    r = ins.schedule_review(_p(), review_type="annual", policy_id=ctx["policy_id"],
                            due_date=date(2026, 1, 1), actor_user_id=ctx["uid"])
    scoped = _p(frozenset({"insurance.read"}))  # no record.read_all, no assignment
    with pytest.raises(ins.InsuranceNotFound):
        ins.get_review(scoped, r["id"])
    assert ins.list_reviews(scoped) == []  # out-of-scope reviews are invisible


# --- the compliance gate: no regulated logic ships in the review path --------

def test_no_regulated_determination_functions_in_review_modules():
    import inspect as _inspect
    regulated = {
        "determine_suitability", "evaluate_suitability", "suitability_score",
        "recommend_replacement", "evaluate_1035", "recommend_1035",
        "validate_license", "validate_licensing", "check_ce", "evaluate_ce",
        "approve_compliance", "compliance_decision", "regulatory_decision",
    }
    verbs = ("determine", "recommend", "certif", "suitab")
    for mod in (det, insurance_reporting):
        defined = {n for n, obj in vars(mod).items()
                   if _inspect.isfunction(obj) and obj.__module__ == mod.__name__}
        assert defined & regulated == set(), f"regulated logic leaked into {mod.__name__}"
        assert not [n for n in defined for v in verbs if v in n], \
            f"a determination verb leaked into {mod.__name__}"


def test_review_scan_result_has_no_compliance_fields():
    result = det.run_insurance_review_scan()
    assert set(result) == {"reviews_marked_overdue", "exceptions_opened", "exceptions_reopened",
                           "exceptions_resolved", "failures", "failure_detail"}


def test_review_type_rejects_regulated_suitability_category():
    ctx = _policy()
    # 'suitability' is not an accepted review_type — the determination stays behind AD-5.
    with pytest.raises(ins.InsuranceError):
        ins.schedule_review(_p(), review_type="suitability", policy_id=ctx["policy_id"],
                            due_date=date(2026, 1, 1), actor_user_id=ctx["uid"])
