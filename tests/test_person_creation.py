"""Creating a canonical client from staff-entered details — the reusable service.

Client360 had no staff-facing person-creation path: people arrived only through ingestion, so a
client who had never appeared in an import could not be created and the portal invite screen
dead-ended. ``app.services.person_creation`` is that path, deliberately built on the same shape as
``prospect_import.create_prospect`` rather than as a tenth ad-hoc ``people.insert()``.

What these tests hold in place: the canonical normalizers (not one of the other eight
``normalize_email`` functions in the repo), an explicit ``active=True`` (the column is nullable with
no default and every search filters on it), a NULL ``contact_type``, hard duplicate refusal that
works even when the duplicate is invisible to the creator, a name-collision warning that needs a
second explicit acknowledgement, a household created through the supported service, and the record
assignment without which the creator could not invite the client they just typed in.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.db import (
    audit_events,
    engine,
    household_relationships,
    households,
    people,
    record_assignments,
    users,
)
from app.security.identity_utils import normalize_email
from app.security.models import Principal
from app.services import person_creation as PC
from app.services.people import _normalize_phone


def _unique_phone(style="parens"):
    """A phone unique to one test. The test database accumulates rows across runs, so a shared
    number would collide with an earlier run's person and trip the hard-duplicate block."""
    d = "540" + f"{uuid.uuid4().int % 10_000_000:07d}"
    return (d if style == "bare" else f"({d[:3]}) {d[3:6]}-{d[6:]}"), d


def _staff(caps=("client.read", "client.write")) -> Principal:
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"staff-{sfx}@example.com", normalized_email=f"staff-{sfx}@example.com",
            display_name="Creating Staff", auth_subject=f"staff-{sfx}", status="active")
            .returning(users.c.id)).scalar_one()
    return Principal(uid, f"staff-{sfx}@example.com", "Creating Staff", frozenset(caps))


def _existing(sfx, *, first="Existing", last=None, email=None, phone=None, active=True,
              household_id=None):
    last = last or f"Person{sfx}"
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last}",
            primary_email=email, normalized_email=normalize_email(email) if email else None,
            primary_phone=phone, normalized_phone=_normalize_phone(phone),
            active=active, household_id=household_id).returning(people.c.id)).scalar_one()


def _row(person_id):
    with engine.connect() as c:
        return c.execute(select(people).where(people.c.id == person_id)).mappings().one()


# --- authorization ------------------------------------------------------------------------

def test_creating_requires_client_write():
    reader = _staff(caps=("client.read",))
    with pytest.raises(PC.PersonCreationError, match="client.write"):
        PC.create_client(reader, first_name="Nope", last_name=f"Denied{uuid.uuid4().hex[:6]}",
                         email="nope@example.com")


def test_a_refused_creation_writes_no_person():
    sfx = uuid.uuid4().hex[:8]
    reader = _staff(caps=("client.read",))
    with pytest.raises(PC.PersonCreationError):
        PC.create_client(reader, first_name="Nope", last_name=f"Denied{sfx}",
                         email=f"n-{sfx}@example.com")
    with engine.connect() as c:
        assert c.execute(select(people.c.id)
                         .where(people.c.last_name == f"Denied{sfx}")).fetchall() == []


# --- required data ------------------------------------------------------------------------

def test_first_name_is_required():
    with pytest.raises(PC.PersonCreationError, match="first name"):
        PC.create_client(_staff(), first_name="  ", last_name="Shelton", email="a@example.com")


def test_last_name_is_required():
    with pytest.raises(PC.PersonCreationError, match="last name"):
        PC.create_client(_staff(), first_name="Michael", last_name="", email="a@example.com")


def test_the_service_requires_an_email_or_a_phone():
    with pytest.raises(PC.PersonCreationError, match="email address or a phone number"):
        PC.create_client(_staff(), first_name="Michael", last_name=f"NoContact{uuid.uuid4().hex[:6]}")


def test_the_service_accepts_a_phone_with_no_email():
    sfx = uuid.uuid4().hex[:8]
    phone, digits = _unique_phone()
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"PhoneOnly{sfx}",
                               phone=phone)
    assert created.email == "" and created.phone == phone
    assert _row(created.person_id)["normalized_phone"] == digits


