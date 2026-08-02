"""Household Management read API (app.services.households) — coverage.

Backend the Household Management UI consumes: search, members with roles, household-scoped documents
(person-or-household ownership, ADR-072/073 — no new ownership logic), audit history, and the
unresolved-TaxDome-folder worklist. Temp/test rows only.
"""
import pytest
from sqlalchemy import delete, insert, select

from app.db import documents, engine, household_relationships, households, people
from app.services import households as hh

_TAG = "HHREAD"


@pytest.fixture(autouse=True)
def _clean():
    def _wipe():
        with engine.begin() as conn:
            pids = list(conn.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            conn.execute(documents.delete().where(documents.c.tags["taxdome_folder"].astext.like(f"%{_TAG}%")))
            if pids:
                conn.execute(delete(household_relationships).where(household_relationships.c.person_id.in_(pids)))
                conn.execute(delete(people).where(people.c.id.in_(pids)))
            conn.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))
    _wipe()
    yield
    _wipe()


def _household(name):
    with engine.begin() as conn:
        return conn.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()


def _person(first, last, household_id=None):
    with engine.begin() as conn:
        pid = conn.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last} {_TAG}",
            household_id=household_id).returning(people.c.id)).scalar_one()
        if household_id is not None:
            conn.execute(insert(household_relationships).values(
                household_id=household_id, person_id=pid, relationship_type="member"))
    return pid


def _doc(name, *, person_id=None, household_id=None, folder=None):
    with engine.begin() as conn:
        return conn.execute(documents.insert().values(
            original_name=name, stored_name=f"{name}-{_TAG}-{person_id}-{household_id}",
            storage_path=f"/x/{name}", storage_provider="Client360 Local",
            size_bytes=10, sha256="0" * 64, person_id=person_id, household_id=household_id,
            status="active", archived=False,
            tags={"source_system": "TaxDome Drive", "taxdome_folder": folder or f"{_TAG} F"})
            .returning(documents.c.id)).scalar_one()


# --- search ------------------------------------------------------------------

def test_search_by_household_name():
    hid = _household(f"{_TAG} White Household")
    results = hh.search_households(f"{_TAG} White")
    assert any(r["id"] == hid for r in results)


def test_search_by_member_name():
    hid = _household(f"{_TAG} Household A")
    _person("Michael", f"White{_TAG}", household_id=hid)
    results = hh.search_households(f"White{_TAG}")
    assert any(r["id"] == hid for r in results)
    assert next(r for r in results if r["id"] == hid)["member_count"] == 1


# --- members -----------------------------------------------------------------

def test_household_members_include_roles():
    hid = _household(f"{_TAG} Household")
    a = _person("Michael", f"White{_TAG}", household_id=hid)
    b = _person("Debra", f"White{_TAG}", household_id=hid)
    members = hh.household_members(hid)
    ids = {m["id"] for m in members}
    assert {a, b} <= ids
    assert all("relationship_type" in m for m in members)


# --- household documents (person-or-household ownership) ----------------------

def test_household_documents_include_household_and_member_owned():
    hid = _household(f"{_TAG} Household")
    a = _person("Michael", f"White{_TAG}", household_id=hid)
    _doc("Joint 1040.pdf", household_id=hid, folder=f"{_TAG} White")     # household-owned
    _doc("Michael W-2.pdf", person_id=a, folder=f"{_TAG} White")        # member-owned
    names = {d["original_name"] for d in hh.household_documents(hid)}
    assert {"Joint 1040.pdf", "Michael W-2.pdf"} <= names


def test_household_documents_excludes_other_households():
    hid = _household(f"{_TAG} Household")
    other = _household(f"{_TAG} Other")
    _doc("Ours.pdf", household_id=hid, folder=f"{_TAG} Ours")
    _doc("Theirs.pdf", household_id=other, folder=f"{_TAG} Theirs")
    names = {d["original_name"] for d in hh.household_documents(hid)}
    assert "Ours.pdf" in names and "Theirs.pdf" not in names


# --- audit -------------------------------------------------------------------

def test_household_audit_reads_assignment_events():
    from app.services.households import assign_people_to_household
    hid = _household(f"{_TAG} Household")
    a = _person("Michael", f"White{_TAG}", household_id=hid)
    b = _person("Debra", f"White{_TAG}")
    # assign with an actor so an audit event is written against the household
    assign_people_to_household([a, b], actor_user_id=1, request_id=f"{_TAG}-req")
    events = hh.household_audit(hid)
    assert any(e["action"] == "household.members_assigned" for e in events)


# --- unresolved TaxDome folders ----------------------------------------------

def test_unresolved_folders_listed_with_candidates_and_resolution():
    # An unlinked joint folder; its people share a household -> resolves to that household.
    # Folder + person names must be clean (the resolver parses the surname), so clean up explicitly.
    hid = _household(f"{_TAG} White Household")
    folder = "Michael and Debra White"
    with engine.begin() as conn:
        a = conn.execute(people.insert().values(
            first_name="Michael", last_name="White", full_name="Michael White",
            household_id=hid).returning(people.c.id)).scalar_one()
        b = conn.execute(people.insert().values(
            first_name="Debra", last_name="White", full_name="Debra White",
            household_id=hid).returning(people.c.id)).scalar_one()
        for pid in (a, b):
            conn.execute(insert(household_relationships).values(
                household_id=hid, person_id=pid, relationship_type="member"))
        conn.execute(documents.insert().values(
            original_name="Joint.pdf", stored_name=f"joint-{_TAG}", storage_path="/x/joint",
            storage_provider="Client360 Local", size_bytes=10, sha256="0" * 64,
            status="active", archived=False,
            tags={"source_system": "TaxDome Drive", "taxdome_folder": folder}))
    try:
        row = next((r for r in hh.unresolved_taxdome_folders() if r["folder"] == folder), None)
        assert row is not None and row["files"] == 1
        # resolver re-evaluates live; with both spouses in one household it resolves to it
        assert row["resolves_to"]["household_id"] == hid
        assert any(s["id"] in (a, b) for s in row["suggestions"])
    finally:
        with engine.begin() as conn:
            conn.execute(documents.delete().where(documents.c.stored_name == f"joint-{_TAG}"))
            conn.execute(delete(household_relationships).where(household_relationships.c.person_id.in_([a, b])))
            conn.execute(delete(people).where(people.c.id.in_([a, b])))
