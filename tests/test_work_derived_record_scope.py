"""Assignment to a client's WORK grants read scope on that client — and nothing else.

A preparer assigned a tax return, or an operations user assigned a task, could not open the
client the work belongs to: `record_in_scope` resolved only DIRECT `record_assignments` rows
on the person, and the assignments those roles actually receive name the work record
(`tax_return`, `task`, `exception`, `workflow_instance`), never the person. Their own Home
queue rendered a link per assigned item and every one of them 404'd.

The derived path (`app.security.authorization._WORK_ANCHORS`) closes exactly that gap. These
tests pin its boundaries, because the risk of a rule like this is that it grows:

  * it grants READ only — write scope still needs a direct assignment on the client;
  * it resolves only the person/household the assigned record itself names;
  * every other client stays invisible;
  * an inactive assignment grants nothing;
  * `record.read_all` holders (admin, advisor) are unaffected — they bypassed already.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import delete, insert

from app.db import (
    engine,
    households,
    people,
    record_assignments,
    tasks,
    tax_engagement_returns,
    tax_engagements,
    users,
    workflow_instances,
)
from app.security.authorization import record_in_scope
from app.security.models import Principal

TODAY = date.today()
CAPS = frozenset({"client.read"})            # no record.read_all: scope must be earned
READ_ALL = frozenset({"client.read", "record.read_all"})


def _env():
    """Two unrelated clients, one user, and no assignments yet."""
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"wd-{tag}@e.test", normalized_email=f"wd-{tag}@e.test",
            display_name=f"WD {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = c.execute(households.insert().values(
            name=f"HH {tag}").returning(households.c.id)).scalar_one()
        mine = c.execute(people.insert().values(
            full_name=f"Mine {tag}", household_id=hh).returning(people.c.id)).scalar_one()
        other = c.execute(people.insert().values(
            full_name=f"Other {tag}").returning(people.c.id)).scalar_one()
    return {"tag": tag, "uid": uid, "hh": hh, "mine": mine, "other": other,
            "p": Principal(uid, f"wd-{tag}@e.test", "WD", CAPS),
            "p_all": Principal(uid, f"wd-{tag}@e.test", "WD", READ_ALL)}


def _assign(uid, entity_type, entity_id, *, inactive=False):
    with engine.begin() as c:
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type=entity_type, entity_id=entity_id,
            assignment_type="primary", effective_date=TODAY - timedelta(days=1),
            inactive_date=(TODAY - timedelta(days=1)) if inactive else None))


def _task_for(person_id, tag):
    with engine.begin() as c:
        return c.execute(tasks.insert().values(
            title=f"T {tag}", person_id=person_id, status="open").returning(tasks.c.id)).scalar_one()


def _tax_return_for(person_id, tag):
    """A return reaches its client through its engagement — the one indirection in the rule.

    The engagement's firm/office/year and the return's type/jurisdiction are NOT NULL, so
    the fixtures already seeded by the migrations are reused rather than inventing more."""
    from sqlalchemy import select as _select
    from app.db import filing_jurisdictions, tax_firms, tax_offices, tax_return_types, tax_years
    with engine.begin() as c:
        firm = c.scalar(_select(tax_firms.c.id).limit(1))
        office = c.scalar(_select(tax_offices.c.id).limit(1))
        year = c.scalar(_select(tax_years.c.id).limit(1))
        rtype = c.scalar(_select(tax_return_types.c.id).limit(1))
        juris = c.scalar(_select(filing_jurisdictions.c.id).limit(1))
        eng = c.execute(tax_engagements.insert().values(
            person_id=person_id, tax_firm_id=firm, tax_office_id=office,
            tax_year_id=year, engagement_type="individual").returning(
            tax_engagements.c.id)).scalar_one()
        return c.execute(tax_engagement_returns.insert().values(
            tax_engagement_id=eng, return_type_id=rtype,
            jurisdiction_id=juris).returning(tax_engagement_returns.c.id)).scalar_one()


# --- the gap this closes -----------------------------------------------------

def test_task_assignment_grants_read_on_its_client():
    e = _env()
    assert record_in_scope(e["p"], "person", e["mine"]) is False      # nothing yet
    _assign(e["uid"], "task", _task_for(e["mine"], e["tag"]))
    assert record_in_scope(e["p"], "person", e["mine"]) is True


def test_tax_return_assignment_grants_read_on_its_client():
    """The Tax Prep case: assigned the return, must be able to open whose return it is."""
    e = _env()
    _assign(e["uid"], "tax_return", _tax_return_for(e["mine"], e["tag"]))
    assert record_in_scope(e["p"], "person", e["mine"]) is True


def test_household_owned_work_grants_read_on_the_household():
    """`tasks.person_id` is NOT NULL, so a task is always person-owned; the household path is
    exercised through a workflow instance, which may be anchored to a household alone."""
    e = _env()
    with engine.begin() as c:
        wid = c.execute(workflow_instances.insert().values(
            name=f"WF {e['tag']}", household_id=e["hh"]
        ).returning(workflow_instances.c.id)).scalar_one()
    _assign(e["uid"], "workflow_instance", wid)
    assert record_in_scope(e["p"], "household", e["hh"]) is True


# --- and the boundaries it must not cross ------------------------------------

def test_unrelated_clients_remain_inaccessible():
    """The whole risk of this rule. Assignment to one client's work must not widen further."""
    e = _env()
    _assign(e["uid"], "tax_return", _tax_return_for(e["mine"], e["tag"]))
    assert record_in_scope(e["p"], "person", e["mine"]) is True
    assert record_in_scope(e["p"], "person", e["other"]) is False
    # and not the household either, which this person happens to belong to
    assert record_in_scope(e["p"], "household", e["hh"]) is False


