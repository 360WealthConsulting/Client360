"""The non-natural Drake identity foundation — constraints, and what it must NOT disturb.

WHAT THESE PIN

``drake_business_identity`` keys on ``(identifier_hash, subject_type)``, not on the hash alone: four
production identifiers are a natural person in early years and an estate later, and both legal
subjects must keep their history. It has no person column at all, so a business identity cannot
require a dummy ``people`` row.

``entity_source_links`` is a second table beside ``person_source_links``, not a polymorphic retrofit.
These tests assert that ``person_source_links`` is untouched — same columns, same constraints, same
``person_id NOT NULL`` — because making it nullable would weaken an invariant across 11,129 rows and
turn every existing reader, including the person-merge registry, into a NULL-handling hazard.

Rows are seeded inside a transaction and rolled back. Nothing is committed.
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import engine
from app.services.link_trust import TRUST_LEVELS
from app.services.relationships import ENTITY_TYPES, create_named_entity

DBI = "drake_business_identity"
ESL = "entity_source_links"

pytestmark = pytest.mark.skipif(
    inspect(engine).get_table_names().count(DBI) == 0,
    reason="migration dbi01 has not been applied to this database",
)


@pytest.fixture()
def conn():
    """A transaction that is always rolled back."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture()
def entity(conn):
    return create_named_entity(conn, "business", f"dbi-test-{uuid.uuid4().hex[:8]} LLC")


def insert_identity(conn, **overrides):
    values = {
        "identifier_hash": uuid.uuid4().hex,
        "subject_type": "business_entity",
        "relationship_entity_id": None,
        "first_year": 2021,
        "last_year": 2023,
        "return_count": 3,
        "subject_name": "TEST ENTITY LLC",
        "return_types": ["1120S"],
        "decedent_identifier_hash": None,
        "trust_level": None,
        "confirmation_source": None,
        "evidence_method": None,
        "confirmed_by_user_id": None,
        "confirmed_at": None,
    }
    values.update(overrides)
    columns = ", ".join(values)
    binds = ", ".join(f":{name}" for name in values)
    return conn.execute(text(
        f"INSERT INTO {DBI} ({columns}) VALUES ({binds}) RETURNING id"),  # noqa: S608
        values).scalar_one()


# ==================================================================================================
# 7. Uniqueness is identifier_hash + subject_type.
# ==================================================================================================

def test_the_same_hash_and_subject_type_cannot_be_inserted_twice(conn):
    identifier_hash = uuid.uuid4().hex
    insert_identity(conn, identifier_hash=identifier_hash, subject_type="business_entity")

    with pytest.raises(IntegrityError):
        insert_identity(conn, identifier_hash=identifier_hash, subject_type="business_entity")


def test_one_hash_may_hold_two_different_subject_types(conn):
    """The decedent/estate case: keying on the hash alone would force one history to be dropped."""
    identifier_hash = uuid.uuid4().hex
    first = insert_identity(conn, identifier_hash=identifier_hash, subject_type="estate_or_trust",
                            first_year=2022, last_year=2022, return_count=1,
                            return_types=["1041"])
    second = insert_identity(conn, identifier_hash=identifier_hash, subject_type="business_entity")

    assert first != second


def test_one_identity_cannot_reference_two_entities(conn, entity):
    """Implied by the unique key, asserted directly: the entity is a column on the unique row."""
    identifier_hash = uuid.uuid4().hex
    insert_identity(conn, identifier_hash=identifier_hash, relationship_entity_id=entity)

    with pytest.raises(IntegrityError):
        insert_identity(conn, identifier_hash=identifier_hash, relationship_entity_id=None)


def test_several_identities_may_share_one_entity(conn, entity):
    """A form change or a second Drake registration legitimately produces two identity rows."""
    insert_identity(conn, relationship_entity_id=entity)
    insert_identity(conn, relationship_entity_id=entity)

    assert conn.execute(text(
        f"SELECT count(*) FROM {DBI} WHERE relationship_entity_id = :e"),  # noqa: S608
        {"e": entity}).scalar() == 2


def test_an_unadjudicated_identity_needs_no_entity(conn):
    """111 of the 150 non-natural identifiers have no entity; NULL means 'not yet adjudicated'."""
    assert insert_identity(conn, relationship_entity_id=None)


# ==================================================================================================
# Constraints.
# ==================================================================================================

def test_the_table_has_no_person_column_at_all(conn):
    """The person/entity boundary is structural, not conventional. Nobody may add one back."""
    columns = {c["name"] for c in inspect(engine).get_columns(DBI)}

    assert not {c for c in columns if "person" in c} - {"relationship_entity_id"}
    assert "primary_person_id" not in columns and "person_id" not in columns


