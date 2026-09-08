"""Rebuilding ``drake_identity`` must not destroy person linkage.

THE DEFECT THESE PIN

``scripts/build_drake_identity.py`` rebuilt the table as ``DELETE FROM drake_identity`` followed by an
``INSERT ... SELECT`` that did not include ``primary_person_id``. Every run therefore discarded the
person link on every identity -- 931 links in production when this was found, including links
established through human adjudication and by individually authorised manual repair, such as the
Christine Simmons identity moved from person 950 to person 951. The rebuild reported success.

Two identities from that production repair are used by name here, with their real identifier hashes,
because they are the concrete regression: one identifier belongs to Christine, the neighbouring one to
Theado, and a rebuild must return each to its own person and never to the other's.

Everything is seeded inside one transaction and rolled back. Nothing is committed, and the assertions
about ``people``, ``person_source_links`` and ``person_merge_history`` compare the whole table before
and after the rebuild rather than trusting that it only touched what it meant to.
"""
import json
import uuid

import pytest
from sqlalchemy import text

from app.db import engine
from app.services.drake_identity_rebuild import (
    DERIVED_COLUMNS,
    PERSISTENT_COLUMNS,
    RebuildRefused,
    rebuild_drake_identities,
)

# The two production identifiers repaired in the D5 relink.
CHRISTINE_HASH = "8d5fb3f38927ff45a7b0bd3cf89255fe1f9b6feb8c5cd8b43179eaf0758690ec"
THEADO_HASH = "032e90c662abe816db1359369b3d18879cf29dcbdbfaab461651d5da0f012f96"

_NO_SUCH_PERSON = -424242