def test_work_assignment_never_grants_write():
    """Read the client to do the work; changing the client record still needs its own grant."""
    e = _env()
    _assign(e["uid"], "task", _task_for(e["mine"], e["tag"]))
    assert record_in_scope(e["p"], "person", e["mine"]) is True
    assert record_in_scope(e["p"], "person", e["mine"], write=True) is False


def test_direct_person_assignment_still_grants_write():
    """The pre-existing path is untouched."""
    e = _env()
    _assign(e["uid"], "person", e["mine"])
    assert record_in_scope(e["p"], "person", e["mine"], write=True) is True


def test_an_inactive_work_assignment_grants_nothing():
    e = _env()
    _assign(e["uid"], "task", _task_for(e["mine"], e["tag"]), inactive=True)
    assert record_in_scope(e["p"], "person", e["mine"]) is False


def test_a_user_with_no_assignments_still_sees_nothing():
    e = _env()
    assert record_in_scope(e["p"], "person", e["mine"]) is False
    assert record_in_scope(e["p"], "person", e["other"]) is False


def test_read_all_holders_are_unaffected():
    """Admin/advisor bypassed before and bypass now; the derived path is never consulted."""
    e = _env()
    assert record_in_scope(e["p_all"], "person", e["mine"]) is True
    assert record_in_scope(e["p_all"], "person", e["other"]) is True


def test_the_derived_path_is_read_only_and_client_anchored_by_construction():
    """Guards the rule's shape: every anchor is a work record, and the helper refuses any
    entity type that is not a client."""
    from app.security.authorization import _WORK_ANCHORS, CLIENT_ENTITY_TYPES
    assert {a[0] for a in _WORK_ANCHORS}.isdisjoint(CLIENT_ENTITY_TYPES)
    with engine.connect() as c:
        from app.security.authorization import _work_derived_scope
        e = _env()
        assert _work_derived_scope(c, e["p"], "organization", 1) is False
        assert _work_derived_scope(c, e["p"], "task", 1) is False


def teardown_module(module):
    """These tests seed assignments; leave the shared test DB as they found it."""
    with engine.begin() as c:
        c.execute(delete(record_assignments).where(
            record_assignments.c.assignment_type == "primary",
            record_assignments.c.effective_date == TODAY - timedelta(days=1)))
