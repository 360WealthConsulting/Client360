"""Explicit household assignment (app.services.households) — coverage.

Verifies the supported, migration-safe way to group people into one household: it reuses an existing
household or creates one, sets people.household_id AND records a household_relationships member row,
preserves existing person records, never duplicates a household, refuses to merge people already in
different households, and is idempotent. Also confirms the downstream effect the TaxDome joint-folder
resolver depends on. Temporary/test rows only.
"""
import pytest
from sqlalchemy import delete, func, select

from app.db import engine, household_relationships, households, people
from app.services.households import assign_people_to_household

_TAG = "HHADMIN"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as conn:
            ids = list(conn.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            if ids:
                conn.execute(delete(household_relationships).where(household_relationships.c.person_id.in_(ids)))
                conn.execute(delete(people).where(people.c.id.in_(ids)))
            conn.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


def _person(first, last, household_id=None):
    with engine.begin() as conn:
        return conn.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last} {_TAG}",
            household_id=household_id).returning(people.c.id)).scalar_one()


def _household(name=f"{_TAG} Existing"):
    with engine.begin() as conn:
        return conn.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()


def _person_row(pid):
    with engine.connect() as conn:
        return conn.execute(select(people).where(people.c.id == pid)).mappings().one()


def _membership_count(household_id, person_id):
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(household_relationships).where(
            household_relationships.c.household_id == household_id,
            household_relationships.c.person_id == person_id))


# --- create + assign ---------------------------------------------------------

def test_assign_creates_household_and_links_both_people():
    a = _person("Michael", f"White{_TAG}")
    b = _person("Debra", f"White{_TAG}")
    report = assign_people_to_household([a, b], name=f"{_TAG} White Household")
    hh = report["household_id"]
    assert report["household_created"] is True and report["members_assigned"] == 2
    assert _person_row(a)["household_id"] == hh and _person_row(b)["household_id"] == hh
    assert _membership_count(hh, a) == 1 and _membership_count(hh, b) == 1


def test_assign_derives_surname_household_name():
    a = _person("Michael", f"White{_TAG}")
    b = _person("Debra", f"White{_TAG}")
    report = assign_people_to_household([a, b])
    assert report["household_name"].endswith("Household") and f"White{_TAG}" in report["household_name"]


def test_assign_reuses_existing_household_no_duplicate():
    hh = _household()
    a = _person("Michael", f"White{_TAG}", household_id=hh)
    b = _person("Debra", f"White{_TAG}")                    # not yet in a household
    before = _household_total()
    report = assign_people_to_household([a, b])
    assert report["household_id"] == hh and report["household_created"] is False
    assert _person_row(b)["household_id"] == hh
    assert _household_total() == before                     # reused; no new household created


def _household_total():
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(households).where(
            households.c.name.like(f"%{_TAG}%")))


def test_assign_is_idempotent():
    a = _person("Michael", f"White{_TAG}")
    b = _person("Debra", f"White{_TAG}")
    first = assign_people_to_household([a, b])
    second = assign_people_to_household([a, b])
    assert second["members_assigned"] == 0 and second["already_members"] == 2
    assert second["membership_rows_added"] == 0
    assert second["household_id"] == first["household_id"]
    assert _household_total() == 1                          # still exactly one household


def test_refuses_to_merge_people_in_different_households():
    hh1 = _household(name=f"{_TAG} One")
    hh2 = _household(name=f"{_TAG} Two")
    a = _person("Michael", f"White{_TAG}", household_id=hh1)
    b = _person("Debra", f"White{_TAG}", household_id=hh2)
    with pytest.raises(ValueError):
        assign_people_to_household([a, b])
    # unchanged
    assert _person_row(a)["household_id"] == hh1 and _person_row(b)["household_id"] == hh2


def test_missing_person_id_raises():
    a = _person("Michael", f"White{_TAG}")
    with pytest.raises(ValueError):
        assign_people_to_household([a, 999_000_001])


# --- dry run + record preservation -------------------------------------------

def test_dry_run_makes_no_changes():
    a = _person("Michael", f"White{_TAG}")
    b = _person("Debra", f"White{_TAG}")
    report = assign_people_to_household([a, b], dry_run=True)
    assert report["dry_run"] is True and report["members_assigned"] == 2
    assert _person_row(a)["household_id"] is None and _person_row(b)["household_id"] is None
    assert _household_total() == 0


def test_preserves_existing_person_fields():
    a = _person("Michael", f"White{_TAG}")
    b = _person("Debra", f"White{_TAG}")
    before = {k: _person_row(a)[k] for k in ("first_name", "last_name", "full_name")}
    assign_people_to_household([a, b])
    after = {k: _person_row(a)[k] for k in ("first_name", "last_name", "full_name")}
    assert after == before                                  # only household_id changed


# --- downstream: TaxDome joint-folder resolver now resolves ------------------

def test_taxdome_resolver_resolves_joint_folder_after_assignment():
    from app.importers import taxdome_drive as td
    a = _person("Michael", "White")                         # names as they appear canonically
    b = _person("Debra", "White")
    # Full names include the _TAG suffix in this fixture, so match on those:
    with engine.begin() as conn:
        conn.execute(people.update().where(people.c.id == a).values(full_name="Michael White"))
        conn.execute(people.update().where(people.c.id == b).values(full_name="Debra White"))
    try:
        assign_people_to_household([a, b], name=f"{_TAG} White Household")
        td._database.cache_clear()
        with engine.connect() as conn:
            household_id, person_id = td.resolve_folder(conn, "Michael and Debra White")
        assert person_id is None and household_id is not None
        assert household_id == _person_row(a)["household_id"]
    finally:
        # these two rows have production-like names; remove them explicitly
        with engine.begin() as conn:
            conn.execute(delete(household_relationships).where(household_relationships.c.person_id.in_([a, b])))
            conn.execute(delete(people).where(people.c.id.in_([a, b])))
