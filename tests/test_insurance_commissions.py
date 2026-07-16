"""Insurance commissions — NON-REGULATED expected/received ledger + reconciliation (Phase 5).

Pins the split-aware expected ledger (each producer credited by split, overrides included),
received posting + status, carrier-statement import and reconciliation (where variance
surfaces), the operational variance/outstanding detectors (idempotent, auto-resolving through
the SHARED Exception Engine — no second engine), the revenue rollup, record scope, and
capability gating. Also asserts NO regulated determination (suitability / replacement / 1035 /
licensing / CE / compliance) ships in the commission surface — that stays behind the AD-5 gate.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
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
from app.security.models import Principal
from app.services import insurance as ins
from app.services import insurance_commissions as com
from app.services import insurance_detectors as det
from app.services import insurance_reporting

FULL = frozenset({"insurance.read", "insurance.write", "insurance.commissions.read",
                  "insurance.commissions.write", "record.read_all", "record.write_all",
                  "exception.read", "exception.write"})


def _p(caps=FULL, uid=1):
    return Principal(uid, "a@e.com", "A", caps)


def _sfx():
    return uuid.uuid4().hex


TODAY = date(2026, 7, 15)


def _carrier(c):
    return c.execute(relationship_entities.insert().values(
        entity_type="insurance_carrier", name=f"Carrier {_sfx()}", details={}, active=True
    ).returning(relationship_entities.c.id)).scalar_one()


def _version(c, carrier_id):
    fam = c.execute(insurance_product_families.insert().values(
        carrier_id=carrier_id, name=f"F {_sfx()}", product_type="term_life", line="life"
    ).returning(insurance_product_families.c.id)).scalar_one()
    return c.execute(insurance_product_versions.insert().values(
        family_id=fam, version_label="1").returning(insurance_product_versions.c.id)).scalar_one()


def _user(c):
    sfx = _sfx()
    return c.execute(users.insert().values(
        email=f"u-{sfx}@e.com", normalized_email=f"u-{sfx}@e.com", display_name=f"Prod {sfx[:6]}",
        auth_subject=f"sub-{sfx}", status="active").returning(users.c.id)).scalar_one()


def _policy_with_producers(splits, *, policy_number=None):
    """Create a policy anchored to a household/person and attach producers with the given
    (role, split) pairs. Returns (policy_id, carrier_id, [producer_user_ids])."""
    with engine.begin() as c:
        carrier = _carrier(c)
        version = _version(c, carrier)
        hid = c.execute(households.insert().values(name=f"HH {_sfx()}").returning(households.c.id)).scalar_one()
        pers = c.execute(people.insert().values(household_id=hid, full_name=f"P {_sfx()}").returning(people.c.id)).scalar_one()
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=version,
                               household_id=hid, person_id=pers, policy_number=policy_number,
                               status="issued")
    producers = []
    for role, split in splits:
        uid = None
        with engine.begin() as c:
            uid = _user(c)
        ins.add_producer(_p(), policy["id"], producer_entity_type="user",
                         producer_entity_id=uid, producer_role=role, split_percentage=split)
        producers.append(uid)
    return policy["id"], carrier, producers


# --- split-aware expected ledger ---------------------------------------------

def test_generate_expected_credits_each_producer_by_split():
    policy_id, _carrier_id, producers = _policy_with_producers(
        [("writing_agent", 70), ("servicing_agent", 20), ("override", 10)])
    out = com.generate_expected(_p(), policy_id=policy_id, basis_amount=1000,
                                schedule="first_year")
    assert out["count"] == 3
    ledger = com.list_commissions(_p(), policy_id=policy_id)
    by_producer = {row["producer_entity_id"]: row for row in ledger}
    assert by_producer[producers[0]]["expected_amount"] == Decimal("700.00")
    assert by_producer[producers[1]]["expected_amount"] == Decimal("200.00")
    assert by_producer[producers[2]]["expected_amount"] == Decimal("100.00")  # override upline
    # the whole basis is credited across producers
    assert sum(r["expected_amount"] for r in ledger) == Decimal("1000.00")


def test_generate_skips_producers_without_a_split():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100), ("servicing_agent", None)])
    out = com.generate_expected(_p(), policy_id=policy_id, basis_amount=500)
    assert out["count"] == 1  # the no-split servicing agent earns nothing on this run


def test_record_expected_single_entry():
    policy_id, _c, producers = _policy_with_producers([("writing_agent", 100)])
    out = com.record_expected(_p(), policy_id=policy_id, producer_entity_type="user",
                              producer_entity_id=producers[0], expected_amount="250.50",
                              schedule="renewal")
    got = com.get_commission(_p(), out["id"])
    assert got["expected_amount"] == Decimal("250.50") and got["schedule"] == "renewal"
    assert got["status"] == "expected"


# --- received + reconciliation (variance surfaced) ---------------------------

def test_record_received_clean_marks_received():
    policy_id, _c, producers = _policy_with_producers([("writing_agent", 100)])
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=300)["created"][0]["id"]
    res = com.record_received(_p(), cid, received_amount=300)
    assert res["status"] == "received"
    assert com.get_commission(_p(), cid)["variance"] == 0.0


def test_underpayment_is_partial_overpayment_is_variance():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100)])
    cids = [e["id"] for e in com.generate_expected(_p(), policy_id=policy_id, basis_amount=100)["created"]]
    # single producer -> single entry
    cid = cids[0]
    com.record_received(_p(), cid, received_amount=80)
    assert com.get_commission(_p(), cid)["status"] == "partial"
    com.record_received(_p(), cid, received_amount=130)
    assert com.get_commission(_p(), cid)["status"] == "variance"


def test_statement_import_and_reconcile_matches_by_policy_number():
    policy_id, carrier_id, _pr = _policy_with_producers([("writing_agent", 100)], policy_number="POL-XYZ")
    com.generate_expected(_p(), policy_id=policy_id, basis_amount=500, schedule="first_year")
    stmt = com.import_statement(_p(), carrier_id=carrier_id, reference="STMT-1",
                                lines=[{"policy_number": "POL-XYZ", "schedule": "first_year", "amount": 450}])
    result = com.reconcile_statement(_p(), stmt["id"])
    assert result["lines_matched"] == 1
    ledger = com.list_commissions(_p(), policy_id=policy_id)
    assert ledger[0]["received_amount"] == Decimal("450.00")
    assert ledger[0]["status"] == "partial"  # 450 vs 500 expected -> variance surfaced


# --- operational detectors (shared Exception Engine) -------------------------

def test_variance_detector_raises_and_is_idempotent_and_auto_resolves():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100)])
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=100)["created"][0]["id"]
    com.record_received(_p(), cid, received_amount=60)  # partial -> variance condition
    first = det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    assert first["exceptions_opened"] >= 1
    second = det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    assert second["exceptions_opened"] == 0  # dedupe
    with engine.connect() as c:
        n = c.execute(select(exceptions.c.id).where(
            exceptions.c.dedupe_key == f"ins:commission_variance:{cid}")).scalars().all()
    assert len(n) == 1
    # clearing the condition (pay in full) auto-resolves the exception
    com.record_received(_p(), cid, received_amount=100)
    third = det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    assert third["exceptions_resolved"] >= 1
    with engine.connect() as c:
        status = c.execute(select(exceptions.c.status).where(
            exceptions.c.dedupe_key == f"ins:commission_variance:{cid}")).scalar_one()
    assert status in ("resolved", "cancelled")


def test_outstanding_detector_raises_for_past_due_unpaid():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100)])
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=200,
                                due_date=TODAY - timedelta(days=5))["created"][0]["id"]
    det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    with engine.connect() as c:
        n = c.execute(select(exceptions.c.id).where(
            exceptions.c.dedupe_key == f"ins:commission_outstanding:{cid}")).scalars().all()
    assert len(n) == 1


def test_not_yet_due_does_not_raise_outstanding():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100)])
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=200,
                                due_date=TODAY + timedelta(days=30))["created"][0]["id"]
    det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    with engine.connect() as c:
        n = c.execute(select(exceptions.c.id).where(
            exceptions.c.dedupe_key == f"ins:commission_outstanding:{cid}")).scalars().all()
    assert n == []


def test_scan_result_has_no_compliance_fields():
    result = det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    assert set(result) == {"exceptions_opened", "exceptions_reopened", "exceptions_resolved",
                           "failures", "failure_detail"}


# --- revenue rollup ----------------------------------------------------------

def test_commission_report_rolls_up_revenue():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 60), ("override", 40)])
    com.generate_expected(_p(), policy_id=policy_id, basis_amount=1000, schedule="first_year")
    report = insurance_reporting.commission_report(_p())
    assert report["revenue_category"] == "insurance_commissions"
    assert report["expected_total"] >= 1000.0
    assert "first_year" in report["by_schedule"]
    assert set(report) == {"revenue_category", "entry_count", "by_status", "expected_total",
                           "received_total", "outstanding_total", "variance_total",
                           "by_schedule", "by_organization"}


# --- record scope + capability gating ----------------------------------------

def test_out_of_scope_commission_is_hidden():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100)])
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=100)["created"][0]["id"]
    scoped_out = Principal(777, "z@e.com", "Z", frozenset({"insurance.commissions.read"}))
    assert com.list_commissions(scoped_out, policy_id=policy_id) == []
    with pytest.raises(com.CommissionNotFound):
        com.get_commission(scoped_out, cid)


def test_commission_requires_capability():
    policy_id, _c, producers = _policy_with_producers([("writing_agent", 100)])
    no_caps = Principal(2, "b@e.com", "B", frozenset())
    with pytest.raises(PermissionError):
        com.list_commissions(no_caps)
    with pytest.raises(PermissionError):
        com.generate_expected(no_caps, policy_id=policy_id, basis_amount=1)


# --- the compliance gate: no regulated determination ships -------------------

def test_no_regulated_determination_functions_in_commissions():
    import inspect as _inspect
    regulated = {"suitability", "check_suitability", "assess_suitability", "replacement_decision",
                 "recommend_replacement", "approve_compliance", "compliance_decision",
                 "regulatory_decision", "validate_license", "determine_ce"}
    verbs = ("suitab", "recommend", "certif", "determine", "regulatory")
    for mod in (com, insurance_reporting, det):
        defined = {n for n, obj in vars(mod).items()
                   if _inspect.isfunction(obj) and obj.__module__ == mod.__name__}
        assert defined & regulated == set(), f"regulated logic leaked into {mod.__name__}"
        assert not [n for n in defined for v in verbs if v in n], \
            f"a determination verb leaked into {mod.__name__}"
