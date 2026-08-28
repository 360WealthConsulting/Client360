"""Governed identity correction for people.full_name.

``update_person_contact`` deliberately refuses identity fields and the client portal cannot reach
them at all, so ``correct_person_identity`` is the single governed path that may write full_name.
These tests pin the contract, the governance side effects (outbox + timeline + audit), the fact
that the two ordinary edit paths STILL cannot touch the field, and the merge-preview interaction
that motivated the work: a corrected canonical name must not be re-filled from a duplicate.

All names here are synthetic.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.db import audit_events, engine, households, people, timeline_events, users
from app.services.people import (
    EDITABLE_FIELDS,
    correct_person_identity,
    update_person_contact,
)
from app.services.person_merge import merge_people, preview_person_merge


def _actor():
    """A synthetic staff user — audit_events.actor_user_id is FK-constrained to users."""
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        return c.execute(users.insert().values(
            email=f"ic-{s}@example.com", normalized_email=f"ic-{s}@example.com",
            display_name=f"Identity Corrector {s}", status="active",
        ).returning(users.c.id)).scalar_one()


def _person(**cols):
    """A synthetic person. Callers override full_name/first_name/last_name as the test needs."""
    s = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(
            households.insert().values(name=f"IC {s}").returning(households.c.id)
        ).scalar_one()
        values = {"household_id": hid, "full_name": f"Ident Person {s}", "active": True}
        values.update(cols)
        return c.execute(people.insert().values(**values).returning(people.c.id)).scalar_one()


def _get(pid):
    with engine.connect() as c:
        return c.execute(select(people).where(people.c.id == pid)).mappings().one()


def _count(table, **where):
    with engine.connect() as c:
        q = select(func.count()).select_from(table)
        for k, v in where.items():
            q = q.where(getattr(table.c, k) == v)
        return c.execute(q).scalar_one()


def _timeline(pid, event_type="person_identity_corrected"):
    with engine.connect() as c:
        return c.execute(
            select(timeline_events)
            .where(timeline_events.c.person_id == pid,
                   timeline_events.c.event_type == event_type)
        ).mappings().all()


def _audit(pid, action="person.identity_corrected"):
    with engine.connect() as c:
        return c.execute(
            select(audit_events)
            .where(audit_events.c.entity_id == str(pid), audit_events.c.action == action)
        ).mappings().all()


def _outbox(pid):
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT payload FROM outbox_events WHERE name = 'people.person_updated'"
        )).mappings().all()
    # The stored envelope nests the domain payload under "payload".
    return [r["payload"]["payload"] for r in rows
            if r["payload"]["payload"].get("person_id") == pid]


# --- contract ------------------------------------------------------------------------------

def test_a_correction_sets_the_canonical_name_and_reports_the_change():
    actor = _actor()
    pid = _person(full_name=None, first_name="Dana", last_name="Halloway")
    changed = correct_person_identity(pid, full_name="Dana Halloway", actor_user_id=actor,
                                      reason="Canonical name was never populated")
    assert changed == ["full_name"]
    assert _get(pid)["full_name"] == "Dana Halloway"


def test_surrounding_and_repeated_whitespace_is_normalized():
    actor = _actor()
    pid = _person(full_name=None)
    changed = correct_person_identity(pid, full_name="  Dana\t\t  Halloway \n", actor_user_id=actor)
    assert changed == ["full_name"]
    assert _get(pid)["full_name"] == "Dana Halloway", "internal runs must collapse to one space"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n  ", None])
def test_a_blank_name_is_refused(blank):
    actor = _actor()
    pid = _person(full_name="Keep This Name")
    with pytest.raises(ValueError, match="blank"):
        correct_person_identity(pid, full_name=blank, actor_user_id=actor)
    assert _get(pid)["full_name"] == "Keep This Name", "a refused correction must not write"


def test_correcting_to_the_same_value_is_a_no_op():
    actor = _actor()
    pid = _person(full_name="Dana Halloway")
    assert correct_person_identity(pid, full_name="  Dana   Halloway  ", actor_user_id=actor) == []
    assert _timeline(pid) == []
    assert _audit(pid) == []
    assert _outbox(pid) == [], "an unchanged value must not emit a FACT"


def test_an_unknown_person_raises_the_same_error_as_the_contact_service():
    actor = _actor()
    missing = 9_000_000 + int(uuid.uuid4().int % 100_000)
    with pytest.raises(ValueError, match="Person not found."):
        correct_person_identity(missing, full_name="Dana Halloway", actor_user_id=actor)


def test_an_actor_is_required():
    actor = _actor()
    pid = _person(full_name=None)
    with pytest.raises(ValueError, match="actor_user_id"):
        correct_person_identity(pid, full_name="Dana Halloway", actor_user_id=None)
    assert _get(pid)["full_name"] is None, "an unattributed correction must not write"


# --- governance ----------------------------------------------------------------------------

def test_the_outbox_carries_people_person_updated_naming_full_name():
    actor = _actor()
    pid = _person(full_name=None)
    correct_person_identity(pid, full_name="Dana Halloway", actor_user_id=actor)
    payloads = _outbox(pid)
    assert len(payloads) == 1
    assert payloads[0]["changed_fields"] == ["full_name"]


def test_the_timeline_identifies_this_as_a_manual_identity_correction():
    actor = _actor()
    pid = _person(full_name=None)
    correct_person_identity(pid, full_name="Dana Halloway", actor_user_id=actor,
                            reason="Merged record review")
    rows = _timeline(pid)
    assert len(rows) == 1
    assert rows[0]["event_metadata"]["correction"] == "identity"
    assert rows[0]["event_metadata"]["actor_user_id"] == actor
    assert "Merged record review" in rows[0]["summary"], "the reason must be preserved"


def test_the_audit_names_the_field_but_never_the_value():
    actor = _actor()
    pid = _person(full_name=None)
    correct_person_identity(pid, full_name="Dana Halloway", actor_user_id=actor,
                            reason="Merged record review")
    rows = _audit(pid)
    assert len(rows) == 1
    assert rows[0]["action"] == "person.identity_corrected"
    assert rows[0]["actor_user_id"] == actor
    assert rows[0]["metadata"]["fields"] == ["full_name"]
    assert rows[0]["metadata"]["reason_provided"] is True
    blob = str(rows[0]["metadata"])
    assert "Dana" not in blob and "Merged record review" not in blob, \
        "the audit trail records field NAMES only — no values, no free text"


def test_a_rolled_back_transaction_leaves_no_outbox_timeline_or_audit_trace():
    actor = _actor()
    pid = _person(full_name=None)
    with pytest.raises(RuntimeError):
        with engine.begin() as conn:
            correct_person_identity(pid, full_name="Dana Halloway", actor_user_id=actor, conn=conn)
            raise RuntimeError("caller aborts after the correction")
    assert _get(pid)["full_name"] is None, "the row write must roll back"
    assert _outbox(pid) == [], "the outbox write is transactional and must roll back"
    assert _timeline(pid) == [], "no timeline row may survive a rolled-back correction"
    assert _audit(pid) == [], "no audit row may survive a rolled-back correction"


# --- the other two edit paths still cannot reach the field --------------------------------

def test_the_contact_service_still_ignores_full_name():
    actor = _actor()
    assert "full_name" not in EDITABLE_FIELDS
    pid = _person(full_name="Original Name")
    changed = update_person_contact(pid, {"full_name": "Injected Name", "city": "Springfield"},
                                    actor_user_id=actor)
    assert changed == ["city"]
    assert _get(pid)["full_name"] == "Original Name", \
        "the contact form must never be able to rewrite identity"


def test_the_client_portal_still_cannot_change_full_name():
    from app.portal import profile as portal_profile
    assert "full_name" not in portal_profile._PERSON_FIELDS
    assert "full_name" not in portal_profile._ALIASES.values()


# --- merge interaction: the case that motivated this work ----------------------------------

def test_a_corrected_name_is_not_refilled_from_the_duplicate_and_survives_the_merge():
    actor = _actor()
    survivor = _person(full_name=None, first_name="Dana", last_name="Halloway")
    duplicate = _person(full_name="Dan Halloway", first_name="Dan", last_name="Halloway")

    before = preview_person_merge(survivor, duplicate)
    assert before["profile_fields_would_fill"].get("full_name") == "Dan Halloway", \
        "precondition: an uncorrected survivor WOULD inherit the duplicate's name"

    correct_person_identity(survivor, full_name="Dana Halloway", actor_user_id=actor,
                            reason="Correct canonical spelling before merge")

    after = preview_person_merge(survivor, duplicate)
    assert "full_name" not in after["profile_fields_would_fill"], \
        "a populated canonical name must no longer be proposed for fill"

    merge_people(survivor, duplicate, reason="synthetic identity-correction test",
                 actor_user_id=actor)
    assert _get(survivor)["full_name"] == "Dana Halloway", \
        "the merge must preserve the corrected canonical name"


# --- projection consistency ----------------------------------------------------------------

def test_the_correction_reaches_rm_people_summary_through_the_existing_projection():
    actor = _actor()
    from app.services.projections import engine as projection_engine
    pid = _person(full_name=None)
    correct_person_identity(pid, full_name="Dana Halloway", actor_user_id=actor)

    # The service does not drain synchronously (matching update_person_contact); the FACT sits in
    # the outbox until the projection runs, exactly as for an ordinary contact edit.
    projection_engine.process("people.summary")

    with engine.connect() as c:
        row = c.execute(text("SELECT * FROM rm_people_summary WHERE person_id = :p"),
                        {"p": pid}).mappings().one_or_none()
    assert row is not None, "the subscribed event must materialize the read model row"
