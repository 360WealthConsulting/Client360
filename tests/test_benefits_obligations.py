"""Release 0.9.11 — Compliance & renewal obligations, SLA, notifications (Phase 5) tests.

Obligations drive date-driven benefits exceptions through the existing Exception Engine and
Phase-4 Work Management; the shared SLA sweep escalates and notifies internally only. Scans run
globally, so assertions filter to a fresh Organization for determinism.
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db import (audit_events, benefit_obligations, engine, exception_events, exception_types,
    exceptions, households, people, users)
from app.security import benefits_crypto
from app.security.models import Principal
from app.services import benefits_detectors as det
from app.services import benefits_domain as bd
from app.services import benefits_notifications as bn
from app.services import benefits_obligations as ob
from app.services import exception_engine as ee
from app.services import exception_sla as sla
from app.services import organization_service as org
from app.services import work_management as wm

os.environ.setdefault("BENEFITS_FIELD_KEY", benefits_crypto.generate_key())
TODAY = date.today()
NOW = datetime.now(timezone.utc)

FULL = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                  "benefits.enroll", "benefits.compliance", "exception.read", "exception.write",
                  "exception.resolve", "exception.compliance", "work.read",
                  "record.read_all", "record.write_all"})
SCOPED = frozenset({"organization.read", "organization.write", "benefits.read", "benefits.write",
                    "exception.read", "work.read"})


def _user():
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(email=f"ob-{s}@e.com", normalized_email=f"ob-{s}@e.com",
            display_name=f"ob {s}", auth_subject=f"ob-{s}", status="active").returning(users.c.id)).scalar_one()


def _admin(caps=FULL):
    return Principal(_user(), "a@e.com", "A", caps)


def _org(admin):
    o = org.create_organization(admin, name=f"Ob Co {uuid.uuid4().hex[:6]}")["organization_id"]
    org.update_organization(o, principal=admin, renewal_month=6)
    org.add_service_line(o, "benefits", principal=admin, status="active")
    return o


def _scan(admin, today=TODAY):
    return det.scan_benefits_exceptions(actor_user_id=admin.user_id, today=today)


def _exc(org_id, code):
    with engine.connect() as c:
        return c.execute(select(exceptions.c.id, exceptions.c.sla_due_at, exceptions.c.title,
                                exceptions.c.status, exceptions.c.category, exceptions.c.severity,
                                exceptions.c.escalation_level)
            .select_from(exceptions.join(exception_types, exception_types.c.id == exceptions.c.exception_type_id))
            .where(exceptions.c.domain == "benefits", exceptions.c.related_entity_id == org_id,
                   exception_types.c.code == code, exceptions.c.status.notin_(("resolved", "cancelled")))
            ).mappings().first()


# --- obligation lifecycle + templates ----------------------------------------

def test_obligation_create_update_complete_cancel_and_recurrence():
    a = _admin()
    o = _org(a)
    created = ob.create_obligation(a, organization_id=o, obligation_type="form_5500",
                                   due_date=TODAY + timedelta(days=30), recurrence="annual", warning_days=60)
    oid = created["id"]
    assert created["status"] == "scheduled"
    ob.update_obligation(oid, principal=a, notes="prep started", status="in_progress")
    assert ob.get_obligation(oid, principal=a)["status"] == "in_progress"
    # completion of an annual obligation materializes the next occurrence
    ob.complete_obligation(oid, principal=a, completed_date=TODAY)
    later = ob.list_obligations(a, o)
    assert any(x["status"] == "completed" for x in later)
    nxt = [x for x in later if x["status"] == "scheduled" and x["obligation_type"] == "form_5500"]
    assert nxt and nxt[0]["due_date"] == created["due_date"].replace(year=created["due_date"].year + 1)
    completed_row = next(x for x in later if x["status"] == "completed")
    assert ob._materialize_next(dict(completed_row), actor_user_id=a.user_id) is None  # idempotent (year+1 exists)


def test_template_vs_instantiated_obligation():
    a = _admin()
    o = _org(a)
    templates = {t["code"] for t in ob.list_templates()}
    assert {"form_5500", "fiduciary_review", "benefit_renewal"} <= templates
    inst = ob.instantiate_from_template(a, template_code="fiduciary_review", organization_id=o,
                                        due_date=TODAY + timedelta(days=20))
    assert inst["obligation_type"] == "fiduciary_review" and inst["template_id"] is not None
    assert inst["warning_days"] == 30 and inst["recurrence"] == "annual"  # template defaults applied


def test_renewal_calendar_milestone_sequencing():
    a = _admin()
    o = _org(a)
    from app.services import engagement_service as es
    eng = es.create_engagement(a, service_line_code="benefits", engagement_type="benefit_renewal",
                               organization_id=o)["id"]
    milestones = {
        "renewal_identified": TODAY + timedelta(days=1), "census_due": TODAY + timedelta(days=15),
        "quotes_due": TODAY + timedelta(days=45), "employer_decision": TODAY + timedelta(days=60),
        "open_enrollment_ends": TODAY + timedelta(days=80), "effective_date": TODAY + timedelta(days=90)}
    ob.create_renewal_calendar(a, organization_id=o, engagement_id=eng, milestones=milestones)
    obligations = ob.list_obligations(a, o)  # ordered by due_date
    seq = [x["obligation_type"] for x in obligations if x["engagement_id"] == eng]
    assert seq == ["renewal_identified", "census_due", "quotes_due", "employer_decision",
                   "open_enrollment_ends", "effective_date"]


# --- date-driven detector ----------------------------------------------------

def test_obligation_deadline_raises_exception_with_deadline_sla_and_autoresolves():
    a = _admin()
    o = _org(a)
    due = TODAY + timedelta(days=10)
    created = ob.create_obligation(a, organization_id=o, obligation_type="form_5500", due_date=due, warning_days=60)
    _scan(a)
    exc = _exc(o, "BEN_5500_FILING_DUE")
    assert exc and exc["category"] == "compliance"
    # the exception SLA is set to the obligation's real due date (not the type default)
    assert exc["sla_due_at"].date() == due
    # completing the obligation auto-resolves the exception on the next scan
    ob.complete_obligation(created["id"], principal=a, completed_date=TODAY)
    _scan(a)
    assert _exc(o, "BEN_5500_FILING_DUE") is None


def test_unsupported_obligation_type_stays_inert():
    a = _admin()
    o = _org(a)
    ob.create_obligation(a, organization_id=o, obligation_type="contribution_deposit_review",
                         due_date=TODAY - timedelta(days=1))  # overdue, but no reliable deposit data
    _scan(a)
    assert _exc(o, "BEN_CONTRIBUTION_DEPOSIT_LATE") is None
    assert "contribution_deposit_late" in det.DETECTOR_GAPS
    assert "contribution_deposit_review" not in det.OBLIGATION_EXCEPTION_CODE


def test_stable_dedupe_idempotent_and_reopen():
    a = _admin()
    o = _org(a)
    created = ob.create_obligation(a, organization_id=o, obligation_type="fiduciary_review",
                                   due_date=TODAY + timedelta(days=5), warning_days=30)
    _scan(a); _scan(a)
    with engine.connect() as c:
        cnt = c.scalar(select(func.count()).select_from(exceptions)
            .select_from(exceptions.join(exception_types, exception_types.c.id == exceptions.c.exception_type_id))
            .where(exceptions.c.related_entity_id == o, exception_types.c.code == "BEN_FIDUCIARY_REVIEW_DUE",
                   exceptions.c.status.notin_(("resolved", "cancelled"))))
    assert cnt == 1  # no duplicate on repeated scans
    exc_id = _exc(o, "BEN_FIDUCIARY_REVIEW_DUE")["id"]
    ob.complete_obligation(created["id"], principal=a)
    _scan(a)
    assert _exc(o, "BEN_FIDUCIARY_REVIEW_DUE") is None
    # reopen the obligation → the SAME exception reopens
    ob.update_obligation(created["id"], principal=a, status="scheduled")
    _scan(a)
    reopened = _exc(o, "BEN_FIDUCIARY_REVIEW_DUE")
    assert reopened and reopened["id"] == exc_id
    with engine.connect() as c:
        types = [e["event_type"] for e in c.execute(select(exception_events.c.event_type)
                 .where(exception_events.c.exception_id == exc_id).order_by(exception_events.c.id)).mappings()]
    assert types[0] == "opened" and "resolved" in types and types[-1] == "reopened"


# --- SLA escalation (shared sweep, benefits) ---------------------------------

def _raise_benefits(o, code, sla_due_at, owner=None):
    return ee.raise_exception(code=code, principal=None, actor_user_id=None, source="system",
                              related_entity_type="organization", related_entity_id=o, owner_user_id=owner,
                              sla_due_at=sla_due_at, dedupe_key=f"ben:test:{code}:{o}")


def test_benefits_sla_breach_escalates_and_notifies_internally_only():
    a = _admin()
    o = _org(a)
    owner = _user()
    exc = _raise_benefits(o, "BEN_5500_FILING_DUE", NOW - timedelta(hours=1), owner=owner)  # breached
    from app.db import portal_notifications
    with engine.connect() as c:
        portal_before = c.scalar(select(func.count()).select_from(portal_notifications))
    summary = sla.sweep_exception_slas(now=NOW, actor_user_id=a.user_id)
    assert summary["escalated"] >= 1
    with engine.connect() as c:
        level = c.scalar(select(exceptions.c.escalation_level).where(exceptions.c.id == exc["id"]))
        notified = [dict(e) for e in c.execute(select(exception_events.c.metadata)
                    .where(exception_events.c.exception_id == exc["id"],
                           exception_events.c.event_type == "notified")).mappings()]
        portal_after = c.scalar(select(func.count()).select_from(portal_notifications))
    assert level >= 1
    # staff-only dispatch; NO employer-portal notification for benefits
    assert notified and all(d["audience"] == "staff" for d in notified[0]["metadata"]["dispatches"])
    assert portal_after == portal_before


def test_benefits_sla_at_risk_notifies_once_cooldown():
    a = _admin()
    o = _org(a)
    _raise_benefits(o, "BEN_FIDUCIARY_REVIEW_DUE", NOW + timedelta(hours=4))  # at_risk (<=8h)
    exc_id = _exc(o, "BEN_FIDUCIARY_REVIEW_DUE")["id"]
    sla.sweep_exception_slas(now=NOW, actor_user_id=a.user_id)
    sla.sweep_exception_slas(now=NOW, actor_user_id=a.user_id)  # cooldown: no second warning
    with engine.connect() as c:
        n = c.scalar(select(func.count()).select_from(exception_events)
            .where(exception_events.c.exception_id == exc_id, exception_events.c.event_type == "notified"))
    assert n == 1


# --- queue projection --------------------------------------------------------

def test_obligation_exceptions_route_to_compliance_and_renewals_queues():
    a = _admin()
    o = _org(a)
    ob.create_obligation(a, organization_id=o, obligation_type="form_5500", due_date=TODAY + timedelta(days=5), warning_days=60)
    ob.create_obligation(a, organization_id=o, obligation_type="renewal", due_date=TODAY + timedelta(days=5), warning_days=90)
    _scan(a)
    compliance = wm.queue_detail(a, "benefits_compliance")["items"]
    assert any(i.get("organization_id") == o and i.get("code") == "BEN_5500_FILING_DUE" for i in compliance)
    renewals = wm.queue_detail(a, "benefits_renewals")["items"]
    assert any(i.get("organization_id") == o and i.get("code") == "BEN_RENEWAL_AT_RISK" for i in renewals)


# --- authorization -----------------------------------------------------------

def test_cross_organization_isolation():
    a = _admin()
    owner2 = Principal(_user(), "o2@e.com", "O2", SCOPED)
    o = _org(a)
    ob.create_obligation(a, organization_id=o, obligation_type="form_5500", due_date=TODAY + timedelta(days=5), warning_days=60)
    _scan(a)
    exc_id = _exc(o, "BEN_5500_FILING_DUE")["id"]
    with pytest.raises(ee.ExceptionAuthorizationError):
        ee.get_exception(exc_id, principal=owner2)
    with pytest.raises(PermissionError):
        ob.list_obligations(owner2, o)


def test_compliance_sensitive_resolution_requires_capability():
    a = _admin()
    o = _org(a)
    ob.create_obligation(a, organization_id=o, obligation_type="form_5500", due_date=TODAY + timedelta(days=5), warning_days=60)
    _scan(a)
    exc_id = _exc(o, "BEN_5500_FILING_DUE")["id"]
    ee.acknowledge(exc_id, principal=a, actor_user_id=a.user_id)
    ee.begin_work(exc_id, principal=a, actor_user_id=a.user_id)
    # a firm-wide writer WITHOUT exception.compliance cannot resolve a compliance-category benefits exception
    non_comp = Principal(_user(), "n@e.com", "N", frozenset({"exception.read", "exception.write",
                                                             "exception.resolve", "record.read_all", "record.write_all"}))
    with pytest.raises(ee.ExceptionAuthorizationError):
        ee.resolve(exc_id, "done", principal=non_comp, actor_user_id=non_comp.user_id)


# --- notifications -----------------------------------------------------------

def test_scan_health_notification_honest_outcomes(monkeypatch):
    # no failures → skipped (nothing sent)
    assert bn.record_scan_health({"failures": 0, "scanned_organizations": 3})["outcome"] == "skipped"
    # failures + real in-app provider → delivered/honest; audit written
    from app.db import audit_events as ae
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(ae).where(ae.c.action == "benefits.scan.health"))
    res = bn.record_scan_health({"failures": 2, "scanned_organizations": 1})
    assert res["notified"] and res["outcome"] in ("delivered", "disabled", "unavailable")
    with engine.connect() as c:
        after = c.scalar(select(func.count()).select_from(ae).where(ae.c.action == "benefits.scan.health"))
    assert after == before + 1
    # a disabled provider must report 'disabled', never claim delivery
    class _Disabled:
        def deliver(self, **kw): return {"delivered": False}
    monkeypatch.setitem(bn.NOTIFICATION_PROVIDERS, "in_app", _Disabled())
    assert bn.record_scan_health({"failures": 1, "scanned_organizations": 1})["outcome"] == "disabled"


def test_run_benefits_scan_reports_materialization_and_failures(monkeypatch):
    a = _admin()
    o = _org(a)
    ob.create_obligation(a, organization_id=o, obligation_type="form_5500", due_date=TODAY + timedelta(days=5), warning_days=60)
    result = det.run_benefits_scan(actor_user_id=a.user_id, today=TODAY)
    assert result["scanned_organizations"] >= 1 and "obligations_materialized" in result
    # per-condition failure isolation for the obligation detector
    real = det.ee.raise_exception

    def flaky(*args, **kwargs):
        if kwargs.get("code") == "BEN_5500_FILING_DUE":
            raise RuntimeError("boom")
        return real(*args, **kwargs)
    monkeypatch.setattr(det.ee, "raise_exception", flaky)
    r2 = det.run_benefits_scan(actor_user_id=a.user_id, today=TODAY)
    assert r2["failures"] >= 1 and r2["scanned_organizations"] >= 1


# --- no sensitive data + audit/immutability ----------------------------------

def test_no_sensitive_data_and_audit_events():
    a = _admin()
    o = _org(a)
    created = ob.create_obligation(a, organization_id=o, obligation_type="form_5500",
                                   due_date=TODAY + timedelta(days=5), warning_days=60)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(audit_events)
                        .where(audit_events.c.action == "benefit.obligation.created",
                               audit_events.c.entity_id == str(created["id"]))) == 1
    _scan(a)
    exc = _exc(o, "BEN_5500_FILING_DUE")
    assert exc["title"] == "Form 5500"  # generic, non-sensitive (obligation title)
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(exception_events)
                        .where(exception_events.c.exception_id == exc["id"],
                               exception_events.c.event_type == "opened")) == 1
    # append-only ledger
    with pytest.raises(Exception):
        with engine.begin() as c:
            c.execute(exception_events.update().where(exception_events.c.exception_id == exc["id"])
                      .values(event_type="tampered"))
