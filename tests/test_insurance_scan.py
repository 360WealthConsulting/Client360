"""Insurance Phase 6 — single orchestrated scan, work queues, assignment, scheduling.

Pins that Phase 6 REUSES the shared platform (Exception Engine, Work Management assignment
rules, the existing scheduler) with no insurance-specific subsystem: one `run_insurance_scan`
across every detector; idempotent + auto-resolving/reopening; per-detector failure isolation;
honest reporting (organizations scanned, opened/resolved/reopened/skipped/failures);
organization-based record scope that never reaches the client Timeline; insurance work queues
via the existing criteria framework; auto-assignment via the existing assignment rules; and the
scheduler registering an idempotent, non-overlapping job.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.db import (
    assignment_rules,
    engine,
    exceptions,
    households,
    insurance_product_families,
    insurance_product_versions,
    people,
    record_assignments,
    relationship_entities,
    users,
    work_queues,
)
from app.security.models import Principal
from app.services import insurance as ins
from app.services import insurance_commissions as com
from app.services import insurance_detectors as det
from app.services import insurance_work as iw
from app.services.work_intelligence import queue_matches

FULL = frozenset({"insurance.read", "insurance.write", "insurance.commissions.read",
                  "insurance.commissions.write", "record.read_all", "record.write_all",
                  "exception.read", "exception.write"})
TODAY = date(2026, 7, 15)


def _p():
    return Principal(1, "a@e.com", "A", FULL)


def _sfx():
    return uuid.uuid4().hex


def _carrier(c):
    return c.execute(relationship_entities.insert().values(
        entity_type="insurance_carrier", name=f"Carrier {_sfx()}", details={}, active=True
    ).returning(relationship_entities.c.id)).scalar_one()


def _org(c):
    return c.execute(relationship_entities.insert().values(
        entity_type="organization", name=f"Org {_sfx()}", details={}, active=True
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
        email=f"u-{sfx}@e.com", normalized_email=f"u-{sfx}@e.com", display_name="U",
        auth_subject=f"sub-{sfx}", status="active").returning(users.c.id)).scalar_one()


def _overdue_review_policy():
    """A person/household policy with a past-due review (raises INS_REVIEW_OVERDUE)."""
    with engine.begin() as c:
        carrier, hid = _carrier(c), c.execute(households.insert().values(
            name=f"HH {_sfx()}").returning(households.c.id)).scalar_one()
        version = _version(c, carrier)
        pers = c.execute(people.insert().values(household_id=hid, full_name=f"P {_sfx()}").returning(people.c.id)).scalar_one()
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=version,
                               household_id=hid, person_id=pers, status="in_force")
    ins.schedule_review(_p(), review_type="annual", due_date=TODAY - timedelta(days=10),
                        policy_id=policy["id"])
    return policy["id"]


def _org_commission_variance():
    """An ORG-owned policy with a commission variance (raises INS_COMMISSION_VARIANCE)."""
    with engine.begin() as c:
        carrier, org = _carrier(c), _org(c)
        version = _version(c, carrier)
        prod = _user(c)
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=version,
                               organization_id=org, status="issued")
    ins.add_producer(_p(), policy["id"], producer_entity_type="user",
                     producer_entity_id=prod, producer_role="writing_agent", split_percentage=100)
    cid = com.generate_expected(_p(), policy_id=policy["id"], basis_amount=100)["created"][0]["id"]
    com.record_received(_p(), cid, received_amount=60)  # partial -> variance
    return org, cid


# --- 1. single orchestrated scan + honest reporting --------------------------

def test_run_insurance_scan_reports_all_fields_across_detectors():
    _overdue_review_policy()
    _org_commission_variance()
    result = det.run_insurance_scan(actor_user_id=1, today=TODAY)
    assert set(result) == {"organizations_scanned", "exceptions_opened", "exceptions_resolved",
                           "exceptions_reopened", "exceptions_skipped", "failures",
                           "failure_detail", "by_detector"}
    assert result["exceptions_opened"] >= 2          # at least the review + the commission
    assert set(result["by_detector"]) == {"reviews", "licensing", "commissions"}
    assert result["organizations_scanned"] >= 1      # the org-owned policy
    assert result["failures"] == 0


def test_scan_is_idempotent():
    _overdue_review_policy()
    first = det.run_insurance_scan(actor_user_id=1, today=TODAY)
    assert first["exceptions_opened"] >= 1
    second = det.run_insurance_scan(actor_user_id=1, today=TODAY)
    assert second["exceptions_opened"] == 0          # dedupe: no new exceptions
    assert second["exceptions_skipped"] >= 1         # prior-open conditions re-confirmed


def test_scan_isolates_a_failing_detector(monkeypatch):
    _overdue_review_policy()  # a reviews condition exists

    def boom(*a, **k):
        raise RuntimeError("licensing blew up")

    monkeypatch.setattr(det, "run_insurance_licensing_scan", boom)
    result = det.run_insurance_scan(actor_user_id=1, today=TODAY)
    assert result["failures"] >= 1
    assert any(f.get("detector") == "licensing" for f in result["failure_detail"])
    # the other detectors still ran and the review exception still opened
    assert "reviews" in result["by_detector"] and "commissions" in result["by_detector"]
    assert result["exceptions_opened"] >= 1


def test_scan_auto_resolves_when_condition_clears():
    org, cid = _org_commission_variance()
    det.run_insurance_scan(actor_user_id=1, today=TODAY)      # opens the variance
    com.record_received(_p(), cid, received_amount=100)        # clears it (paid in full)
    result = det.run_insurance_scan(actor_user_id=1, today=TODAY)
    assert result["exceptions_resolved"] >= 1
    with engine.connect() as c:
        status = c.execute(select(exceptions.c.status).where(
            exceptions.c.dedupe_key == f"ins:commission_variance:{cid}")).scalar_one()
    assert status in ("resolved", "cancelled")


# --- 2. organization-based record scope, never client-facing -----------------

def test_commission_exception_is_org_scoped_and_off_the_client_timeline():
    org, cid = _org_commission_variance()
    det.run_insurance_scan(actor_user_id=1, today=TODAY)
    with engine.connect() as c:
        row = c.execute(select(exceptions.c.related_entity_type, exceptions.c.related_entity_id,
                               exceptions.c.person_id, exceptions.c.household_id).where(
            exceptions.c.dedupe_key == f"ins:commission_variance:{cid}")).mappings().one()
    assert row["related_entity_type"] == "organization" and row["related_entity_id"] == org
    assert row["person_id"] is None and row["household_id"] is None   # never client-facing


# --- 3. work queues via the existing criteria framework ----------------------

def test_seeded_insurance_queues_match_insurance_exceptions_only():
    with engine.connect() as c:
        queues = {q["code"]: q["criteria"] for q in c.execute(select(
            work_queues.c.code, work_queues.c.criteria).where(
            work_queues.c.code.like("insurance_%"))).mappings()}
    assert {"insurance_unassigned", "insurance_exceptions", "insurance_reviews",
            "insurance_licensing", "insurance_commissions", "insurance_high_priority"} <= set(queues)
    ins_item = {"domain": "insurance", "entity_type": "exception", "code": "INS_COMMISSION_VARIANCE",
                "severity": "high", "assigned": False}
    ben_item = {"domain": "benefits", "entity_type": "exception", "code": "BEN_RENEWAL_AT_RISK",
                "severity": "high", "assigned": False}
    assert queue_matches(ins_item, queues["insurance_exceptions"])
    assert queue_matches(ins_item, queues["insurance_commissions"])
    assert queue_matches(ins_item, queues["insurance_high_priority"])
    assert not queue_matches(ben_item, queues["insurance_exceptions"])  # domain isolation


# --- 4. auto-assignment via the existing assignment rules --------------------

def test_auto_assign_uses_existing_assignment_rules():
    _org_commission_variance()
    det.run_insurance_scan(actor_user_id=1, today=TODAY)
    with engine.begin() as c:
        assignee = _user(c)
        rule_id = c.execute(assignment_rules.insert().values(
            name=f"ins-ops-{_sfx()}", entity_type="exception", conditions={"domain": "insurance"},
            assignment_role="primary", assignee_user_id=assignee, priority=100, active=True,
        ).returning(assignment_rules.c.id)).scalar_one()
    try:
        result = iw.auto_assign_unassigned(actor_user_id=1)
        assert result["assigned"] >= 1
        # a real record_assignments row now exists for an insurance exception
        with engine.connect() as c:
            n = c.execute(select(func.count()).select_from(record_assignments).where(
                record_assignments.c.entity_type == "exception",
                record_assignments.c.assignment_type == "primary")).scalar_one()
        assert n >= 1
    finally:
        with engine.begin() as c:
            c.execute(assignment_rules.delete().where(assignment_rules.c.id == rule_id))


def test_assignment_only_considers_insurance_domain():
    # insurance_exception_attributes returns None for a missing/non-insurance exception,
    # so auto-assignment never reaches across domains.
    with engine.connect() as c:
        assert iw.insurance_exception_attributes(c, -1) is None


# --- 5. scheduler registration (existing scheduler; idempotent, non-overlapping) ---

def test_scheduler_registers_insurance_scan_job():
    from app.jobs import scheduler as sched
    if sched._scheduler.running:
        sched.stop_scheduler()
    sched.start_scheduler()
    try:
        job = sched._scheduler.get_job("insurance-detector-scan")
        assert job is not None
        assert job.max_instances == 1 and job.coalesce is True   # overlap prevention
        assert any(j["id"] == "insurance-detector-scan" for j in sched.scheduler_status()["jobs"])
    finally:
        sched.stop_scheduler()


# --- 6. authorization: dedicated insurance.scan capability (pre-Phase-7 cleanup) ---

def test_scan_requires_the_insurance_scan_capability():
    """The operational scan is gated by insurance.scan — a dedicated, non-mutating-detection
    authority. insurance.write alone no longer suffices (no weakening: the roles that could scan
    before are granted insurance.scan; see the grant test)."""
    from app.security.dependencies import require_capability
    dep = require_capability("insurance.scan")

    allowed = Principal(1, "a@e.com", "A", frozenset({"insurance.scan"}))
    assert dep(principal=allowed) is allowed

    write_only = Principal(2, "b@e.com", "B", frozenset({"insurance.write"}))
    with pytest.raises(HTTPException) as exc:
        dep(principal=write_only)
    assert exc.value.status_code == 403


def test_insurance_scan_capability_granted_to_operational_roles_only():
    with engine.connect() as c:
        granted = set(c.execute(text(
            "select r.code from role_capabilities rc "
            "join roles r on r.id = rc.role_id "
            "join capabilities cap on cap.id = rc.capability_id "
            "where cap.code = 'insurance.scan'")).scalars())
    assert granted == {"administrator", "insurance_agent", "insurance_operations"}
    assert "insurance_compliance" not in granted  # compliance cannot run operational scans
