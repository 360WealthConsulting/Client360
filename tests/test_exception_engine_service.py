"""Release 0.9.10 / Sprint 5.5 — Core Exception Service (Phase 2) tests.

Covers the state machine, dedupe/idempotency, reopen-after-resolution, stale-action
rejection, assignment, append-only history, audit + timeline publication, domain-aware
record-scope authorization, least-privilege capabilities, and unsupported-domain rejection.
"""
import uuid

import pytest
from sqlalchemy import select, text

from app.db import engine, exception_types, exception_events, timeline_events, audit_events
from app.security.models import Principal
from app.services import exception_engine as ee

FULL = frozenset({"exception.read", "exception.write", "exception.resolve", "exception.compliance"})
WRITE = frozenset({"exception.read", "exception.write"})
READONLY = frozenset({"exception.read"})
READALL = frozenset({"exception.read", "exception.write", "exception.resolve", "record.read_all"})


def _case():
    """A tax return assigned to a user (record-scoped), with client person/household."""
    from tests.test_tax_intake import _case as intake_case
    user_id, person_id, household_id, portal, result = intake_case()
    return user_id, person_id, household_id, result["return_id"]


def _principal(user_id, caps):
    return Principal(user_id, f"u{user_id}@e.com", f"U{user_id}", caps)


def _raise(return_id, person_id, household_id, *, code="CLIENT_UNRESPONSIVE", actor=None,
           principal=None, dedupe_key=None):
    return ee.raise_exception(
        code=code, actor_user_id=actor, principal=principal, dedupe_key=dedupe_key,
        tax_engagement_return_id=return_id, person_id=person_id, household_id=household_id,
    )


# --- state machine -----------------------------------------------------------

def test_valid_transition_path():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    ex = _raise(r, p, h, principal=admin, actor=u)
    assert ex["status"] == "open"
    assert ee.acknowledge(ex["id"], principal=admin, actor_user_id=u)["status"] == "acknowledged"
    assert ee.begin_work(ex["id"], principal=admin, actor_user_id=u)["status"] == "in_progress"
    assert ee.place_waiting(ex["id"], principal=admin, actor_user_id=u)["status"] == "waiting"
    assert ee.begin_work(ex["id"], principal=admin, actor_user_id=u)["status"] == "in_progress"
    assert ee.resolve(ex["id"], "handled", principal=admin, actor_user_id=u)["status"] == "resolved"


def test_invalid_transition_rejected():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    ex = _raise(r, p, h, principal=admin, actor=u)
    with pytest.raises(ee.InvalidTransitionError):
        ee.resolve(ex["id"], "x", principal=admin, actor_user_id=u)  # open -> resolved not allowed


def test_stale_action_rejected_by_expected_status():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    ex = _raise(r, p, h, principal=admin, actor=u)
    with pytest.raises(ee.StaleActionError):
        ee.acknowledge(ex["id"], principal=admin, actor_user_id=u, expected_status="in_progress")


def test_duplicate_action_rejected():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    ex = _raise(r, p, h, principal=admin, actor=u)
    ee.acknowledge(ex["id"], principal=admin, actor_user_id=u)
    with pytest.raises(ee.InvalidTransitionError):
        ee.acknowledge(ex["id"], principal=admin, actor_user_id=u)  # already acknowledged


# --- dedupe / idempotency / reopen -------------------------------------------

def test_idempotent_raise_and_duplicate_open_prevention():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    key = f"dk-{uuid.uuid4().hex[:10]}"
    first = _raise(r, p, h, principal=admin, actor=u, dedupe_key=key)
    second = _raise(r, p, h, principal=admin, actor=u, dedupe_key=key)
    assert first["id"] == second["id"]  # idempotent — no duplicate open exception
    with engine.connect() as c:
        opened = c.scalar(select(text("count(*)")).select_from(exception_events)
                          .where(exception_events.c.exception_id == first["id"],
                                 exception_events.c.event_type == "opened"))
    assert opened == 1


