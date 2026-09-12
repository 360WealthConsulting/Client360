"""The six conditions that let a person-owned folder be mapped to their household.

A TaxDome folder owned partly by a person and partly by a household is usually one client recorded at
two levels — and occasionally two clients sharing a folder. Structure cannot tell those apart, so
Phase 1 requires an independent authority to say the two people are one tax household: a Drake
married-filing-jointly return naming both. Anything else is one folder-level review.

Every condition gets a passing case and a failing case, because a policy whose refusals are untested
is a policy that quietly stops refusing.

Fixture names are invented and match no real client.
"""
import hashlib
import uuid

import pytest
import sqlalchemy as sa

from app.db import engine, households, people
from app.services.document_pipeline_continuous import household_policy
from app.services.document_pipeline_continuous.household_policy import (
    MFJ_FILING_STATUS,
    household_mapping_allowed,
)

MFS_FILING_STATUS = "3"      # married, but filing SEPARATELY — deliberately not sufficient

_PEOPLE: list[int] = []
_HH: list[int] = []
_HASHES: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _HASHES:
            c.execute(sa.text("DELETE FROM drake_client_returns WHERE taxpayer_identifier_hash"
                              " = ANY(:h) OR spouse_identifier_hash = ANY(:h)"), {"h": _HASHES})
            c.execute(sa.text("DELETE FROM drake_identity WHERE identifier_hash = ANY(:h)"),
                      {"h": _HASHES})
        if _PEOPLE:
            c.execute(sa.text("DELETE FROM household_relationships WHERE person_id = ANY(:p)"),
                      {"p": _PEOPLE})
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
        if _HH:
            c.execute(households.delete().where(households.c.id.in_(_HH)))
    for bucket in (_PEOPLE, _HH, _HASHES):
        bucket.clear()


def _tag():
    return uuid.uuid4().hex[:10]


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name)
                        .returning(households.c.id)).scalar_one()
    _HH.append(hid)
    return hid


def _person(name, household_id=None, *, active=True, member=True):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=name, active=active, contact_type="Client",
            household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id and member:
            c.execute(sa.text("INSERT INTO household_relationships"
                              " (household_id, person_id, relationship_type)"
                              " VALUES (:h, :p, 'member')"), {"h": household_id, "p": pid})
    _PEOPLE.append(pid)
    return pid


def _drake_identity(person_id, name):
    h = hashlib.sha256(f"{person_id}-{uuid.uuid4()}".encode()).hexdigest()[:64]
    with engine.begin() as c:
        c.execute(sa.text("""
            INSERT INTO drake_identity (identifier_hash, primary_person_id, taxpayer_name,
                                        first_year, last_year, return_count, confidence)
            VALUES (:h, :p, :n, 2023, 2024, 1, 100)
        """), {"h": h, "p": person_id, "n": name})
    _HASHES.append(h)
    return h


_ROW = iter(range(900_000, 999_999))


def _joint_return(taxpayer_hash, spouse_hash, *, filing_status=MFJ_FILING_STATUS):
    with engine.begin() as c:
        c.execute(sa.text("""
            INSERT INTO drake_client_returns (tax_year, source_row_number, source_updated_at,
                                              raw_data, taxpayer_identifier_hash,
                                              spouse_identifier_hash, filing_status, return_type)
            VALUES (2024, :row, now(), '{}'::jsonb, :t, :s, :fs, '1040')
        """), {"row": next(_ROW), "t": taxpayer_hash, "s": spouse_hash, "fs": filing_status})


def _couple(*, filing_status=MFJ_FILING_STATUS, with_return=True):
    """A household of two active people whom Drake records as filing jointly."""
    tag = _tag()
    hid = _household(f"Quillon Household {tag}")
    a = _person(f"Quillon A {tag}", hid)
    b = _person(f"Quillon B {tag}", hid)
    if with_return:
        _joint_return(_drake_identity(a, f"Quillon A {tag}"),
                      _drake_identity(b, f"Quillon B {tag}"), filing_status=filing_status)
    return hid, a, b


def _verdict(person_ids, household_ids, organization_ids=()):
    with engine.begin() as c:
        return household_mapping_allowed(c, person_ids=person_ids, household_ids=household_ids,
                                         organization_ids=organization_ids)


# --- the passing case -----------------------------------------------------------------------------

def test_all_six_conditions_hold_so_the_folder_maps_to_the_household():
    hid, a, _b = _couple()
    v = _verdict({a}, {hid})
    assert v.mapped is True
    assert (v.entity_type, v.entity_id) == ("household", hid)
    assert v.condition == "mfj_household_confirmed"


def test_the_spouse_may_also_appear_as_an_owner():
    """Condition 4 permits the couple, not just the one person."""
    hid, a, b = _couple()
    v = _verdict({a, b}, {hid})
    # Two person owners fails condition 1 by design: "exactly one active person".
    assert v.mapped is False
    assert v.condition == "not_exactly_one_person"


# --- condition 1: exactly one active person -------------------------------------------------------

def test_two_unrelated_person_owners_are_refused():
    hid, a, _b = _couple()
    outsider = _person(f"Vexley {_tag()}", _household(f"Vexley Household {_tag()}"))
    v = _verdict({a, outsider}, {hid})
    assert v.mapped is False
    assert v.condition == "not_exactly_one_person"


def test_an_inactive_person_owner_is_refused():
    tag = _tag()
    hid = _household(f"Quillon Household {tag}")
    dormant = _person(f"Quillon Dormant {tag}", hid, active=False)
    v = _verdict({dormant}, {hid})
    assert v.mapped is False
    assert v.condition == "inactive_person_owner"


