"""Release 0.9.11 — Benefits Work Management integration (Phase 4) tests.

Benefits exceptions project through the canonical ``work_items()`` system (no benefits-specific
work-item table or assignment system). Covers projection + anchoring, My/Team/Unassigned/queue
filtering, assignment + reassignment + precedence, cross-org isolation, queue membership not
bypassing record scope, SLA/aging, the scheduled detector scan (registration, idempotency,
overlap prevention, per-org failure isolation, honest counts), configurable thresholds, inert
disabled-provider types, and no sensitive-data leakage.
"""
import os
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, insert, select

from app.db import (assignment_rules, engine, exception_types, exceptions, households, people,
    record_assignments, users)
from app.security.models import Principal
from app.services import benefits_domain as bd
from app.services import benefits_enrollment as be
from app.services import benefits_detectors as det
from app.services import benefits_work as bw
from app.services import organization_service as org
from app.services import work_intelligence as wi
from app.services import work_management as wm

TODAY = date.today()
FULL = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                  "benefits.enroll", "benefits.compliance", "exception.read", "exception.write",
                  "work.read", "record.read_all", "record.write_all"})
SCOPED = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                    "benefits.enroll", "exception.read", "work.read"})


def _user():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(email=f"wi-{s}@e.com", normalized_email=f"wi-{s}@e.com",
            display_name=f"wi {s}", auth_subject=f"wi-{s}", status="active").returning(users.c.id)).scalar_one()


def _p(user_id, caps=FULL):
    return Principal(user_id, "u@e.com", "U", caps)


def _admin():
    return _p(_user())


def _person():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {s}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hh, full_name=f"Emp {s}", active=True)
                        .returning(people.c.id)).scalar_one()
    return pid, hh


def _org(admin):
    o = org.create_organization(admin, name=f"WI Co {uuid.uuid4().hex[:6]}")["organization_id"]
    org.update_organization(o, principal=admin, renewal_month=6)
    org.add_service_line(o, "benefits", principal=admin, status="active")
    return o


def _eligibility_scenario(admin, *, hire_days_ago=90):
    """An org whose one established, unenrolled employee yields an eligibility exception."""
    o = _org(admin)
    plan = bd.create_plan(admin, organization_id=o, plan_type_code="medical", name="PPO")
    bd.create_plan_year(plan["id"], principal=admin, plan_year=2027, status="active")
    pid, _ = _person()
    emp = be.create_employment(admin, organization_id=o, person_id=pid, hire_date=TODAY - timedelta(days=hire_days_ago))
    return o, emp, pid


def _exc(org_id, code):
    with engine.connect() as c:
        return c.execute(select(exceptions.c.id, exceptions.c.sla_due_at, exceptions.c.title, exceptions.c.person_id)
            .select_from(exceptions.join(exception_types, exception_types.c.id == exceptions.c.exception_type_id))
            .where(exceptions.c.domain == "benefits", exceptions.c.related_entity_id == org_id,
                   exception_types.c.code == code, exceptions.c.status.notin_(("resolved", "cancelled")))
            ).mappings().first()


def _items(principal, org_id, code=None):
    return [i for i in wm.work_items(principal)
            if i.get("domain") == "benefits" and i.get("organization_id") == org_id
            and (code is None or i.get("code") == code)]


# --- projection + anchoring --------------------------------------------------

def test_benefits_exception_projects_into_work_items_with_anchors():
    a = _admin()
    o, emp, pid = _eligibility_scenario(a)
    det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
    items = _items(a, o, "BEN_ELIGIBILITY_UNRESOLVED")
    assert len(items) == 1
    it = items[0]
    assert it["entity_type"] == "exception" and it["work_type"] == "exception"
    assert it["organization_id"] == o and it["person_id"] == pid and it["household_id"] is not None
    assert it["priority"] in ("low", "normal", "high", "urgent")
    assert "sla_state" in it and "due_date" in it and "code" in it
    # no benefits-specific work-item table exists — the item is a plain work item
    assert it["entity_id"] == _exc(o, "BEN_ELIGIBILITY_UNRESOLVED")["id"]