def test_reopen_after_resolution_via_dedupe():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    key = f"dk-{uuid.uuid4().hex[:10]}"
    ex = _raise(r, p, h, principal=admin, actor=u, dedupe_key=key)
    ee.acknowledge(ex["id"], principal=admin, actor_user_id=u)
    ee.begin_work(ex["id"], principal=admin, actor_user_id=u)
    ee.resolve(ex["id"], "done", principal=admin, actor_user_id=u)
    again = _raise(r, p, h, principal=admin, actor=u, dedupe_key=key)
    assert again["id"] == ex["id"] and again["status"] == "reopened"


# --- assignment --------------------------------------------------------------

def test_assign_and_reassign():
    from app.db import teams
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    with engine.connect() as c:
        team_id = c.scalar(select(teams.c.id).where(teams.c.code == "operations"))
    ex = _raise(r, p, h, principal=admin, actor=u)
    a = ee.assign(ex["id"], principal=admin, actor_user_id=u, owner_user_id=u)
    assert a["owner_user_id"] == u and a["owner_team_id"] is None
    b = ee.assign(ex["id"], principal=admin, actor_user_id=u, owner_team_id=team_id)
    assert b["owner_team_id"] == team_id and b["owner_user_id"] is None


# --- escalation --------------------------------------------------------------

def test_escalate_bumps_level():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    ex = _raise(r, p, h, principal=admin, actor=u)
    e1 = ee.escalate(ex["id"], principal=admin, actor_user_id=u)
    assert e1["status"] == "escalated" and e1["escalation_level"] == 1
    e2 = ee.escalate(ex["id"], principal=admin, actor_user_id=u)
    assert e2["escalation_level"] == 2


# --- append-only history + audit + timeline ----------------------------------

def test_event_history_append_only_and_ordered():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    ex = _raise(r, p, h, principal=admin, actor=u)
    ee.acknowledge(ex["id"], principal=admin, actor_user_id=u)
    ee.begin_work(ex["id"], principal=admin, actor_user_id=u)
    history = ee.event_history(ex["id"], principal=admin)
    assert [e["event_type"] for e in history] == ["opened", "acknowledged", "started"]
    # ledger is DB-immutable
    with pytest.raises(Exception) as exc:
        with engine.begin() as c:
            c.execute(exception_events.update().where(exception_events.c.id == history[0]["id"]).values(event_type="x"))
    assert "append-only" in str(exc.value)


def test_audit_and_timeline_published_on_raise():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    ex = _raise(r, p, h, principal=admin, actor=u)
    with engine.connect() as c:
        audit = c.scalar(select(text("count(*)")).select_from(audit_events)
                         .where(audit_events.c.action == "exception.raised",
                                audit_events.c.entity_id == str(ex["id"])))
        tl = c.scalar(select(text("count(*)")).select_from(timeline_events)
                      .where(timeline_events.c.source == "exception", timeline_events.c.person_id == p))
    assert audit >= 1 and tl >= 1


# --- authorization -----------------------------------------------------------

def test_record_scope_authorization():
    u, p, h, r = _case()
    owner = _principal(u, WRITE)          # assigned to the return (via create_engagement) — in scope
    outsider = _principal(9_000_001, WRITE)  # not assigned, no read_all — out of scope
    readall = _principal(9_000_002, READALL)
    ex = ee.raise_exception(code="CLIENT_UNRESPONSIVE", actor_user_id=u, principal=owner,
                            tax_engagement_return_id=r, person_id=p, household_id=h)
    assert ee.get_exception(ex["id"], principal=owner)["id"] == ex["id"]
    assert ee.get_exception(ex["id"], principal=readall)["id"] == ex["id"]
    with pytest.raises(ee.ExceptionAuthorizationError):
        ee.get_exception(ex["id"], principal=outsider)
    with pytest.raises(ee.ExceptionAuthorizationError):
        ee.acknowledge(ex["id"], principal=outsider, actor_user_id=outsider.user_id)