def test_a_natural_person_subject_type_is_rejected(conn):
    with pytest.raises(IntegrityError):
        insert_identity(conn, subject_type="natural_person")


def test_a_reversed_year_range_is_rejected(conn):
    with pytest.raises(IntegrityError):
        insert_identity(conn, first_year=2023, last_year=2021)


def test_a_zero_return_count_is_rejected(conn):
    with pytest.raises(IntegrityError):
        insert_identity(conn, return_count=0)


def test_human_approval_without_an_actor_is_rejected(conn):
    """The gap that forced the D5 relink's actor into an external receipt."""
    with pytest.raises(IntegrityError):
        insert_identity(conn, trust_level="human_approved", confirmation_source="human")


def test_an_unknown_trust_level_is_rejected(conn):
    with pytest.raises(IntegrityError):
        insert_identity(conn, trust_level="totally_made_up")


def test_the_trust_vocabulary_matches_link_trust_exactly(conn):
    """Two divergent trust vocabularies were a real defect once. Pin them together."""
    definition = conn.execute(text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_dbi_trust_level'"
    )).scalar_one()

    for level in TRUST_LEVELS:
        assert f"'{level}'" in definition, f"{level} missing from the check constraint"


def test_the_entity_reference_must_exist(conn):
    with pytest.raises(IntegrityError):
        insert_identity(conn, relationship_entity_id=-424242)


# ==================================================================================================
# 8. entity_source_links targets entities, never people.
# ==================================================================================================

def test_entity_source_links_has_no_person_column(conn):
    columns = {c["name"] for c in inspect(engine).get_columns(ESL)}

    assert "person_id" not in columns
    assert "relationship_entity_id" in columns


def test_entity_source_links_refuses_a_person_id_that_is_not_an_entity(conn):
    person = conn.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"esl-test-{uuid.uuid4().hex[:8]}"}).scalar_one()
    contact = conn.execute(text(
        "INSERT INTO source_contacts (source_system, source_file, source_hash, raw_data) "
        "VALUES ('SyntheticCRM', :f, :h, CAST('{}' AS json)) RETURNING id"),
        {"f": "esl.csv", "h": uuid.uuid4().hex}).scalar_one()
    exists = conn.execute(text(
        "SELECT 1 FROM relationship_entities WHERE id = :i"), {"i": person}).scalar()
    if exists:
        pytest.skip("this person id happens to collide with a real entity id")

    with pytest.raises(IntegrityError):
        conn.execute(text(
            f"INSERT INTO {ESL} (relationship_entity_id, source_contact_id) "  # noqa: S608
            "VALUES (:e, :c)"), {"e": person, "c": contact})


def test_entity_source_links_is_unique_per_entity_and_contact(conn, entity):
    contact = conn.execute(text(
        "INSERT INTO source_contacts (source_system, source_file, source_hash, raw_data) "
        "VALUES ('SyntheticCRM', :f, :h, CAST('{}' AS json)) RETURNING id"),
        {"f": "esl.csv", "h": uuid.uuid4().hex}).scalar_one()
    conn.execute(text(
        f"INSERT INTO {ESL} (relationship_entity_id, source_contact_id) "  # noqa: S608
        "VALUES (:e, :c)"), {"e": entity, "c": contact})

    with pytest.raises(IntegrityError):
        conn.execute(text(
            f"INSERT INTO {ESL} (relationship_entity_id, source_contact_id) "  # noqa: S608
            "VALUES (:e, :c)"), {"e": entity, "c": contact})


def test_entity_source_links_human_approval_needs_an_actor(conn, entity):
    contact = conn.execute(text(
        "INSERT INTO source_contacts (source_system, source_file, source_hash, raw_data) "
        "VALUES ('SyntheticCRM', :f, :h, CAST('{}' AS json)) RETURNING id"),
        {"f": "esl.csv", "h": uuid.uuid4().hex}).scalar_one()

    with pytest.raises(IntegrityError):
        conn.execute(text(
            f"INSERT INTO {ESL} (relationship_entity_id, source_contact_id, trust_level) "  # noqa: S608
            "VALUES (:e, :c, 'human_approved')"), {"e": entity, "c": contact})