def test_the_portal_flavour_requires_an_email():
    """The portal cannot send an invitation without one, so it asks for a stricter contract."""
    sfx = uuid.uuid4().hex[:8]
    with pytest.raises(PC.PersonCreationError, match="email address is required to invite"):
        PC.create_client(_staff(), first_name="Michael", last_name=f"NoMail{sfx}",
                         phone=_unique_phone("bare")[0], require_email=True)


def test_phone_stays_optional_in_the_portal_flavour():
    sfx = uuid.uuid4().hex[:8]
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"MailOnly{sfx}",
                               email=f"m-{sfx}@example.com", require_email=True)
    assert created.phone == "" and _row(created.person_id)["normalized_phone"] is None


# --- the stored record --------------------------------------------------------------------

def test_the_new_person_is_explicitly_active():
    """The column is nullable with NO default, and every search filters `active IS TRUE`."""
    sfx = uuid.uuid4().hex[:8]
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"Active{sfx}",
                               email=f"a-{sfx}@example.com")
    assert _row(created.person_id)["active"] is True


def test_contact_type_is_left_null():
    """No new lifecycle taxonomy is invented; provenance lives in the audit event."""
    sfx = uuid.uuid4().hex[:8]
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"Type{sfx}",
                               email=f"t-{sfx}@example.com")
    assert _row(created.person_id)["contact_type"] is None


def test_normalisation_uses_the_canonical_helpers():
    sfx = uuid.uuid4().hex[:8]
    typed_email = f"MiXeD.Case-{sfx}@Example.COM"                 # unique: the DB accumulates rows
    digits = "540" + f"{uuid.uuid4().int % 10_000_000:07d}"
    typed_phone = f" ({digits[:3]}) {digits[3:6]}-{digits[6:]} "
    created = PC.create_client(_staff(), first_name="  Michael ", last_name=f" Norm{sfx} ",
                               email=f"  {typed_email}  ", phone=typed_phone)
    row = _row(created.person_id)
    assert row["primary_email"] == typed_email                    # stored as typed
    assert row["normalized_email"] == normalize_email(typed_email)
    assert row["normalized_phone"] == _normalize_phone(typed_phone) == digits
    assert row["first_name"] == "Michael" and row["last_name"] == f"Norm{sfx}"
    assert row["full_name"] == f"Michael Norm{sfx}"


def test_the_canonical_email_normalizer_is_used_not_the_matching_variant():
    """`matching.matcher.normalize_email` strips gmail dots; that is a MATCHING heuristic and is
    not what any other writer stores. Using it here would break duplicate detection."""
    from app.matching.matcher import normalize_email as matching_variant

    sfx = uuid.uuid4().hex[:8]
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"Gmail{sfx}",
                               email=f"first.last+tag-{sfx}@gmail.com")
    stored = _row(created.person_id)["normalized_email"]
    assert stored == normalize_email(f"first.last+tag-{sfx}@gmail.com")
    assert stored != matching_variant(f"first.last+tag-{sfx}@gmail.com")


def test_created_by_and_updated_by_are_recorded():
    sfx = uuid.uuid4().hex[:8]
    staff = _staff()
    created = PC.create_client(staff, first_name="Michael", last_name=f"By{sfx}",
                               email=f"b-{sfx}@example.com")
    row = _row(created.person_id)
    assert row["created_by_user_id"] == staff.user_id
    assert row["updated_by_user_id"] == staff.user_id


# --- duplicates: hard blocks ----------------------------------------------------------------

def test_an_exact_email_duplicate_hard_blocks_creation():
    sfx = uuid.uuid4().hex[:8]
    email = f"dup-{sfx}@example.com"
    _existing(sfx, first="Someone", email=email)
    with pytest.raises(PC.DuplicateClientError, match="already exists"):
        PC.create_client(_staff(caps=("client.write", "record.read_all")),
                         first_name="Michael", last_name=f"Fresh{sfx}", email=email)
    with engine.connect() as c:
        assert c.execute(select(people.c.id)
                         .where(people.c.last_name == f"Fresh{sfx}")).fetchall() == []


