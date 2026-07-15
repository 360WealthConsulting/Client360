"""Release 0.9.10 / Sprint 5.5 — SLA sweep & notifications (Phase 4) tests."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.db import (audit_events, engine, exception_events, exceptions, portal_notifications,
    tax_engagement_returns)
from app.services import exception_engine as ee
from app.services import exception_sla as sla

UTC = timezone.utc


def _case():
    from tests.test_tax_intake import _case as intake_case
    user_id, person_id, household_id, portal, result = intake_case()
    return user_id, person_id, household_id, result["return_id"]


def _breached(code, u, p, h, r, *, minutes_overdue=10, dedupe=None):
    ex = ee.raise_exception(code=code, actor_user_id=u, tax_engagement_return_id=r,
                            person_id=p, household_id=h, dedupe_key=dedupe or f"sla-{code}-{r}")
    with engine.begin() as c:
        c.execute(exceptions.update().where(exceptions.c.id == ex["id"])
                  .values(sla_due_at=datetime.now(UTC) - timedelta(minutes=minutes_overdue)))
    return ex["id"]


def _events(exc_id, event_type=None):
    with engine.connect() as c:
        q = select(exception_events.c.event_type).where(exception_events.c.exception_id == exc_id)
        if event_type:
            q = q.where(exception_events.c.event_type == event_type)
        return [e[0] for e in c.execute(q.order_by(exception_events.c.id))]


# --- SLA calculations --------------------------------------------------------

def test_severity_specific_sla_calculations_and_next_escalation():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for severity, cadence in sla.CADENCE_MINUTES.items():
        base = {"sla_due_at": now, "severity": severity, "status": "open",
                "escalation_level": 0, "last_notified_at": None}
        rep = sla.sla_report(base, now)
        assert rep["next_escalation_at"] == now  # first escalation opportunity is the breach time
        notified = {**base, "last_notified_at": now}
        assert sla.sla_report(notified, now)["next_escalation_at"] == now + timedelta(minutes=cadence)


def test_at_risk_and_breach_and_on_track_thresholds():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    def row(due): return {"sla_due_at": due, "severity": "high", "status": "open",
                          "escalation_level": 0, "last_notified_at": None}
    assert sla.sla_report(row(now + timedelta(hours=20)), now)["state"] == "on_track"
    assert sla.sla_report(row(now + timedelta(hours=4)), now)["state"] == "at_risk"  # within 8h
    assert sla.sla_report(row(now - timedelta(minutes=1)), now)["state"] == "breached"


# --- sweep: escalation + idempotency -----------------------------------------

def test_breach_escalates_and_repeated_sweep_is_idempotent():
    u, p, h, r = _case()
    eid = _breached("FILING_REJECTED", u, p, h, r)  # staff-facing blocker
    now = datetime.now(UTC)
    sla.sweep_exception_slas(now=now, actor_user_id=u)
    assert ee._fetch(eid)["escalation_level"] == 1
    sla.sweep_exception_slas(now=now, actor_user_id=u)  # same moment
    assert ee._fetch(eid)["escalation_level"] == 1  # no duplicate escalation
    assert _events(eid, "escalated") == ["escalated"]
    assert _events(eid, "notified") == ["notified"]


def test_escalation_level_progression_and_cap():
    u, p, h, r = _case()
    eid = _breached("FILING_REJECTED", u, p, h, r)
    now = datetime.now(UTC)
    levels = []
    for hours in (0, 5, 10, 15, 20):  # cadence 4h → +1 each step, capped at MAX
        sla.sweep_exception_slas(now=now + timedelta(hours=hours), actor_user_id=u)
        levels.append(ee._fetch(eid)["escalation_level"])
    assert levels == [1, 2, 3, 3, 3]
    assert _events(eid, "escalated") == ["escalated", "escalated", "escalated"]


def test_at_risk_notified_once():
    u, p, h, r = _case()
    ex = ee.raise_exception(code="CLIENT_UNRESPONSIVE", actor_user_id=u, tax_engagement_return_id=r,
                            person_id=p, household_id=h, dedupe_key=f"atrisk-{r}")
    now = datetime.now(UTC)
    with engine.begin() as c:  # within 8h → at_risk
        c.execute(exceptions.update().where(exceptions.c.id == ex["id"])
                  .values(sla_due_at=now + timedelta(hours=4)))
    sla.sweep_exception_slas(now=now, actor_user_id=u)
    sla.sweep_exception_slas(now=now, actor_user_id=u)
    assert _events(ex["id"], "notified") == ["notified"]  # single early warning
    assert ee._fetch(ex["id"])["escalation_level"] == 0  # at-risk does not escalate


# --- notifications -----------------------------------------------------------

def test_client_facing_notification_and_failure_recording():
    u, p, h, r = _case()  # _case creates a portal account for the client
    eid = _breached("CLIENT_UNRESPONSIVE", u, p, h, r)  # client-facing
    now = datetime.now(UTC)
    sla.sweep_exception_slas(now=now, actor_user_id=u)
    with engine.connect() as c:
        delivered = c.execute(select(portal_notifications.c.status).where(
            portal_notifications.c.entity_type == "exception",
            portal_notifications.c.entity_id == eid, portal_notifications.c.channel == "in_app")).scalars().all()
        disabled = c.execute(select(portal_notifications.c.status).where(
            portal_notifications.c.entity_type == "exception",
            portal_notifications.c.entity_id == eid, portal_notifications.c.channel == "email")).scalars().all()
    assert delivered and all(s == "delivered" for s in delivered)
    assert disabled and all(s == "disabled" for s in disabled)  # email stubbed, recorded honestly


def test_duplicate_notification_prevented_on_replay():
    u, p, h, r = _case()
    eid = _breached("CLIENT_UNRESPONSIVE", u, p, h, r)
    now = datetime.now(UTC)
    for _ in range(3):
        sla.sweep_exception_slas(now=now, actor_user_id=u)  # same moment, thrice
    with engine.connect() as c:
        pn = c.scalar(select(text("count(*)")).select_from(portal_notifications)
                      .where(portal_notifications.c.entity_type == "exception",
                             portal_notifications.c.entity_id == eid))
    assert _events(eid, "notified") == ["notified"]  # one dispatch batch
    assert pn == 2  # in_app + email for the single account, not multiplied by replays


def test_audit_and_timeline_published_on_escalation():
    from app.db import timeline_events
    u, p, h, r = _case()
    eid = _breached("FILING_REJECTED", u, p, h, r)
    sla.sweep_exception_slas(now=datetime.now(UTC), actor_user_id=u)
    with engine.connect() as c:
        esc_audit = c.scalar(select(text("count(*)")).select_from(audit_events)
                             .where(audit_events.c.action == "exception.escalated", audit_events.c.entity_id == str(eid)))
        notify_audit = c.scalar(select(text("count(*)")).select_from(audit_events)
                                .where(audit_events.c.action == "exception.notified", audit_events.c.entity_id == str(eid)))
        tl = c.scalar(select(text("count(*)")).select_from(timeline_events)
                      .where(timeline_events.c.source == "exception", timeline_events.c.person_id == p))
    assert esc_audit >= 1 and notify_audit >= 1 and tl >= 1


# --- exclusions / state handling ---------------------------------------------

def test_resolved_and_cancelled_are_ignored():
    u, p, h, r = _case()
    admin_none = None
    eid = _breached("FILING_REJECTED", u, p, h, r)
    ee.acknowledge(eid, principal=None); ee.begin_work(eid, principal=None)
    ee.resolve(eid, "done", principal=None)
    s = sla.sweep_exception_slas(now=datetime.now(UTC), actor_user_id=u)
    assert ee._fetch(eid)["escalation_level"] == 0  # resolved → not escalated
    assert _events(eid, "escalated") == []


def test_waiting_state_is_skipped():
    u, p, h, r = _case()
    eid = _breached("FILING_REJECTED", u, p, h, r, minutes_overdue=600)
    ee.acknowledge(eid, principal=None); ee.begin_work(eid, principal=None); ee.place_waiting(eid, principal=None)
    s = sla.sweep_exception_slas(now=datetime.now(UTC), actor_user_id=u)
    assert ee._fetch(eid)["escalation_level"] == 0 and s["waiting"] >= 1


def test_reopened_exception_is_swept():
    u, p, h, r = _case()
    key = f"reopen-{r}"
    eid = _breached("FILING_REJECTED", u, p, h, r, dedupe=key)
    ee.acknowledge(eid, principal=None); ee.begin_work(eid, principal=None); ee.resolve(eid, "x", principal=None)
    # recurrence reopens the same exception; keep its breached sla_due_at
    again = ee.raise_exception(code="FILING_REJECTED", actor_user_id=u, tax_engagement_return_id=r,
                               person_id=p, household_id=h, dedupe_key=key)
    assert again["id"] == eid and again["status"] == "reopened"
    with engine.begin() as c:
        c.execute(exceptions.update().where(exceptions.c.id == eid)
                  .values(sla_due_at=datetime.now(UTC) - timedelta(minutes=10), last_notified_at=None))
    sla.sweep_exception_slas(now=datetime.now(UTC), actor_user_id=u)
    assert ee._fetch(eid)["status"] == "escalated"  # reopened is active and subject to SLA


def test_unsupported_domain_excluded_from_sweep():
    import uuid
    from app.db import exception_types
    with engine.begin() as c:
        tid = c.execute(exception_types.insert().values(
            domain="operations", code=f"OPS_SWEEP_{uuid.uuid4().hex[:6]}", category="operational",
            name="Ops sweep", default_severity="high", sla_minutes=60).returning(exception_types.c.id)).scalar_one()
        oid = c.execute(exceptions.insert().values(
            exception_type_id=tid, domain="operations", category="operational", severity="high",
            status="open", title="ops", sla_due_at=datetime.now(UTC) - timedelta(hours=5),
            opened_at=datetime.now(UTC)).returning(exceptions.c.id)).scalar_one()
    sla.sweep_exception_slas(now=datetime.now(UTC), actor_user_id=1)
    with engine.connect() as c:
        level = c.scalar(select(exceptions.c.escalation_level).where(exceptions.c.id == oid))
    assert level == 0  # non-tax domain not processed


# --- scheduler ---------------------------------------------------------------

def test_scheduler_wrapper_safe_and_job_registered():
    from app.jobs import scheduler as sch
    sch.run_exception_sla_sweep()  # must not raise
    started = False
    if not sch._scheduler.running:
        sch.start_scheduler(); started = True
    try:
        assert sch._scheduler.get_job("exception-sla-sweep") is not None
    finally:
        if started:
            sch.stop_scheduler()
