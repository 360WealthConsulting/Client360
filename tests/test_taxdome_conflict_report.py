"""The TaxDome ownership-conflict report: read-only, and honest about which cause is which.

The report exists to answer one question before the pipeline is ever switched on — how many TaxDome
documents would the authoritative folder mapping disagree with, and why. Two properties have to
hold or the answer is worthless:

  * it must never write. It reads production.
  * its fast path must give the same answers as ``taxdome_drive.resolve_folder``. The report
    rebuilds the people index once instead of rescanning per document, and a shortcut that quietly
    disagreed with the real resolver would misreport every count in the file.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.db import documents, engine, households, people

REPORT = Path(__file__).parents[1] / "scripts/report_taxdome_ownership_conflicts.py"


def _tag() -> str:
    return uuid.uuid4().hex[:10]


def _person(conn, name, household_id=None):
    return conn.execute(people.insert().values(
        full_name=name, active=True, household_id=household_id).returning(people.c.id)).scalar_one()


def _doc(conn, *, folder=None, person_id=None, household_id=None, source="TaxDome"):
    """A live TaxDome document. ``storage_path``, ``size_bytes`` and ``sha256`` are NOT NULL with no
    default, so they are supplied here rather than discovered one failing insert at a time."""
    tags = {"source_system": source}
    if folder:
        tags["taxdome_folder"] = folder
    tag = _tag()
    return conn.execute(documents.insert().values(
        original_name=f"td-{tag}.pdf", stored_name=f"td-{tag}.pdf",
        storage_path=f"taxdome/{tag}.pdf", size_bytes=10,
        sha256=hashlib.sha256(tag.encode()).hexdigest(),
        status="active", archived=False,
        tags=tags, person_id=person_id, household_id=household_id).returning(
        documents.c.id)).scalar_one()


# --- read-only ------------------------------------------------------------------------------------

def test_the_report_never_writes():
    """SQL-shaped tokens, so a Python list ``.insert`` cannot trip the guard into being weakened."""
    source = REPORT.read_text(encoding="utf-8")
    for token in ("engine.begin(", "documents.insert(", "documents.update(", "documents.delete(",
                  "INSERT INTO", "UPDATE ", "DELETE FROM",
                  "resolve_document_ownership", "record_extracted_text"):
        assert token not in source, f"the report must not be able to {token}"


def test_the_report_asserts_read_only_before_reading_anything():
    """Inspect ``run``'s body, not the whole file.

    Searching the file compares the guard against the *definition* of ``_people_index`` near the
    top, which says nothing about call order. The question is what happens inside ``run``."""
    import inspect

    import scripts.report_taxdome_ownership_conflicts as report

    body = inspect.getsource(report.run)
    assert "assert_read_only(conn)" in body, "run() must assert read-only on its own connection"
    assert body.index("assert_read_only(conn)") < body.index("_people_index(conn)"), \
        "the guard must run before the first read, not after"


# --- the fast path is the real rule ---------------------------------------------------------------

@pytest.mark.parametrize("folder_people,expect", [
    (["Solo Unique"], "person"),            # single-person folder, one match
    (["Joint Aye", "Joint Bee"], "household"),   # joint folder, one shared household
    ([], "unresolved"),                     # nothing matchable
])
def test_the_index_path_matches_resolve_folder(folder_people, expect):
    """Every branch of the resolver, checked against the authoritative implementation itself."""
    import scripts.report_taxdome_ownership_conflicts as report
    from app.importers.taxdome_drive import resolve_folder

    tag = _tag()
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {tag}").returning(
            households.c.id)).scalar_one()
        names = [f"{n} {tag}" for n in folder_people]
        for name in names:
            _person(c, name, household_id=hh)
        folder = " and ".join(names) if names else f"Nobody Here {tag}"

    with engine.connect() as c:
        index = report._people_index(c)
        fast = report._resolve_with_index(folder, index)
        authoritative = resolve_folder(c, folder)

    assert fast == authoritative, "the report's index path disagreed with resolve_folder"
    household_id, person_id = fast
    if expect == "person":
        assert person_id is not None and household_id is None
    elif expect == "household":
        assert household_id is not None and person_id is None
    else:
        assert (household_id, person_id) == (None, None)


# --- causes ---------------------------------------------------------------------------------------

def test_a_folder_naming_a_different_owner_is_a_conflict_not_an_overwrite():
    """The whole point: the mapping is authoritative, but it may never take a document off the
    client already recorded on it. That disagreement is reported, never applied."""
    import scripts.report_taxdome_ownership_conflicts as report

    tag = _tag()
    with engine.begin() as c:
        folder_owner = _person(c, f"Folder Owner {tag}")
        other_owner = _person(c, f"Other Owner {tag}")
        doc = _doc(c, folder=f"Folder Owner {tag}", person_id=other_owner)

    with engine.connect() as c:
        index = report._people_index(c)
        row = c.execute(sa.select(documents.c.id, documents.c.tags, documents.c.person_id,
                                  documents.c.household_id, documents.c.organization_id)
                        .where(documents.c.id == doc)).mappings().first()
        cause, detail = report._classify(row, index)

    assert cause == "conflict_person"
    assert detail["proposed"]["entity_id"] == folder_owner
    assert detail["stored"]["person_id"] == other_owner


def test_a_folder_naming_the_same_owner_agrees():
    import scripts.report_taxdome_ownership_conflicts as report

    tag = _tag()
    with engine.begin() as c:
        owner = _person(c, f"Same Owner {tag}")
        doc = _doc(c, folder=f"Same Owner {tag}", person_id=owner)

    with engine.connect() as c:
        index = report._people_index(c)
        row = c.execute(sa.select(documents.c.id, documents.c.tags, documents.c.person_id,
                                  documents.c.household_id, documents.c.organization_id)
                        .where(documents.c.id == doc)).mappings().first()
        cause, _ = report._classify(row, index)
    assert cause == "agrees"


def test_an_unowned_document_with_a_resolving_folder_would_link():
    import scripts.report_taxdome_ownership_conflicts as report

    tag = _tag()
    with engine.begin() as c:
        _person(c, f"Unowned Target {tag}")
        doc = _doc(c, folder=f"Unowned Target {tag}")

    with engine.connect() as c:
        index = report._people_index(c)
        row = c.execute(sa.select(documents.c.id, documents.c.tags, documents.c.person_id,
                                  documents.c.household_id, documents.c.organization_id)
                        .where(documents.c.id == doc)).mappings().first()
        cause, _ = report._classify(row, index)
    assert cause == "would_link", "an unowned document is work, not a conflict"


def test_an_unresolvable_folder_is_its_own_cause():
    """No match is a different problem from a disagreement, and needs a different human action."""
    import scripts.report_taxdome_ownership_conflicts as report

    tag = _tag()
    with engine.begin() as c:
        doc = _doc(c, folder=f"Nobody Named This {tag}")

    with engine.connect() as c:
        index = report._people_index(c)
        row = c.execute(sa.select(documents.c.id, documents.c.tags, documents.c.person_id,
                                  documents.c.household_id, documents.c.organization_id)
                        .where(documents.c.id == doc)).mappings().first()
        cause, _ = report._classify(row, index)
    assert cause == "folder_unresolved"


def test_a_taxdome_document_with_no_folder_tag_is_reported_separately():
    """Without the folder the lane is not authoritative at all, so it must not be counted as one."""
    import scripts.report_taxdome_ownership_conflicts as report

    with engine.begin() as c:
        doc = _doc(c, folder=None)

    with engine.connect() as c:
        index = report._people_index(c)
        row = c.execute(sa.select(documents.c.id, documents.c.tags, documents.c.person_id,
                                  documents.c.household_id, documents.c.organization_id)
                        .where(documents.c.id == doc)).mappings().first()
        cause, _ = report._classify(row, index)
    assert cause == "missing_folder_tag"


def test_every_cause_is_declared_in_the_documented_order():
    """The summary prints CAUSE_ORDER; a cause the classifier can emit but the order omits would be
    counted and never shown."""
    import scripts.report_taxdome_ownership_conflicts as report

    emitted = {"conflict_person", "conflict_household", "folder_unresolved",
               "missing_folder_tag", "agrees", "would_link"}
    assert set(report.CAUSE_ORDER) == emitted
    assert report.CAUSE_ORDER[0].startswith("conflict"), "conflicts belong at the top"


def test_the_report_uses_the_shared_live_predicate():
    """It must count what discovery would enqueue, not a status-only approximation."""
    source = REPORT.read_text(encoding="utf-8")
    assert "live_document_clause" in source
    assert "status IS DISTINCT FROM 'deleted'" not in source