def test_sla_state_and_aging_projected():
    a = _admin()
    o, emp, pid = _eligibility_scenario(a)
    det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
    it = _items(a, o, "BEN_ELIGIBILITY_UNRESOLVED")[0]
    assert it["sla_state"] in ("on_track", "at_risk", "breached", "none")
    agenda = wi.daily_agenda([it])
    assert "sla_risk" in agenda[0] and "priority_score" in agenda[0]


# --- My / Team / Unassigned / queue filtering --------------------------------

def test_my_work_dashboard_queues_and_unassigned():
    a = _admin()
    o, emp, pid = _eligibility_scenario(a)
    det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
    data = wm.dashboard(a)
    assert any(i.get("organization_id") == o and i.get("code") == "BEN_ELIGIBILITY_UNRESOLVED" for i in data["items"])
    codes = {q["code"] for q in data["queues"]}
    assert {"benefits_unassigned", "benefits_enrollment", "benefits_retirement", "benefits_high_priority"} <= codes
    # the enrollment queue contains it; the retirement queue does not
    enrollment = wm.queue_detail(a, "benefits_enrollment")["items"]
    assert any(i.get("organization_id") == o for i in enrollment)
    retirement = wm.queue_detail(a, "benefits_retirement")["items"]
    assert all(i.get("organization_id") != o for i in retirement)
    # unassigned queue: admin holds no exception assignment → the item is unassigned to them
    unassigned = wm.queue_detail(a, "benefits_unassigned")["items"]
    assert any(i.get("organization_id") == o for i in unassigned)


def test_high_priority_queue_uses_severity_and_domain():
    a = _admin()
    o = _org(a)
    # a blocker-severity benefits exception (contribution deposit late is blocker) raised directly
    from app.services import exception_engine as ee
    ee.raise_exception(code="BEN_CONTRIBUTION_DEPOSIT_LATE", principal=None, actor_user_id=a.user_id,
                       source="system", related_entity_type="organization", related_entity_id=o,
                       dedupe_key=f"ben:test_blocker:{o}")
    hi = wm.queue_detail(a, "benefits_high_priority")["items"]
    assert any(i.get("organization_id") == o for i in hi)


# --- assignment / reassignment / precedence ----------------------------------

def test_assignment_and_reassignment():
    a = _admin()
    o, emp, pid = _eligibility_scenario(a)
    det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
    exc_id = _exc(o, "BEN_ELIGIBILITY_UNRESOLVED")["id"]
    u1, u2 = _user(), _user()
    aid = wm.assign_work(entity_type="exception", entity_id=exc_id, assignment_role="primary",
                         user_id=u1, actor_user_id=a.user_id)
    assignee = _p(u1, SCOPED)
    assert any(i["entity_id"] == exc_id and i["assigned"] for i in _items(assignee, o))
    new_aid = wm.reassign_work(aid, user_id=u2, actor_user_id=a.user_id)
    assert new_aid != aid
    assert any(i["entity_id"] == exc_id and i["assigned"] for i in _items(_p(u2, SCOPED), o))


def test_assignment_rule_precedence_and_default():
    a = _admin()
    o, emp, pid = _eligibility_scenario(a)          # category 'client'
    det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
    exc_id = _exc(o, "BEN_ELIGIBILITY_UNRESOLVED")["id"]
    specific_user, default_user = _user(), _user()
    with engine.begin() as c:
        # specific rule (lower priority number wins) + a default benefits rule — both org-scoped
        c.execute(insert(assignment_rules).values(name="ben-client", entity_type="exception",
            conditions={"domain": "benefits", "category": "client", "organization_id": o},
            assignment_role="primary", assignee_user_id=specific_user, priority=10, active=True))
        c.execute(insert(assignment_rules).values(name="ben-default", entity_type="exception",
            conditions={"domain": "benefits", "organization_id": o},
            assignment_role="primary", assignee_user_id=default_user, priority=100, active=True))
    try:
        created = bw.apply_benefits_exception_rules(exc_id, actor_user_id=a.user_id)
        assert created  # a rule fired
        # the first matching primary (priority 10) wins and stops evaluation
        with engine.connect() as c:
            assignees = set(c.scalars(select(record_assignments.c.user_id).where(
                record_assignments.c.entity_type == "exception", record_assignments.c.entity_id == exc_id)))
        assert specific_user in assignees and default_user not in assignees
    finally:
        with engine.begin() as c:
            c.execute(delete(assignment_rules).where(assignment_rules.c.name.in_(("ben-client", "ben-default"))))