def test_least_privilege_capabilities():
    u, p, h, r = _case()
    admin = _principal(u, READALL)
    readonly = _principal(u, READONLY)
    writer = _principal(u, WRITE | frozenset({"record.read_all"}))
    # read-only cannot raise
    with pytest.raises(ee.ExceptionAuthorizationError):
        _raise(r, p, h, principal=readonly, actor=u)
    # blocker resolution needs exception.resolve — writer (write only) is denied
    blocker = ee.raise_exception(code="FILING_REJECTED", actor_user_id=u, principal=admin,
                                 tax_engagement_return_id=r, person_id=p, household_id=h)
    ee.acknowledge(blocker["id"], principal=admin, actor_user_id=u)
    ee.begin_work(blocker["id"], principal=admin, actor_user_id=u)
    with pytest.raises(ee.ExceptionAuthorizationError):
        ee.resolve(blocker["id"], "fixed", principal=writer, actor_user_id=u)
    # admin (has exception.resolve) can
    assert ee.resolve(blocker["id"], "fixed", principal=admin, actor_user_id=u)["status"] == "resolved"


def test_compliance_resolution_requires_compliance_capability():
    u, p, h, r = _case()
    admin = _principal(u, READALL | frozenset({"exception.compliance"}))
    non_comp = _principal(u, WRITE | frozenset({"record.read_all", "exception.resolve"}))
    ex = ee.raise_exception(code="COMPLIANCE_SOD_VIOLATION", actor_user_id=u, principal=admin,
                            tax_engagement_return_id=r, person_id=p, household_id=h)
    ee.acknowledge(ex["id"], principal=admin, actor_user_id=u)
    ee.begin_work(ex["id"], principal=admin, actor_user_id=u)
    with pytest.raises(ee.ExceptionAuthorizationError):
        ee.resolve(ex["id"], "reviewed", principal=non_comp, actor_user_id=u)
    assert ee.resolve(ex["id"], "reviewed", principal=admin, actor_user_id=u)["status"] == "resolved"


# --- domain support ----------------------------------------------------------

def test_unsupported_domain_rejected_on_raise():
    # Seed a non-tax type directly and confirm the service refuses it.
    with engine.begin() as c:
        c.execute(exception_types.insert().values(
            domain="wealth", code=f"WEALTH_TEST_{uuid.uuid4().hex[:6]}", category="operational",
            name="Wealth test", default_severity="low", sla_minutes=60))
        code = c.execute(select(exception_types.c.code).where(exception_types.c.domain == "wealth")
                         .order_by(exception_types.c.id.desc())).scalars().first()
    admin = _principal(1, READALL)
    with pytest.raises(ee.UnsupportedDomainError):
        ee.raise_exception(code=code, actor_user_id=1, principal=admin)


def test_unsupported_domain_rejected_on_list():
    admin = _principal(1, READALL)
    with pytest.raises(ee.UnsupportedDomainError):
        ee.list_exceptions(admin, domain="wealth")


def test_unknown_code_rejected():
    admin = _principal(1, READALL)
    with pytest.raises(ee.ExceptionNotFoundError):
        ee.raise_exception(code="NO_SUCH_CODE", actor_user_id=1, principal=admin)


# --- list with record-scope --------------------------------------------------

def test_list_exceptions_record_scoped():
    u, p, h, r = _case()
    owner = _principal(u, WRITE)
    outsider = _principal(9_100_001, WRITE)
    ee.raise_exception(code="CLIENT_UNRESPONSIVE", actor_user_id=u, principal=owner,
                       tax_engagement_return_id=r, person_id=p, household_id=h)
    owner_ids = {e["id"] for e in ee.list_exceptions(owner)}
    outsider_ids = {e["id"] for e in ee.list_exceptions(outsider)}
    assert owner_ids and not (owner_ids & outsider_ids)