def test_one_source_contact_may_link_to_both_a_person_and_an_entity(conn, entity):
    """Not a defect: a Drake contact for a jointly-held business genuinely has both meanings."""
    person = conn.execute(text(
        "INSERT INTO people (full_name, active) VALUES (:n, true) RETURNING id"),
        {"n": f"both-{uuid.uuid4().hex[:8]}"}).scalar_one()
    contact = conn.execute(text(
        "INSERT INTO source_contacts (source_system, source_file, source_hash, raw_data) "
        "VALUES ('SyntheticCRM', :f, :h, CAST('{}' AS json)) RETURNING id"),
        {"f": "both.csv", "h": uuid.uuid4().hex}).scalar_one()

    conn.execute(text(
        "INSERT INTO person_source_links (person_id, source_contact_id) VALUES (:p, :c)"),
        {"p": person, "c": contact})
    conn.execute(text(
        f"INSERT INTO {ESL} (relationship_entity_id, source_contact_id) "  # noqa: S608
        "VALUES (:e, :c)"), {"e": entity, "c": contact})


# ==================================================================================================
# 9-11. Nothing that already existed is disturbed.
# ==================================================================================================

def test_person_source_links_is_structurally_unchanged(conn):
    inspector = inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("person_source_links")}

    assert columns["person_id"]["nullable"] is False, "person_id must stay NOT NULL"
    assert "relationship_entity_id" not in columns, "no polymorphic retrofit"
    assert {"trust_level", "confirmation_source", "evidence_method", "confirmed_by_user_id",
            "confirmed_at"} <= set(columns)
    names = {c["name"] for c in inspector.get_unique_constraints("person_source_links")}
    assert "uq_person_source_link" in names


def test_this_migration_writes_no_rows(conn):
    """Both tables are created empty; the capability is dormant until a later phase."""
    assert conn.execute(text(f"SELECT count(*) FROM {DBI}")).scalar() == 0  # noqa: S608
    assert conn.execute(text(f"SELECT count(*) FROM {ESL}")).scalar() == 0  # noqa: S608


def test_drake_identity_keeps_its_shape_and_its_links(conn):
    """No drake_identity row is moved, and its person column is untouched by this phase."""
    columns = {c["name"] for c in inspect(engine).get_columns("drake_identity")}

    assert "primary_person_id" in columns
    assert "subject_type" not in columns and "relationship_entity_id" not in columns


def test_the_missing_drake_identity_foreign_key_is_still_absent(conn):
    """Deliberately out of scope here: a separate hardening decision on a populated table."""
    keys = inspect(engine).get_foreign_keys("drake_identity")

    assert not any(k["constrained_columns"] == ["primary_person_id"] for k in keys)


# ==================================================================================================
# Estate / trust convention.
# ==================================================================================================

def test_estate_is_no_longer_a_creatable_entity_type(conn):
    """Production stores every estate as entity_type='trust'; the second bucket is closed."""
    assert "estate" not in ENTITY_TYPES
    with pytest.raises(ValueError):
        create_named_entity(conn, "estate", "Some Estate")


def test_trust_is_still_creatable(conn):
    assert create_named_entity(conn, "trust", f"dbi-trust-{uuid.uuid4().hex[:8]} Estate")


def test_the_organization_allowlists_cannot_drift(conn):
    from app.services.organization_service import ORG_ENTITY_TYPES

    assert ORG_ENTITY_TYPES is ENTITY_TYPES


@pytest.mark.parametrize("form", ["estate", "revocable_trust", "irrevocable_trust"])
def test_entity_form_now_carries_the_estate_trust_subtype(conn, entity, form):
    conn.execute(text(
        "INSERT INTO organization_profiles (relationship_entity_id, entity_form) "
        "VALUES (:e, :f)"), {"e": entity, "f": form})


def test_entity_form_still_rejects_an_unknown_value(conn, entity):
    with pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO organization_profiles (relationship_entity_id, entity_form) "
            "VALUES (:e, 'not_a_real_form')"), {"e": entity})


# ==================================================================================================
# 13. The migration graph has exactly one head.
# ==================================================================================================

def test_the_migration_graph_has_exactly_one_head():
    """Asked of Alembic, not of a regex: some revisions pack ``revision=...; down_revision=...``
    onto one line, which defeats line-anchored parsing — the same trap
    ``scripts/check_migration_heads.sh`` documents."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    scripts = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    heads = set(scripts.get_heads())

    # The pin moves with every migration added on top — that is the point: a second head has to be
    # noticed deliberately, not absorbed by a laxer assertion. ``dbi01`` -> ``drake03`` (the 1120S
    # short-row re-key) -> ``docpipe01`` (the continuous document pipeline); see the next test and
    # ``tests/test_drake_1120s_short_row_repair.py``.
    assert heads == {"docpub01"}, f"expected exactly one head (docpub01), found {sorted(heads)}"


def test_dbi01_descends_from_the_previous_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    scripts = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))

    assert scripts.get_revision("dbi01").down_revision == "emailnorm01"