def test_permanent_relationship_owner_is_context_not_assignee():
    a = _admin()
    o, emp, pid = _eligibility_scenario(a)
    renewal_owner = _user()
    org.assign_role(o, principal=a, user_id=renewal_owner, role_code="renewal_owner", is_primary=True)
    det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
    it = _items(a, o, "BEN_ELIGIBILITY_UNRESOLVED")[0]
    # the permanent relationship owner is surfaced as context, but is NOT the work assignee
    assert it["relationship_owner_user_id"] == renewal_owner
    assert not it["assigned"]  # no work assignment created merely from the permanent role


# --- isolation / scope -------------------------------------------------------

def test_cross_organization_isolation_and_queue_scope():
    a = _admin()
    owner1 = _p(_user(), SCOPED)
    owner2 = _p(_user(), SCOPED)
    o1 = _org(owner1)
    plan = bd.create_plan(owner1, organization_id=o1, plan_type_code="medical", name="PPO")
    bd.create_plan_year(plan["id"], principal=owner1, plan_year=2027, status="active")
    p1, _ = _person()
    be.create_employment(owner1, organization_id=o1, person_id=p1, hire_date=TODAY - timedelta(days=90))
    det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
    # owner1 sees org1's benefits work item; owner2 (unrelated) sees none of it
    assert _items(owner1, o1, "BEN_ELIGIBILITY_UNRESOLVED")
    assert _items(owner2, o1) == []
    # queue membership does not bypass record scope: owner2's enrollment queue excludes org1
    q = wm.queue_detail(owner2, "benefits_enrollment")["items"]
    assert all(i.get("organization_id") != o1 for i in q)


# --- scheduled scan ----------------------------------------------------------

def test_run_benefits_scan_honest_counts_and_idempotency():
    a = _admin()
    o, emp, pid = _eligibility_scenario(a)
    first = det.run_benefits_scan(actor_user_id=a.user_id, today=TODAY)
    assert first["scanned_organizations"] >= 1
    assert first["exceptions_opened"] >= 1
    assert first["failures"] == 0
    # repeated scan opens nothing new (idempotent); the persisting condition counts as skipped
    second = det.run_benefits_scan(actor_user_id=a.user_id, today=TODAY)
    assert second["exceptions_opened"] == 0 and second["exceptions_skipped"] >= 1
    # resolution reflected: once the employee enrolls, a later scan reports it resolved
    plan_year = _exc  # noqa - readability
    from app.db import benefit_plan_years, benefit_employments
    with engine.connect() as c:
        yr = c.scalar(select(benefit_plan_years.c.id).limit(1).where(
            benefit_plan_years.c.plan_id.in_(select(bd.benefit_plans.c.id).where(bd.benefit_plans.c.organization_id == o))))
    be.enroll(a, benefit_employment_id=emp, plan_year_id=yr, status="elected")
    third = det.run_benefits_scan(actor_user_id=a.user_id, today=TODAY)
    assert third["exceptions_resolved"] >= 1


def test_scheduler_registers_idempotent_non_overlapping_job():
    from app.jobs import scheduler as sched
    if sched._scheduler.running:
        sched.stop_scheduler()
    sched.start_scheduler()
    try:
        job = sched._scheduler.get_job("benefits-detector-scan")
        assert job is not None
        assert job.max_instances == 1 and job.coalesce is True   # overlap prevention
        assert any(j["id"] == "benefits-detector-scan" for j in sched.scheduler_status()["jobs"])
    finally:
        sched.stop_scheduler()


def test_per_condition_failure_isolation_does_not_abort_scan(monkeypatch):
    a = _admin()
    o, emp, pid = _eligibility_scenario(a)
    # force the eligibility detector's raise to fail; other detectors must still run/report
    import app.services.benefits_detectors as d
    real_raise = d.ee.raise_exception

    def flaky(*args, **kwargs):
        if kwargs.get("code") == "BEN_ELIGIBILITY_UNRESOLVED":
            raise RuntimeError("boom")
        return real_raise(*args, **kwargs)

    monkeypatch.setattr(d.ee, "raise_exception", flaky)
    result = d.run_benefits_scan(actor_user_id=a.user_id, today=TODAY)
    assert result["failures"] >= 1                 # the failure is recorded
    assert result["scanned_organizations"] >= 1    # the scan still completed