def test_an_exact_phone_duplicate_hard_blocks_creation():
    sfx = uuid.uuid4().hex[:8]
    digits = "540" + f"{uuid.uuid4().int % 10_000_000:07d}"
    _existing(sfx, first="Someone", phone=f"({digits[:3]}) {digits[3:6]}-{digits[6:]}")
    with pytest.raises(PC.DuplicateClientError):
        PC.create_client(_staff(caps=("client.write", "record.read_all")),
                         first_name="Michael", last_name=f"Fresh{sfx}",
                         email=f"f-{sfx}@example.com", phone=digits)


def test_a_hard_duplicate_is_matched_however_the_phone_is_punctuated():
    sfx = uuid.uuid4().hex[:8]
    digits = "540" + f"{uuid.uuid4().int % 10_000_000:07d}"
    _existing(sfx, first="Someone", phone=digits)
    with pytest.raises(PC.DuplicateClientError):
        PC.create_client(_staff(caps=("client.write", "record.read_all")),
                         first_name="Michael", last_name=f"Punct{sfx}",
                         email=f"p-{sfx}@example.com",
                         phone=f"({digits[:3]}) {digits[3:6]}-{digits[6:]}")


def test_an_out_of_scope_hard_duplicate_blocks_without_revealing_anything():
    """Duplicate protection is a data-integrity rule, not a visibility rule — but the refusal must
    not leak a record the creator may not see."""
    sfx = uuid.uuid4().hex[:8]
    email = f"hidden-{sfx}@example.com"
    hidden_phone = _unique_phone("bare")[0]
    _existing(sfx, first="Confidential", last=f"Hidden{sfx}", email=email, phone=hidden_phone)
    scoped_out = _staff(caps=("client.read", "client.write"))       # no record.read_all
    with pytest.raises(PC.DuplicateClientError) as exc:
        PC.create_client(scoped_out, first_name="Michael", last_name=f"Blocked{sfx}", email=email)
    message = str(exc.value)
    assert message == PC.OUT_OF_SCOPE_DUPLICATE
    for leak in ("Confidential", f"Hidden{sfx}", email, hidden_phone):
        assert leak not in message, f"an out-of-scope record leaked: {leak}"
    with engine.connect() as c:
        assert c.execute(select(people.c.id)
                         .where(people.c.last_name == f"Blocked{sfx}")).fetchall() == []


# --- duplicates: name-only warning + explicit override ---------------------------------------

def test_an_exact_name_match_warns_instead_of_creating():
    sfx = uuid.uuid4().hex[:8]
    _existing(sfx, first="Michael", last=f"Shelton{sfx}", email=f"other-{sfx}@example.com")
    staff = _staff(caps=("client.write", "record.read_all"))
    with pytest.raises(PC.PossibleDuplicateWarning) as exc:
        PC.create_client(staff, first_name="Michael", last_name=f"Shelton{sfx}",
                         email=f"new-{sfx}@example.com")
    assert "Review them" in str(exc.value)
    assert exc.value.candidates and exc.value.candidates[0]["full_name"] == f"Michael Shelton{sfx}"
    with engine.connect() as c:
        assert len(c.execute(select(people.c.id)
                             .where(people.c.last_name == f"Shelton{sfx}")).fetchall()) == 1


def test_the_warning_is_cleared_only_by_an_explicit_acknowledgement():
    sfx = uuid.uuid4().hex[:8]
    _existing(sfx, first="Michael", last=f"Shelton{sfx}", email=f"other-{sfx}@example.com")
    staff = _staff(caps=("client.write", "record.read_all"))
    created = PC.create_client(staff, first_name="Michael", last_name=f"Shelton{sfx}",
                               email=f"new-{sfx}@example.com", acknowledge_name_duplicate=True)
    assert created.person_id
    with engine.connect() as c:
        assert len(c.execute(select(people.c.id)
                             .where(people.c.last_name == f"Shelton{sfx}")).fetchall()) == 2


def test_a_name_match_on_a_contact_less_record_is_a_warning_not_a_block():
    """A first+last collision is never conclusive: real people share names. A record with no email
    and no phone is LESS corroborated, not more, so it must warn and stay overridable."""
    sfx = uuid.uuid4().hex[:8]
    _existing(sfx, first="Michael", last=f"Bare{sfx}")              # no email, no phone
    staff = _staff(caps=("client.write", "record.read_all"))
    with pytest.raises(PC.PossibleDuplicateWarning) as exc:
        PC.create_client(staff, first_name="Michael", last_name=f"Bare{sfx}",
                         email=f"n-{sfx}@example.com")
    assert "Review them" in str(exc.value)
    assert exc.value.candidates and exc.value.candidates[0]["full_name"] == f"Michael Bare{sfx}"
    with engine.connect() as c:
        assert len(c.execute(select(people.c.id)
                             .where(people.c.last_name == f"Bare{sfx}")).fetchall()) == 1


