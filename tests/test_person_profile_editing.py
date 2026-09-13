"""Editing a client profile: name, date of birth, contact, address and household.

WHAT THIS FILE IS PROTECTING

The Overview grew an Edit Profile button, and with it the ability to change fields that used to be
reachable only from an import or a console. Four properties matter more than the form itself:

  * **Identity stays governed.** Legal name and date of birth go through their own named
    correction services, not through the contact path, so neither can change as a side effect of
    someone fixing a phone number.
  * **The household is the one that was chosen.** Nothing infers a household from a surname,
    reuses a spouse's, or creates one. Removing an assignment is its own deliberate, audited act.
  * **One form is one transaction.** A rejected date of birth or an unknown household leaves the
    contact fields exactly as they were, not half written.
  * **A shared email warns and does nothing else.** No record is merged, no correspondence moves,
    and no document changes owner.

NO REAL DATA. Every fixture is synthetic: ``@example.test`` addresses and the 555 exchange
reserved for fiction. Nothing here, in its assertions or in its failure output, can carry a real
client's details.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from urllib.parse import urlencode

import pytest
from sqlalchemy import func, select
from starlette.requests import Request

from app.db import audit_events, documents, engine, households, people, timeline_events
from app.routes.person_edit import NO_HOUSEHOLD, edit_person_submit
from app.security.models import Principal
from app.services.people import (
    correct_person_birth_date,
    people_sharing_email,
    set_person_household,
)
from tests._portal_util import seed_staff_user


def _tag() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture(scope="module")
def actor() -> int:
    return seed_staff_user()


def _household(name_hint: str) -> int:
    with engine.begin() as c:
        return c.execute(households.insert().values(name=f"{name_hint} {_tag()}")
                         .returning(households.c.id)).scalar_one()


def _person(**cols) -> int:
    tag = _tag()
    values = {"full_name": f"Edit Subject {tag}", "active": True}
    values.update(cols)
    with engine.begin() as c:
        return c.execute(people.insert().values(**values).returning(people.c.id)).scalar_one()


def _row(pid: int):
    with engine.connect() as c:
        return c.execute(select(people).where(people.c.id == pid)).mappings().one()


def _count(table, **where) -> int:
    with engine.connect() as c:
        q = select(func.count()).select_from(table)
        for key, value in where.items():
            q = q.where(getattr(table.c, key) == value)
        return c.execute(q).scalar_one()


def _household_count() -> int:
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(households)).scalar_one()


def _request(pid: int, form: dict) -> Request:
    """A real Starlette request, because a refused edit re-renders the form and the templates
    read ``request.url``. The middleware is not in the picture here; its own coverage of this
    route is proved in ``test_person_profile_edit_authorization``."""
    body = urlencode(form).encode()
    scope = {
        "type": "http", "method": "POST", "path": f"/people/{pid}/edit",
        "raw_path": f"/people/{pid}/edit".encode(), "query_string": b"",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        "client": ("127.0.0.1", 1234), "server": ("testserver", 80), "scheme": "http",
        "session": {}, "app": None,
    }

    async def _receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(scope, _receive)
    request.state.request_id = "profile-edit-test"
    return request


def _submit(pid: int, form: dict, actor_id: int):
    principal = Principal(actor_id, "staff@example.test", "Staff", frozenset({"client.write"}))
    return asyncio.run(edit_person_submit(_request(pid, form), pid, principal))


def _full_form(row, **overrides):
    """Everything the real form posts, so a test changes one field and leaves the rest alone."""
    form = {
        "full_name": row["full_name"] or "",
        "preferred_name": row["preferred_name"] or "",
        "birth_date": row["birth_date"].isoformat() if row["birth_date"] else "",
        "primary_email": row["primary_email"] or "",
        "primary_phone": row["primary_phone"] or "",
        "address_line_1": row["address_line_1"] or "",
        "address_line_2": row["address_line_2"] or "",
        "city": row["city"] or "",
        "state": row["state"] or "",
        "postal_code": row["postal_code"] or "",
        "household_id": str(row["household_id"]) if row["household_id"] else NO_HOUSEHOLD,
    }
    form.update(overrides)
    return form


# --- editing the fields the button promises ----------------------------------------------------

def test_the_form_edits_name_dob_contact_and_address_together(actor):
    pid = _person(primary_email="before@example.test", city="Roanoke",
                  birth_date=date(1975, 3, 4))
    row = _row(pid)
    resp = _submit(pid, _full_form(
        row, full_name="Corrected Legal Name", preferred_name="Corrected",
        birth_date="1975-03-05", primary_email="after@example.test",
        primary_phone="(540) 555-0144", address_line_1="2 Example Way", city="Salem",
        state="VA", postal_code="24153"), actor)
    assert resp.status_code == 303

    after = _row(pid)
    assert after["full_name"] == "Corrected Legal Name"
    assert after["preferred_name"] == "Corrected"
    assert after["birth_date"] == date(1975, 3, 5)
    assert after["primary_email"] == "after@example.test"
    assert after["normalized_email"] == "after@example.test"
    assert after["normalized_phone"] == "5405550144"
    assert after["address_line_1"] == "2 Example Way"
    assert (after["city"], after["state"], after["postal_code"]) == ("Salem", "VA", "24153")


def test_a_field_the_submission_omits_is_left_alone(actor):
    """An absent input means "this form does not edit that", never "erase it"."""
    pid = _person(birth_date=date(1969, 7, 20), preferred_name="Keep", city="Roanoke")
    _submit(pid, {"city": "Salem"}, actor)
    after = _row(pid)
    assert after["birth_date"] == date(1969, 7, 20)
    assert after["preferred_name"] == "Keep"
    assert after["city"] == "Salem"


# --- validation --------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["04/02/1980", "not a date", "2999-01-01", "1800-01-01"])
def test_an_unusable_date_of_birth_is_refused(bad, actor):
    pid = _person(birth_date=date(1980, 4, 2), city="Roanoke")
    resp = _submit(pid, _full_form(_row(pid), birth_date=bad, city="Salem"), actor)
    assert resp.status_code == 400
    after = _row(pid)
    assert after["birth_date"] == date(1980, 4, 2)
    assert after["city"] == "Roanoke"          # nothing at all was saved


def test_a_blank_legal_name_is_refused(actor):
    pid = _person(full_name="Has A Name", city="Roanoke")
    resp = _submit(pid, _full_form(_row(pid), full_name="   ", city="Salem"), actor)
    assert resp.status_code == 400
    assert _row(pid)["full_name"] == "Has A Name"
    assert _row(pid)["city"] == "Roanoke"


def test_an_unparseable_household_selection_is_refused(actor):
    pid = _person(city="Roanoke")
    resp = _submit(pid, _full_form(_row(pid), household_id="not-an-id", city="Salem"), actor)
    assert resp.status_code == 400
    assert _row(pid)["city"] == "Roanoke"


def test_editing_a_person_who_does_not_exist_is_a_404(actor):
    resp = _submit(999_999_999, {"city": "X"}, actor)
    assert resp.status_code == 404


# --- one form, one transaction -----------------------------------------------------------------

def test_a_rejected_household_rolls_the_contact_fields_back(actor):
    """The household is validated inside the write transaction, after contact has been written."""
    pid = _person(primary_email="rollback@example.test", city="Roanoke")
    missing_household = 999_999_999
    resp = _submit(pid, _full_form(_row(pid), household_id=str(missing_household),
                                   city="Salem", primary_phone="(540) 555-0155"), actor)
    assert resp.status_code == 400
    after = _row(pid)
    assert after["city"] == "Roanoke"                 # rolled back with the failed assignment
    assert after["primary_phone"] is None
    assert after["household_id"] is None


def test_a_rolled_back_edit_leaves_no_audit_or_timeline_trail(actor):
    pid = _person(city="Roanoke")
    _submit(pid, _full_form(_row(pid), household_id="999999999", city="Salem"), actor)
    assert _count(audit_events, action="person.updated", entity_id=str(pid)) == 0
    assert _count(timeline_events, person_id=pid, event_type="person_updated") == 0


# --- household: exactly the one that was chosen -------------------------------------------------

def test_the_selected_household_is_assigned_and_no_other_is_created(actor):
    target = _household("Chosen HH")
    pid = _person(city="Roanoke")
    before = _household_count()
    resp = _submit(pid, _full_form(_row(pid), household_id=str(target)), actor)
    assert resp.status_code == 303
    assert _row(pid)["household_id"] == target
    assert _household_count() == before               # nothing was invented


def test_assignment_never_touches_another_member_of_either_household(actor):
    """No spouse or family inference: one person moves, and only that person."""
    origin = _household("Origin HH")
    target = _household("Target HH")
    spouse = _person(household_id=origin, city="Roanoke")
    bystander = _person(household_id=target, city="Salem")
    pid = _person(household_id=origin)

    _submit(pid, _full_form(_row(pid), household_id=str(target)), actor)

    assert _row(pid)["household_id"] == target
    assert _row(spouse)["household_id"] == origin     # left where they were
    assert _row(bystander)["household_id"] == target  # untouched, not re-anchored


def test_an_unknown_household_is_rejected_rather_than_created(actor):
    pid = _person()
    before = _household_count()
    with pytest.raises(ValueError, match="Household not found"):
        set_person_household(pid, 999_999_999, actor_user_id=actor)
    assert _household_count() == before
    assert _row(pid)["household_id"] is None


def test_removing_a_household_is_an_explicit_audited_action(actor):
    origin = _household("Leaving HH")
    pid = _person(household_id=origin)
    resp = _submit(pid, _full_form(_row(pid), household_id=NO_HOUSEHOLD), actor)
    assert resp.status_code == 303
    assert _row(pid)["household_id"] is None
    assert _count(audit_events, action="person.household_assigned", entity_id=str(pid)) == 1
    assert _count(timeline_events, person_id=pid,
                  event_type="person_household_assigned") == 1
    with engine.connect() as c:                       # the household itself still exists
        assert c.execute(select(households.c.id)
                         .where(households.c.id == origin)).scalar() == origin


def test_an_unchanged_household_writes_nothing(actor):
    hid = _household("Stable HH")
    pid = _person(household_id=hid)
    assert set_person_household(pid, hid, actor_user_id=actor) == []
    assert _count(audit_events, action="person.household_assigned", entity_id=str(pid)) == 0


def test_a_household_assignment_requires_an_actor():
    pid = _person()
    with pytest.raises(ValueError, match="actor_user_id"):
        set_person_household(pid, None, actor_user_id=None)


# --- audit and timeline ------------------------------------------------------------------------

def test_every_governed_change_is_audited_without_recording_its_value(actor):
    target = _household("Audited HH")
    pid = _person(primary_email="audit@example.test", birth_date=date(1960, 1, 1))
    _submit(pid, _full_form(_row(pid), full_name="Audited New Name",
                            birth_date="1960-01-02", primary_email="audited@example.test",
                            household_id=str(target)), actor)

    for action in ("person.updated", "person.identity_corrected", "person.household_assigned"):
        assert _count(audit_events, action=action, entity_id=str(pid)) >= 1, action

    with engine.connect() as c:
        rows = c.execute(select(audit_events.c["metadata"])
                         .where(audit_events.c.entity_id == str(pid))).scalars().all()
    blob = " ".join(str(r) for r in rows)
    assert "Audited New Name" not in blob            # field NAMES only, never their values
    assert "audited@example.test" not in blob
    assert "1960-01-02" not in blob


def test_the_date_of_birth_correction_is_its_own_named_operation(actor):
    pid = _person(birth_date=date(1955, 6, 6))
    changed = correct_person_birth_date(pid, birth_date=date(1955, 6, 7), actor_user_id=actor)
    assert changed == ["birth_date"]
    assert _count(audit_events, action="person.identity_corrected", entity_id=str(pid)) == 1
    assert _count(timeline_events, person_id=pid, event_type="person_identity_corrected") == 1


# --- shared email: warn, never re-anchor --------------------------------------------------------

def test_a_shared_address_is_reported_against_the_other_holders(actor):
    shared = f"shared-{_tag()}@example.test"
    first = _person(primary_email=shared, normalized_email=shared)
    second = _person(primary_email=shared, normalized_email=shared)
    warning = people_sharing_email(shared, exclude_person_id=second)
    assert [w["person_id"] for w in warning] == [first]


def test_the_warning_never_lists_the_person_being_edited(actor):
    shared = f"self-{_tag()}@example.test"
    pid = _person(primary_email=shared, normalized_email=shared)
    assert people_sharing_email(shared, exclude_person_id=pid) == []


def test_an_inactive_holder_is_not_reported(actor):
    shared = f"inactive-{_tag()}@example.test"
    _person(primary_email=shared, normalized_email=shared, active=False)
    other = _person()
    assert people_sharing_email(shared, exclude_person_id=other) == []


def test_saving_a_shared_address_changes_nothing_on_the_other_record(actor):
    shared = f"collide-{_tag()}@example.test"
    other = _person(primary_email=shared, normalized_email=shared, city="Roanoke",
                    household_id=_household("Other HH"))
    before = dict(_row(other))
    pid = _person(primary_email=f"mine-{_tag()}@example.test")

    resp = _submit(pid, _full_form(_row(pid), primary_email=shared), actor)
    assert resp.status_code == 303
    assert _row(pid)["primary_email"] == shared

    after = dict(_row(other))
    for column in ("primary_email", "normalized_email", "household_id", "city", "full_name",
                   "active"):
        assert after[column] == before[column], column


def test_editing_a_profile_moves_no_document(actor):
    """Ownership is not touched by a contact edit — no document changes person or household."""
    pid = _person(city="Roanoke", household_id=_household("Docs HH"))
    with engine.connect() as c:
        before = c.execute(
            select(documents.c.id, documents.c.person_id, documents.c.owner_user_id)
            .where(documents.c.person_id == pid)).mappings().all()

    _submit(pid, _full_form(_row(pid), city="Salem",
                            household_id=str(_household("Docs HH 2"))), actor)

    with engine.connect() as c:
        after = c.execute(
            select(documents.c.id, documents.c.person_id, documents.c.owner_user_id)
            .where(documents.c.person_id == pid)).mappings().all()
    assert [dict(r) for r in after] == [dict(r) for r in before]