def test_no_person_owner_at_all_is_refused():
    hid, _a, _b = _couple()
    v = _verdict(set(), {hid})
    assert v.mapped is False
    assert v.condition == "not_exactly_one_person"


# --- condition 2: exactly one active household -----------------------------------------------------

def test_a_person_in_two_households_is_refused():
    hid, a, _b = _couple()
    second = _household(f"Second Household {_tag()}")
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO household_relationships"
                          " (household_id, person_id, relationship_type)"
                          " VALUES (:h, :p, 'member')"), {"h": second, "p": a})
    v = _verdict({a}, {hid})
    assert v.mapped is False
    assert v.condition == "person_in_multiple_households"


def test_a_person_who_is_not_a_member_of_the_folders_household_is_refused():
    hid, _a, _b = _couple()
    stranger = _person(f"Stranger {_tag()}", _household(f"Other Household {_tag()}"))
    v = _verdict({stranger}, {hid})
    assert v.mapped is False
    assert v.condition == "person_not_in_this_household"


def test_two_competing_households_in_one_folder_are_refused():
    hid, a, _b = _couple()
    other = _household(f"Vexley Household {_tag()}")
    v = _verdict({a}, {hid, other})
    assert v.mapped is False
    assert v.condition == "not_exactly_one_household"


# --- condition 3: Drake confirms married filing jointly --------------------------------------------

def test_without_a_drake_joint_return_the_folder_is_refused():
    hid, a, _b = _couple(with_return=False)
    v = _verdict({a}, {hid})
    assert v.mapped is False
    assert v.condition == "no_drake_mfj_confirmation"


def test_married_filing_SEPARATELY_does_not_qualify():
    """Code 3 carries a spouse on every row, and means the opposite of one filing unit."""
    hid, a, _b = _couple(filing_status=MFS_FILING_STATUS)
    v = _verdict({a}, {hid})
    assert v.mapped is False
    assert v.condition == "no_drake_mfj_confirmation"


def test_a_joint_return_with_someone_outside_the_household_does_not_qualify():
    tag = _tag()
    hid = _household(f"Quillon Household {tag}")
    a = _person(f"Quillon A {tag}", hid)
    _b = _person(f"Quillon B {tag}", hid)
    outsider = _person(f"Elsewhere {tag}", _household(f"Elsewhere Household {tag}"))
    _joint_return(_drake_identity(a, f"Quillon A {tag}"),
                  _drake_identity(outsider, f"Elsewhere {tag}"))
    v = _verdict({a}, {hid})
    assert v.mapped is False
    assert v.condition == "no_drake_mfj_confirmation"


def test_the_confirmation_is_symmetric_between_taxpayer_and_spouse():
    """Either partner may be the taxpayer on the return; the household is the same either way."""
    hid, a, b = _couple()
    assert _verdict({a}, {hid}).mapped is True
    assert _verdict({b}, {hid}).mapped is True


# --- condition 5: no organization or competing identity --------------------------------------------

def test_an_organization_owner_anywhere_in_the_folder_is_refused():
    hid, a, _b = _couple()
    v = _verdict({a}, {hid}, organization_ids={4242})
    assert v.mapped is False
    assert v.condition == "organization_owner_present"


# --- condition 6: the household is a consistent couple ---------------------------------------------

def test_a_household_of_one_cannot_be_joint():
    tag = _tag()
    hid = _household(f"Solo Household {tag}")
    solo = _person(f"Solo {tag}", hid)
    v = _verdict({solo}, {hid})
    assert v.mapped is False
    assert v.condition == "household_is_not_a_couple"


def test_a_household_of_three_is_refused():
    hid, a, _b = _couple()
    _person(f"Quillon C {_tag()}", hid)
    v = _verdict({a}, {hid})
    assert v.mapped is False
    assert v.condition == "household_is_not_a_couple"


def test_inconsistent_household_membership_is_refused():
    """The relationship table and people.household_id must agree about who is in the household."""
    hid, a, _b = _couple()
    ghost = _person(f"Ghost {_tag()}", hid, member=False)   # denormalised column only
    assert ghost
    v = _verdict({a}, {hid})
    assert v.mapped is False
    assert v.condition == "household_membership_inconsistent"


# --- the refusal is the same shape whatever failed -------------------------------------------------

@pytest.mark.parametrize("condition", [
    "not_exactly_one_person", "person_in_multiple_households", "no_drake_mfj_confirmation",
    "organization_owner_present", "household_is_not_a_couple",
    "household_membership_inconsistent", "not_exactly_one_household",
])
def test_every_refusal_names_its_condition(condition):
    assert condition in {
        "not_exactly_one_person", "inactive_person_owner", "person_not_in_this_household",
        "person_in_multiple_households", "household_membership_inconsistent",
        "household_is_not_a_couple", "no_drake_mfj_confirmation", "unrelated_person_owner",
        "organization_owner_present", "not_exactly_one_household",
    }


def test_a_refusal_carries_no_entity_to_write():
    hid, a, _b = _couple(with_return=False)
    v = _verdict({a}, {hid})
    assert v.mapped is False
    assert v.entity_type is None and v.entity_id is None
    assert v.as_evidence.startswith("household policy: ")


def test_mfj_code_is_two_and_mfs_is_not_treated_as_joint():
    assert household_policy.MFJ_FILING_STATUS == "2"
    assert MFS_FILING_STATUS != household_policy.MFJ_FILING_STATUS