# --- configurable thresholds -------------------------------------------------

def test_configurable_new_hire_window():
    a = _admin()
    # employee hired 100 days ago; default window (30) → eligibility, not new-hire
    o = _org(a)
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO")
    bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="active")
    pid, _ = _person()
    be.create_employment(a, organization_id=o, person_id=pid, hire_date=TODAY - timedelta(days=100))
    det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
    assert _exc(o, "BEN_ELIGIBILITY_UNRESOLVED") and not _exc(o, "BEN_NEW_HIRE_ENROLLMENT_DUE")
    # (D.31) the runtime engine is now the AUTHORITATIVE source for the benefits window (seeded config
    # item). Widen the window past 100 via the runtime config item — not the env var — and the same
    # employee is now a "new hire", not "eligibility unresolved".
    from sqlalchemy import text as _text

    from app.db import engine as _rt_engine
    from app.services.runtime.cache import RUNTIME_CACHE
    with _rt_engine.begin() as _c:
        _c.execute(_text("UPDATE configuration_items SET value=CAST('365' AS json) "
                         "WHERE code='benefits.new_hire_window_days'"))
    RUNTIME_CACHE.invalidate()
    try:
        det.scan_benefits_exceptions(actor_user_id=a.user_id, today=TODAY)
        assert _exc(o, "BEN_NEW_HIRE_ENROLLMENT_DUE") and not _exc(o, "BEN_ELIGIBILITY_UNRESOLVED")
    finally:
        with _rt_engine.begin() as _c:
            _c.execute(_text("UPDATE configuration_items SET value=CAST('30' AS json) "
                             "WHERE code='benefits.new_hire_window_days'"))
        RUNTIME_CACHE.invalidate()


def test_threshold_safe_defaults():
    from app import config
    monkeyp_env = {"BENEFITS_NEW_HIRE_WINDOW_DAYS", "BENEFITS_RENEWAL_WARNING_DAYS", "BENEFITS_OE_WARNING_DAYS"}
    for key in monkeyp_env:
        os.environ.pop(key, None)
    assert config.benefits_new_hire_window_days() == 30
    assert config.benefits_renewal_warning_days() == 60
    assert config.benefits_open_enrollment_warning_days() == 7
    assert config.benefits_scan_interval_minutes() == 30


# --- inert disabled providers + no leakage -----------------------------------

def test_disabled_provider_codes_never_appear_as_work_items():
    a = _admin()
    o = _org(a)
    bd.create_plan(a, organization_id=o, plan_type_code="401k", name="401k", provider_code="betterment")
    det.run_benefits_scan(actor_user_id=a.user_id, today=TODAY)
    codes = {i.get("code") for i in wm.work_items(a) if i.get("domain") == "benefits"}
    assert not (codes & {"BEN_CARRIER_SUBMISSION_FAILED", "BEN_PAYROLL_SYNC_FAILED", "BEN_PROVIDER_CONNECTION_STALE"})


def test_no_sensitive_data_in_work_item_or_scan_result():
    a = _admin()
    o = _org(a)
    secret = f"SecretEmp{uuid.uuid4().hex[:6]}"
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {secret}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(household_id=hh, full_name=secret, active=True).returning(people.c.id)).scalar_one()
    plan = bd.create_plan(a, organization_id=o, plan_type_code="medical", name="PPO")
    bd.create_plan_year(plan["id"], principal=a, plan_year=2027, status="active")
    be.create_employment(a, organization_id=o, person_id=pid, hire_date=TODAY - timedelta(days=90))
    result = det.run_benefits_scan(actor_user_id=a.user_id, today=TODAY)
    assert secret not in str(result)                       # scheduler result carries no PII
    it = _items(a, o, "BEN_ELIGIBILITY_UNRESOLVED")[0]
    assert secret not in str(it.get("title"))
    for forbidden in ("ein", "compensation", "deferral_percent", "ssn"):
        assert forbidden not in it  # no sensitive fields projected onto the work item