def test_the_contact_less_name_match_can_be_overridden_like_any_other():
    sfx = uuid.uuid4().hex[:8]
    _existing(sfx, first="Michael", last=f"BareOk{sfx}")            # no email, no phone
    staff = _staff(caps=("client.write", "record.read_all"))
    created = PC.create_client(staff, first_name="Michael", last_name=f"BareOk{sfx}",
                               email=f"n-{sfx}@example.com", acknowledge_name_duplicate=True)
    assert created.person_id
    with engine.connect() as c:
        assert len(c.execute(select(people.c.id)
                             .where(people.c.last_name == f"BareOk{sfx}")).fetchall()) == 2


def test_no_name_only_condition_is_ever_a_hard_block():
    """Only the exact identifier matches are non-overridable."""
    import inspect
    body = inspect.getsource(PC.create_client).split("name_rows =")[1]
    assert "DuplicateClientError" not in body, "a name-only hard block was reintroduced"
    assert "PossibleDuplicateWarning" in body


def test_the_warning_candidates_are_scoped_to_what_the_creator_may_see():
    sfx = uuid.uuid4().hex[:8]
    _existing(sfx, first="Michael", last=f"Scoped{sfx}", email=f"o-{sfx}@example.com")
    unscoped = _staff(caps=("client.read", "client.write"))         # sees nobody
    with pytest.raises(PC.PossibleDuplicateWarning) as exc:
        PC.create_client(unscoped, first_name="Michael", last_name=f"Scoped{sfx}",
                         email=f"n-{sfx}@example.com")
    assert exc.value.candidates == [], "an out-of-scope candidate was shown to staff"


def test_duplicate_checks_run_again_inside_the_write_transaction():
    import inspect
    src = inspect.getsource(PC.create_client)
    body = src.split("with engine.begin() as connection:")[1]
    assert "_hard_duplicate_ids(connection" in body, "the hard check is not transactional"
    assert "_name_match_rows(connection" in body, "the name check is not transactional"
    assert body.index("_hard_duplicate_ids(connection") < body.index("people.insert()")


def test_an_inactive_person_does_not_block_creation():
    sfx = uuid.uuid4().hex[:8]
    email = f"gone-{sfx}@example.com"
    _existing(sfx, first="Retired", email=email, active=False)
    created = PC.create_client(_staff(caps=("client.write", "record.read_all")),
                               first_name="Michael", last_name=f"Live{sfx}", email=email)
    assert created.person_id


# --- household ----------------------------------------------------------------------------

def test_a_new_person_gets_their_own_household_through_the_supported_service():
    sfx = uuid.uuid4().hex[:8]
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"House{sfx}",
                               email=f"h-{sfx}@example.com")
    row = _row(created.person_id)
    assert row["household_id"] is not None, "people.household_id was not set"
    with engine.connect() as c:
        name = c.scalar(select(households.c.name).where(households.c.id == row["household_id"]))
        members = c.execute(select(household_relationships.c.person_id)
                            .where(household_relationships.c.household_id == row["household_id"])
                            ).scalars().all()
    assert name == f"House{sfx} Household", "the established naming convention was not used"
    assert members == [created.person_id], "the household_relationships row was not written"
    assert created.household_created is True


def test_a_matching_surname_never_attaches_the_new_person_to_an_existing_household():
    """Households are not matched by last name and family relationships are never guessed."""
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        other_hh = c.execute(households.insert().values(name=f"Shelton{sfx} Household")
                             .returning(households.c.id)).scalar_one()
    _existing(sfx, first="Dana", last=f"Shelton{sfx}", email=f"d-{sfx}@example.com",
              household_id=other_hh)
    created = PC.create_client(_staff(caps=("client.write", "record.read_all")),
                               first_name="Michael", last_name=f"Shelton{sfx}",
                               email=f"m-{sfx}@example.com", acknowledge_name_duplicate=True)
    assert _row(created.person_id)["household_id"] != other_hh, "auto-joined by surname"


