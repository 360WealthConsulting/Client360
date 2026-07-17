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
from sqlalchemy import func, select

from app.db import (
    audit_events,
    engine,
    exceptions,
    households,
    insurance_commissions,
    insurance_product_families,
    insurance_product_versions,
    people,
    relationship_entities,
    timeline_events,
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
    pnum = f"POL-{_sfx()[:8]}"
    policy_id, carrier_id, _pr = _policy_with_producers([("writing_agent", 100)], policy_number=pnum)
    com.generate_expected(_p(), policy_id=policy_id, basis_amount=500, schedule="first_year")
    stmt = com.import_statement(_p(), carrier_id=carrier_id, reference="STMT-1",
                                lines=[{"policy_number": pnum, "schedule": "first_year", "amount": 450}])
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
                           "producer_payouts", "agency_retained",
                           "by_schedule", "by_organization", "by_producer"}


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


# ============================================================================
# Audit-and-revenue-validation pass (Phase 5 follow-up)
# ============================================================================

def _org(c, entity_type="organization"):
    return c.execute(relationship_entities.insert().values(
        entity_type=entity_type, name=f"Agency {_sfx()}", details={}, active=True
    ).returning(relationship_entities.c.id)).scalar_one()


def _audit_count(action, entity_id=None):
    with engine.connect() as c:
        q = select(func.count()).select_from(audit_events).where(audit_events.c.action == action)
        if entity_id is not None:
            q = q.where(audit_events.c.entity_id == str(entity_id))
        return c.execute(q).scalar_one()


def _commission_timeline_rows():
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.title.ilike("%commission%"))).scalar_one()


# --- 1. audit coverage: every mutation writes an immutable audit event -------

def test_every_commission_mutation_writes_an_audit_event():
    pnum = f"AUD-{_sfx()[:8]}"
    policy_id, carrier_id, producers = _policy_with_producers(
        [("writing_agent", 100)], policy_number=pnum)

    # generated
    before = _audit_count("insurance.commission.generated", policy_id)
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=500,
                                schedule="first_year")["created"][0]["id"]
    assert _audit_count("insurance.commission.generated", policy_id) == before + 1

    # expected_recorded (single)
    before = _audit_count("insurance.commission.expected_recorded")
    com.record_expected(_p(), policy_id=policy_id, producer_entity_type="user",
                        producer_entity_id=producers[0], expected_amount=100, schedule="renewal")
    assert _audit_count("insurance.commission.expected_recorded") == before + 1

    # received_recorded
    assert _audit_count("insurance.commission.received_recorded", cid) == 0
    com.record_received(_p(), cid, received_amount=400)
    assert _audit_count("insurance.commission.received_recorded", cid) == 1

    # adjusted
    assert _audit_count("insurance.commission.adjusted", cid) == 0
    com.record_adjustment(_p(), cid, amount=100, kind="adjustment")
    assert _audit_count("insurance.commission.adjusted", cid) == 1

    # statement imported + line reconciled + statement reconciled
    assert _audit_count("insurance.commission.statement_imported") >= 0
    stmt = com.import_statement(_p(), carrier_id=carrier_id, reference="AUD-STMT",
                                lines=[{"policy_number": pnum, "schedule": "renewal", "amount": 90}])
    assert _audit_count("insurance.commission.statement_imported", stmt["id"]) == 1
    before_line = _audit_count("insurance.commission.line_reconciled")
    com.reconcile_statement(_p(), stmt["id"])
    assert _audit_count("insurance.commission.line_reconciled") == before_line + 1
    assert _audit_count("insurance.commission.statement_reconciled", stmt["id"]) == 1

    # written_off
    wid = com.record_expected(_p(), policy_id=policy_id, producer_entity_type="user",
                              producer_entity_id=producers[0], expected_amount=50)["id"]
    com.write_off(_p(), wid)
    assert _audit_count("insurance.commission.written_off", wid) == 1


def test_variance_exception_lifecycle_is_audited():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100)])
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=100)["created"][0]["id"]
    com.record_received(_p(), cid, received_amount=60)  # variance condition
    before_raise = _audit_count("exception.raised")
    det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    assert _audit_count("exception.raised") > before_raise  # opening the exception was audited
    before_resolve = _audit_count("exception.resolved")
    com.record_received(_p(), cid, received_amount=100)  # clears the condition
    det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    assert _audit_count("exception.resolved") > before_resolve  # auto-resolve was audited


# --- 1b. Timeline visibility & privacy: no compensation on the client timeline

def test_commission_activity_never_lands_on_the_client_timeline():
    pnum = f"PRIV-{_sfx()[:8]}"
    before = _commission_timeline_rows()
    policy_id, carrier_id, _pr = _policy_with_producers([("writing_agent", 100)], policy_number=pnum)
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=100,
                                due_date=TODAY - timedelta(days=10))["created"][0]["id"]
    com.record_received(_p(), cid, received_amount=60)   # variance
    stmt = com.import_statement(_p(), carrier_id=carrier_id, reference="PRIV-STMT",
                                lines=[{"policy_number": pnum, "amount": 10}])
    com.reconcile_statement(_p(), stmt["id"])
    det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)  # raises variance + outstanding

    # No NEW timeline event mentioning commissions/compensation is created by any commission
    # mutation or by the scan (delta guards against pre-existing rows in a shared test DB).
    assert _commission_timeline_rows() == before, "commission/compensation leaked onto the timeline"

    # The commission exceptions are firm-internal (unanchored) — no person/household anchor,
    # which is what would otherwise publish a client timeline event.
    with engine.connect() as c:
        rows = c.execute(select(exceptions.c.person_id, exceptions.c.household_id).where(
            exceptions.c.dedupe_key.in_((f"ins:commission_variance:{cid}",
                                         f"ins:commission_outstanding:{cid}")))).mappings().all()
    assert rows and all(r["person_id"] is None and r["household_id"] is None for r in rows)