class Seeder:
    """Seeds tagged Drake contacts, people and identities inside a transaction."""

    def __init__(self, connection, tag):
        self.connection = connection
        self.tag = tag
        self.hashes = []

    def person(self, name, person_id=None):
        if person_id is None:
            return self.connection.execute(text(
                "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
                {"n": f"{self.tag} {name}"}).scalar_one()
        self.connection.execute(text(
            "INSERT INTO people (id, full_name, active) VALUES (:i, :n, true)"),
            {"i": person_id, "n": f"{self.tag} {name}"})
        return person_id

    def contacts(self, identifier_hash, years, *, taxpayer=None, spouse=None):
        """One Drake source contact per year, exactly as the importer writes them."""
        for year in years:
            for role, name in (("taxpayer", taxpayer), ("spouse", spouse)):
                if name is None:
                    continue
                self.connection.execute(text(
                    "INSERT INTO source_contacts "
                    "  (source_system, source_file, source_hash, full_name, raw_data) "
                    "VALUES ('Drake', :f, :sh, :n, CAST(:raw AS json))"),
                    {"f": f"{self.tag}.csv", "sh": uuid.uuid4().hex, "n": name,
                     "raw": json.dumps({"identifier_hash": identifier_hash, "role": role,
                                        "tax_year": str(year)})})
        self.hashes.append(identifier_hash)

    def identity(self, identifier_hash, *, person_id=None, first_year=1990, last_year=1990,
                 return_count=1, taxpayer_name="stale", spouse_name=None, confidence=None):
        self.connection.execute(text(
            "INSERT INTO drake_identity (identifier_hash, primary_person_id, first_year, "
            "  last_year, return_count, taxpayer_name, spouse_name, confidence) "
            "VALUES (:h, :p, :f, :l, :c, :t, :s, :conf)"),
            {"h": identifier_hash, "p": person_id, "f": first_year, "l": last_year,
             "c": return_count, "t": taxpayer_name, "s": spouse_name, "conf": confidence})
        self.hashes.append(identifier_hash)

    def row(self, identifier_hash):
        return self.connection.execute(text(
            "SELECT * FROM drake_identity WHERE identifier_hash = :h"),
            {"h": identifier_hash}).mappings().one_or_none()


def snapshot(connection, table, order="id"):
    return connection.execute(text(
        f"SELECT md5(string_agg(t::text, chr(10) ORDER BY t.{order})), count(*) "  # noqa: S608
        f"FROM {table} t")).one()


@pytest.fixture()
def seeded():
    """A transaction with a tagged fixture in it. Always rolled back."""
    tag = f"dkrebuild-{uuid.uuid4().hex[:8]}"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield Seeder(connection, tag)
        finally:
            transaction.rollback()


# ==================================================================================================
# 1-2. Existing identities survive, linked or not.
# ==================================================================================================

def test_a_linked_identity_survives_the_rebuild(seeded):
    person = seeded.person("Linked")
    identifier_hash = uuid.uuid4().hex
    seeded.identity(identifier_hash, person_id=person)
    seeded.contacts(identifier_hash, [2021, 2022], taxpayer="LINKED TAXPAYER")

    rebuild_drake_identities(seeded.connection)

    assert seeded.row(identifier_hash)["primary_person_id"] == person


def test_an_unlinked_identity_survives_and_is_refreshed(seeded):
    identifier_hash = uuid.uuid4().hex
    seeded.identity(identifier_hash)
    seeded.contacts(identifier_hash, [2023], taxpayer="UNLINKED TAXPAYER")

    rebuild_drake_identities(seeded.connection)

    row = seeded.row(identifier_hash)
    assert row is not None
    assert row["primary_person_id"] is None
    assert row["first_year"] == 2023


# ==================================================================================================
# 3-4. New identities are inserted; derived fields refresh without touching persistent state.
# ==================================================================================================

def test_a_newly_discovered_identity_is_inserted(seeded):
    identifier_hash = uuid.uuid4().hex
    seeded.contacts(identifier_hash, [2024, 2025], taxpayer="BRAND NEW")

    report = rebuild_drake_identities(seeded.connection)

    row = seeded.row(identifier_hash)
    assert row is not None and row["primary_person_id"] is None
    assert (row["first_year"], row["last_year"], row["return_count"]) == (2024, 2025, 2)
    assert report.inserted >= 1


def test_derived_fields_refresh_while_persistent_state_is_untouched(seeded):
    person = seeded.person("Refreshed")
    identifier_hash = uuid.uuid4().hex
    seeded.identity(identifier_hash, person_id=person, first_year=1990, last_year=1990,
                    return_count=1, taxpayer_name="stale", confidence=77)
    before = dict(seeded.row(identifier_hash))
    seeded.contacts(identifier_hash, [2021, 2022, 2023], taxpayer="FRESH NAME", spouse="FRESH SP")

    rebuild_drake_identities(seeded.connection)
    after = dict(seeded.row(identifier_hash))

    assert [after[c] for c in DERIVED_COLUMNS] == [2021, 2023, 6, "FRESH NAME", "FRESH SP"]
    assert [after[c] for c in PERSISTENT_COLUMNS] == [before[c] for c in PERSISTENT_COLUMNS]
    assert after["confidence"] == 77, "an adjudicated score must not be reset to NULL"


# ==================================================================================================
# 5-7. The production regression: two neighbouring identities keep their own people.
# ==================================================================================================

def test_the_christine_and_theado_identities_keep_their_own_people(seeded):
    """Christine's identifier was moved to 951 by an authorised repair. A rebuild must not undo it."""
    christine = seeded.person("Christine Simmons")
    theado = seeded.person("Theado Simmons")
    seeded.identity(CHRISTINE_HASH, person_id=christine)
    seeded.identity(THEADO_HASH, person_id=theado)
    seeded.contacts(CHRISTINE_HASH, [2021, 2022], spouse="CHRISTINE SIMMONS")
    seeded.contacts(CHRISTINE_HASH, [2023, 2024, 2025], taxpayer="CHRISTINE SIMMONS")
    seeded.contacts(THEADO_HASH, [2021, 2022], taxpayer="THEADO SIMMONS")

    rebuild_drake_identities(seeded.connection)

    assert seeded.row(CHRISTINE_HASH)["primary_person_id"] == christine
    assert seeded.row(THEADO_HASH)["primary_person_id"] == theado
    assert christine != theado, "the two identities must not collapse onto one person"


def test_the_christine_identity_keeps_person_951_by_id(seeded):
    """The literal production shape: identifier 8d5fb3f8... on person 951, and it stays there."""
    taken = seeded.connection.execute(text(
        "SELECT count(*) FROM people WHERE id IN (950, 951)")).scalar()
    if taken:
        pytest.skip("person ids 950/951 already exist in this database")
    seeded.person("Theado Simmons", person_id=950)
    seeded.person("Christine Simmons", person_id=951)
    seeded.identity(CHRISTINE_HASH, person_id=951)
    seeded.identity(THEADO_HASH, person_id=950)
    seeded.contacts(CHRISTINE_HASH, [2021, 2022, 2023, 2024, 2025], taxpayer="CHRISTINE SIMMONS")
    seeded.contacts(THEADO_HASH, [2021, 2022], taxpayer="THEADO SIMMONS")

    rebuild_drake_identities(seeded.connection)

    assert seeded.row(CHRISTINE_HASH)["primary_person_id"] == 951
    assert seeded.row(THEADO_HASH)["primary_person_id"] == 950


def test_many_existing_links_all_survive(seeded):
    expected = {}
    for index in range(8):
        identifier_hash = uuid.uuid4().hex
        person = seeded.person(f"Person {index}")
        seeded.identity(identifier_hash, person_id=person)
        seeded.contacts(identifier_hash, [2020 + index], taxpayer=f"TAXPAYER {index}")
        expected[identifier_hash] = person

    report = rebuild_drake_identities(seeded.connection)

    for identifier_hash, person in expected.items():
        assert seeded.row(identifier_hash)["primary_person_id"] == person
    assert report.links_preserved >= len(expected)


# ==================================================================================================
# 8, 10. Fail closed, and the caller's rollback undoes everything.
# ==================================================================================================

def test_a_dangling_person_reference_refuses_the_rebuild(seeded):
    identifier_hash = uuid.uuid4().hex
    seeded.identity(identifier_hash, person_id=_NO_SUCH_PERSON)
    seeded.contacts(identifier_hash, [2021], taxpayer="DANGLING")

    with pytest.raises(RebuildRefused, match="person that does not exist"):
        rebuild_drake_identities(seeded.connection)


def test_an_empty_source_refuses_rather_than_rebuilding_nothing(seeded):
    """A truncated import must not be mistaken for an empty world."""
    savepoint = seeded.connection.begin_nested()
    try:
        seeded.connection.execute(text(
            "DELETE FROM person_source_links WHERE source_contact_id IN "
            "  (SELECT id FROM source_contacts WHERE source_system = 'Drake')"))
        seeded.connection.execute(text("DELETE FROM source_contacts WHERE source_system = 'Drake'"))
        with pytest.raises(RebuildRefused, match="no identities"):
            rebuild_drake_identities(seeded.connection)
    finally:
        savepoint.rollback()


def test_a_refused_rebuild_leaves_the_table_exactly_as_it_was(seeded):
    person = seeded.person("Survivor")
    good = uuid.uuid4().hex
    seeded.identity(good, person_id=person, first_year=1990, taxpayer_name="stale")
    seeded.contacts(good, [2021, 2022], taxpayer="WOULD BE REFRESHED")
    before = snapshot(seeded.connection, "drake_identity", order="identifier_hash")

    savepoint = seeded.connection.begin_nested()
    try:
        bad = uuid.uuid4().hex
        seeded.identity(bad, person_id=_NO_SUCH_PERSON)
        seeded.contacts(bad, [2021], taxpayer="DANGLING")
        with pytest.raises(RebuildRefused):
            rebuild_drake_identities(seeded.connection)
    finally:
        savepoint.rollback()

    assert snapshot(seeded.connection, "drake_identity", order="identifier_hash") == before
    assert seeded.row(good)["taxpayer_name"] == "stale", "the refused refresh was rolled back"


# ==================================================================================================
# 11. Identities that vanish from source data are retained and reported, never deleted.
# ==================================================================================================

def test_a_stale_unlinked_identity_is_retained_and_reported(seeded):
    stale = uuid.uuid4().hex
    seeded.identity(stale)                      # no source contacts for this hash
    seeded.contacts(uuid.uuid4().hex, [2021], taxpayer="KEEPS THE SOURCE NON-EMPTY")

    report = rebuild_drake_identities(seeded.connection)

    assert seeded.row(stale) is not None, "a stale identity must never be silently deleted"
    assert stale in report.stale_retained
    assert stale not in report.stale_retained_linked


def test_a_stale_linked_identity_is_retained_and_flagged_for_review(seeded):
    person = seeded.person("Vanished Source")
    stale = uuid.uuid4().hex
    seeded.identity(stale, person_id=person)
    seeded.contacts(uuid.uuid4().hex, [2021], taxpayer="KEEPS THE SOURCE NON-EMPTY")

    report = rebuild_drake_identities(seeded.connection)

    assert seeded.row(stale)["primary_person_id"] == person
    assert stale in report.stale_retained_linked
    assert any(stale in line for line in report.lines())


# ==================================================================================================
# 9. Idempotence.
# ==================================================================================================

def test_the_rebuild_is_idempotent(seeded):
    person = seeded.person("Idempotent")
    identifier_hash = uuid.uuid4().hex
    seeded.identity(identifier_hash, person_id=person, first_year=1990, taxpayer_name="stale")
    seeded.contacts(identifier_hash, [2021, 2022], taxpayer="STABLE")

    rebuild_drake_identities(seeded.connection)
    once = snapshot(seeded.connection, "drake_identity", order="identifier_hash")
    second = rebuild_drake_identities(seeded.connection)
    twice = snapshot(seeded.connection, "drake_identity", order="identifier_hash")

    assert once == twice
    assert second.inserted == 0, "a second run must discover nothing new"


# ==================================================================================================
# 12-14. The rebuild touches drake_identity and nothing else.
# ==================================================================================================

@pytest.mark.parametrize("table,order", [
    ("people", "id"),
    ("person_source_links", "id"),
    ("person_merge_history", "id"),
])
def test_the_rebuild_changes_no_other_table(seeded, table, order):
    person = seeded.person("Bystander")
    identifier_hash = uuid.uuid4().hex
    seeded.identity(identifier_hash, person_id=person)
    seeded.contacts(identifier_hash, [2021, 2022], taxpayer="BYSTANDER")
    before = snapshot(seeded.connection, table, order=order)

    rebuild_drake_identities(seeded.connection)

    assert snapshot(seeded.connection, table, order=order) == before