def test_household_creation_goes_through_the_household_service_not_local_sql():
    import inspect
    src = inspect.getsource(PC)
    assert "assign_people_to_household" in src
    assert "households.insert()" not in src, "the service writes households directly"


# --- record assignment (the scope the creator needs next) --------------------------------------

def test_the_creator_is_assigned_to_the_new_person():
    sfx = uuid.uuid4().hex[:8]
    staff = _staff()                                  # client.write only, NO record.write_all
    created = PC.create_client(staff, first_name="Michael", last_name=f"Assign{sfx}",
                               email=f"as-{sfx}@example.com")
    with engine.connect() as c:
        rows = c.execute(select(record_assignments.c.assignment_type)
                         .where(record_assignments.c.user_id == staff.user_id,
                                record_assignments.c.entity_type == "person",
                                record_assignments.c.entity_id == created.person_id)
                         ).scalars().all()
    assert rows == ["primary"]


def test_the_creator_immediately_holds_write_record_scope():
    """Without this the very next step — resolve_invite_target — would refuse the client they
    just typed in themselves."""
    from app.security.authorization import record_in_scope

    sfx = uuid.uuid4().hex[:8]
    staff = _staff()                                  # no record.write_all
    created = PC.create_client(staff, first_name="Michael", last_name=f"Scope{sfx}",
                               email=f"sc-{sfx}@example.com")
    assert record_in_scope(staff, "person", created.person_id, write=True) is True


def test_the_creator_is_assigned_to_the_household_this_workflow_created():
    """This workflow created that household, so the creator must be able to open it on the
    surfaces that authorize household records separately."""
    sfx = uuid.uuid4().hex[:8]
    staff = _staff()                                  # client.write only
    created = PC.create_client(staff, first_name="Michael", last_name=f"HHAssign{sfx}",
                               email=f"hha-{sfx}@example.com")
    household_id = _row(created.person_id)["household_id"]
    with engine.connect() as c:
        rows = c.execute(select(record_assignments.c.assignment_type).where(
            record_assignments.c.user_id == staff.user_id,
            record_assignments.c.entity_type == "household",
            record_assignments.c.entity_id == household_id)).scalars().all()
    assert rows == ["primary"]


def test_the_creator_can_read_and_write_the_new_household_through_normal_policy():
    from app.security.authorization import record_in_scope

    sfx = uuid.uuid4().hex[:8]
    staff = _staff()                                  # no record.read_all, no record.write_all
    created = PC.create_client(staff, first_name="Michael", last_name=f"HHScope{sfx}",
                               email=f"hhs-{sfx}@example.com")
    household_id = _row(created.person_id)["household_id"]
    assert record_in_scope(staff, "household", household_id) is True
    assert record_in_scope(staff, "household", household_id, write=True) is True


def test_no_pre_existing_household_gains_an_assignment():
    """Only the household created HERE is assigned — never one that already existed."""
    sfx = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        unrelated = c.execute(households.insert().values(name=f"Unrelated{sfx} Household")
                              .returning(households.c.id)).scalar_one()
    staff = _staff()
    created = PC.create_client(staff, first_name="Michael", last_name=f"Only{sfx}",
                               email=f"on-{sfx}@example.com")
    new_household = _row(created.person_id)["household_id"]
    with engine.connect() as c:
        assigned = c.execute(select(record_assignments.c.entity_id).where(
            record_assignments.c.user_id == staff.user_id,
            record_assignments.c.entity_type == "household")).scalars().all()
    assert assigned == [new_household]
    assert unrelated not in assigned


def test_the_household_assignment_uses_the_existing_identity_service():
    import inspect
    src = inspect.getsource(PC)
    assert "from app.services.identity import assign_record" in src
    # The prohibition is on WRITING the table directly; prose mentioning it is fine.
    assert "record_assignments.insert()" not in src, "record_assignments is written directly"
    assert "insert(record_assignments)" not in src
    assert src.count('assign_record(principal.user_id, "household"') == 1


def test_the_invitation_path_still_checks_person_scope_only():
    """The household assignment does not change resolve_invite_target."""
    import inspect

    from app.portal import invite_targets
    src = inspect.getsource(invite_targets.resolve_invite_target)
    assert 'record_in_scope(principal, "person"' in src
    assert 'record_in_scope(principal, "household"' not in src