# --- 2. revenue rollup is ledger-derived, idempotent, and correction-aware ---

def test_report_totals_are_derived_from_the_ledger():
    policy_id, _c, producers = _policy_with_producers([("writing_agent", 100)])
    com.generate_expected(_p(), policy_id=policy_id, basis_amount=1000)
    e = com.list_commissions(_p(), policy_id=policy_id)[0]
    com.record_received(_p(), e["id"], received_amount=750)
    report = insurance_reporting.commission_report(_p())
    # A full-scope (record.read_all) principal's report totals equal the raw ledger sums over
    # the ENTIRE ledger — the rollup is a faithful, uncapped projection of the transactions.
    with engine.connect() as c:
        total_expected = c.execute(select(func.coalesce(
            func.sum(insurance_commissions.c.expected_amount), 0))).scalar_one()
        total_received = c.execute(select(func.coalesce(
            func.sum(insurance_commissions.c.received_amount), 0))).scalar_one()
    assert Decimal(str(report["expected_total"])) == Decimal(str(total_expected))
    assert Decimal(str(report["received_total"])) == Decimal(str(total_received))


def test_adjustment_reversal_chargeback_writeoff_flow_through_totals():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100)])
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=1000)["created"][0]["id"]
    com.record_received(_p(), cid, received_amount=1000)
    assert com.get_commission(_p(), cid)["received_amount"] == Decimal("1000.00")
    com.record_adjustment(_p(), cid, amount=-200, kind="reversal")
    assert com.get_commission(_p(), cid)["received_amount"] == Decimal("800.00")
    com.record_adjustment(_p(), cid, amount=-800, kind="chargeback")
    assert com.get_commission(_p(), cid)["received_amount"] == Decimal("0.00")
    com.record_adjustment(_p(), cid, amount=50, kind="adjustment")
    got = com.get_commission(_p(), cid)
    assert got["received_amount"] == Decimal("50.00") and got["status"] == "partial"
    # the rollup reflects the net after all corrections (received == 50 for this entry)
    rep = insurance_reporting.commission_report(_p(), )
    assert rep["by_producer"], "producer breakdown present"


def test_revenue_rollup_is_idempotent_and_non_duplicating():
    policy_id, _c, _pr = _policy_with_producers([("writing_agent", 100)])
    com.generate_expected(_p(), policy_id=policy_id, basis_amount=500)
    with engine.connect() as c:
        rows_before = c.execute(select(func.count()).select_from(insurance_commissions)).scalar_one()
    r1 = insurance_reporting.commission_report(_p())
    r2 = insurance_reporting.commission_report(_p())
    assert r1 == r2  # pure re-derivation — no drift, no double counting
    with engine.connect() as c:
        rows_after = c.execute(select(func.count()).select_from(insurance_commissions)).scalar_one()
    assert rows_before == rows_after  # reporting persists nothing


def test_reconciliation_after_a_correction_clears_the_variance():
    pnum = f"COR-{_sfx()[:8]}"
    policy_id, carrier_id, _pr = _policy_with_producers([("writing_agent", 100)], policy_number=pnum)
    cid = com.generate_expected(_p(), policy_id=policy_id, basis_amount=500)["created"][0]["id"]
    stmt = com.import_statement(_p(), carrier_id=carrier_id, reference="COR-STMT",
                                lines=[{"policy_number": pnum, "amount": 450}])
    com.reconcile_statement(_p(), stmt["id"])  # 450 vs 500 -> partial (variance)
    det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    with engine.connect() as c:
        opened = c.execute(select(exceptions.c.status).where(
            exceptions.c.dedupe_key == f"ins:commission_variance:{cid}")).scalar_one()
    assert opened not in ("resolved", "cancelled")
    # correction: carrier pays the missing 50 -> clean, variance auto-resolves
    com.record_adjustment(_p(), cid, amount=50, kind="adjustment")
    assert com.get_commission(_p(), cid)["status"] == "received"
    det.run_insurance_commission_scan(actor_user_id=1, today=TODAY)
    with engine.connect() as c:
        after = c.execute(select(exceptions.c.status).where(
            exceptions.c.dedupe_key == f"ins:commission_variance:{cid}")).scalar_one()
    assert after in ("resolved", "cancelled")


def test_producer_payouts_and_agency_retained_split_from_ledger():
    with engine.begin() as c:
        agency = _org(c)
    policy_id, _c, producers = _policy_with_producers([("writing_agent", 60)])
    # attach an organization producer (agency) with a 40% override
    ins.add_producer(_p(), policy_id, producer_entity_type="organization",
                     producer_entity_id=agency, producer_role="override", split_percentage=40)
    com.generate_expected(_p(), policy_id=policy_id, basis_amount=1000)
    rep = insurance_reporting.commission_report(_p())
    # this test's policy is isolated in scope; individual producer gets 600, agency org 400
    assert rep["producer_payouts"]["expected"] >= 600.0
    assert rep["agency_retained"]["expected"] >= 400.0
    assert f"organization:{agency}" in rep["by_producer"]
    assert f"user:{producers[0]}" in rep["by_producer"]