# --- provenance ----------------------------------------------------------------------------

def test_an_audit_event_records_the_manual_creation():
    sfx = uuid.uuid4().hex[:8]
    staff = _staff()
    created = PC.create_client(staff, first_name="Michael", last_name=f"Audit{sfx}",
                               email=f"au-{sfx}@example.com")
    with engine.connect() as c:
        # audit_events.entity_id is VARCHAR, not an integer column.
        row = c.execute(select(audit_events)
                        .where(audit_events.c.entity_type == "person",
                               audit_events.c.entity_id == str(created.person_id),
                               audit_events.c.action == "person.created")).mappings().first()
    assert row, "no person.created audit event"
    assert row["actor_user_id"] == staff.user_id
    assert (row["metadata"] or {}).get("source") == PC.CREATION_SOURCE
    assert PC.CREATION_SOURCE != "microsoft365_lead", "provenance must not look like a lead import"


def test_the_person_created_event_is_published_inside_the_transaction():
    sfx = uuid.uuid4().hex[:8]
    captured = []
    with patch("app.services.events.publisher.publish_safe",
               side_effect=lambda *a, **k: captured.append((a, k))):
        created = PC.create_client(_staff(), first_name="Michael", last_name=f"Event{sfx}",
                                   email=f"ev-{sfx}@example.com")
    published = [a[0] for a, _ in captured]
    assert "people.person_created" in published
    call = next(k for a, k in captured if a[0] == "people.person_created")
    assert call.get("conn") is not None, "the fact was published outside the write transaction"
    assert call.get("producer") == PC.PRODUCER
    assert created.person_id


def test_a_timeline_event_is_written():
    from app.db import timeline_events

    sfx = uuid.uuid4().hex[:8]
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"Time{sfx}",
                               email=f"ti-{sfx}@example.com")
    with engine.connect() as c:
        row = c.execute(select(timeline_events.c.event_type, timeline_events.c.title)
                        .where(timeline_events.c.person_id == created.person_id)).mappings().first()
    assert row and row["event_type"] == "person_created"


# --- the new client is usable ----------------------------------------------------------------

def test_the_new_person_is_findable_by_the_canonical_portal_search():
    from app.portal import invite_targets

    sfx = uuid.uuid4().hex[:8]
    staff = _staff()
    created = PC.create_client(staff, first_name="Michael", last_name=f"Findable{sfx}",
                               email=f"fi-{sfx}@example.com", phone=_unique_phone()[0])
    found = invite_targets.search_people(staff, first_name="Michael", last_name=f"Findable{sfx}")
    assert found and found[0]["person_id"] == created.person_id
    assert invite_targets.search_people(staff, email=f"fi-{sfx}@example.com")[0][
        "person_id"] == created.person_id


def test_the_new_person_resolves_as_an_invitation_target_for_its_creator():
    from app.portal import invite_targets

    sfx = uuid.uuid4().hex[:8]
    staff = _staff()                                  # client.write only
    created = PC.create_client(staff, first_name="Michael", last_name=f"Target{sfx}",
                               email=f"tg-{sfx}@example.com")
    target = invite_targets.resolve_invite_target(staff, created.person_id)
    assert target.person_id == created.person_id
    assert target.household_name == f"Target{sfx} Household"
    assert invite_targets.validate_access_type("self", target) == "self"


def test_creating_a_client_creates_no_portal_account_or_invitation():
    from app.db import portal_accounts, portal_invitations

    sfx = uuid.uuid4().hex[:8]
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"NoInvite{sfx}",
                               email=f"ni-{sfx}@example.com")
    with engine.connect() as c:
        assert c.execute(select(portal_accounts.c.id)
                         .where(portal_accounts.c.person_id == created.person_id)).fetchall() == []
        assert c.execute(select(portal_invitations.c.id)).fetchall() is not None   # table untouched


def test_the_returned_client_exposes_no_household_id():
    sfx = uuid.uuid4().hex[:8]
    created = PC.create_client(_staff(), first_name="Michael", last_name=f"Opaque{sfx}",
                               email=f"op-{sfx}@example.com")
    assert not hasattr(created, "household_id")
    assert created.household_name == f"Opaque{sfx} Household"
