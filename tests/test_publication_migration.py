"""docpub01 — structural reversibility, constraints, and the refusal that protects client access.

CI already walks the whole graph (``scripts/check_migrations_reversible.sh``: head → base → head), so
this file is not a second copy of that. It pins the three things specific to this migration:

  * upgrade / downgrade / upgrade round-trips cleanly around docpub01 itself, leaving the schema
    exactly where it started;
  * the constraints that carry the safety properties really exist in the database — an audience row
    cannot be ambiguous, and one audience cannot hold two live publications of the same document;
  * the downgrade REFUSES while live client-visible publications exist, so a schema rollback can
    never silently withdraw client access and delete the evidence that it was ever granted.

Every test restores the schema to head in a finally block, so a failure here cannot leave the rest
of the suite running against a half-migrated database.
"""
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, insert, inspect, select, text

from app.db import documents, engine, households, people

REVISION = "docpub01"
PREVIOUS = "docpipe01"
TABLES = ("document_publications", "document_publication_events")


def _alembic_config():
    """An Alembic config that does NOT carry the ini file path.

    ``migrations/env.py`` calls ``fileConfig(config.config_file_name)`` when one is set, and
    ``fileConfig`` defaults to ``disable_existing_loggers=True`` — so running a migration in-process
    from a test silently disables every logger created before it, for the rest of the session. Two
    unrelated suites that assert on captured log output then fail, hundreds of tests later, with no
    visible connection to migrations.

    Leaving ``config_file_name`` as None skips that branch entirely. Nothing is lost: env.py sets
    ``sqlalchemy.url`` from ``app.database.schema.DATABASE_URL`` itself, and the only other value
    this needs from the ini is ``script_location``, set explicitly here.
    """
    cfg = Config()
    cfg.set_main_option("script_location", "migrations")
    return cfg


def _table_names():
    # A fresh inspector each call: the cached one would not see a table the migration just dropped.
    return set(inspect(engine).get_table_names())


def _at_head():
    command.upgrade(_alembic_config(), "head")


@pytest.fixture
def schema_restored():
    """Whatever the test does to the schema, end at head."""
    try:
        yield
    finally:
        _at_head()


# --- the revision sits where it claims to ------------------------------------

def test_revision_is_the_single_head_and_follows_drake03():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    heads = list(script.get_heads())
    assert heads == [REVISION], f"expected a single head {REVISION!r}, found {heads}"
    assert script.get_revision(REVISION).down_revision == PREVIOUS


def test_manifest_records_the_new_head():
    from pathlib import Path

    import yaml

    manifest = yaml.safe_load(Path("docs/platform_architecture_manifest.yaml").read_text(
        encoding="utf-8"))
    assert manifest["meta"]["migration_head"] == REVISION


# --- upgrade / downgrade / upgrade -------------------------------------------

def test_running_a_migration_here_does_not_disable_other_loggers(schema_restored):
    """Guards the fix in :func:`_alembic_config`.

    ``fileConfig`` disables every existing logger by default. When this test file ran migrations
    through a config that carried the ini path, it silently switched off logging for the rest of the
    session, and suites that assert on captured output failed far away with an empty log and no
    apparent cause. This asserts the property directly rather than leaving it to be rediscovered.
    """
    import logging

    canary = logging.getLogger("client360.migration_logging_canary")
    assert canary.disabled is False

    command.downgrade(_alembic_config(), PREVIOUS)
    command.upgrade(_alembic_config(), REVISION)

    assert canary.disabled is False, (
        "running a migration disabled an existing logger — _alembic_config must not carry "
        "config_file_name, or env.py's fileConfig() will reconfigure logging for the whole session")


def test_upgrade_downgrade_upgrade_round_trips(schema_restored):
    assert TABLES[0] in _table_names(), "suite should start at head"
    before = _table_names()

    command.downgrade(_alembic_config(), PREVIOUS)
    after_downgrade = _table_names()
    for table in TABLES:
        assert table not in after_downgrade, f"{table} survived the downgrade"

    command.upgrade(_alembic_config(), REVISION)
    after_upgrade = _table_names()
    for table in TABLES:
        assert table in after_upgrade

    assert after_upgrade == before, "the schema must return to exactly its starting shape"


def test_downgrade_removes_only_this_revisions_tables(schema_restored):
    before = _table_names()
    command.downgrade(_alembic_config(), PREVIOUS)
    removed = before - _table_names()
    assert removed == set(TABLES), f"downgrade touched unexpected tables: {removed - set(TABLES)}"


# --- the constraints that carry the safety properties ------------------------

class _Fixture:
    """One person, one household and one canonical document, cleaned up afterwards."""

    def __init__(self):
        suffix = uuid.uuid4().hex[:10]
        with engine.begin() as c:
            self.household_id = c.execute(insert(households).values(
                name=f"Migration HH {suffix}").returning(households.c.id)).scalar_one()
            self.person_id = c.execute(insert(people).values(
                household_id=self.household_id, full_name=f"Migration Person {suffix}",
                active=True).returning(people.c.id)).scalar_one()
            self.document_id = c.execute(insert(documents).values(
                original_name="migration.pdf", stored_name=f"{suffix}-migration.pdf",
                storage_path=f"/tmp/{suffix}.pdf", storage_provider="local",
                size_bytes=1, sha256=suffix * 6, status="active", archived=False,
            ).returning(documents.c.id)).scalar_one()

    def cleanup(self):
        def _try(stmt):
            try:
                with engine.begin() as c:
                    c.execute(stmt)
            except Exception:
                pass

        _try(text("DELETE FROM document_publication_events WHERE document_id = :d").bindparams(
            d=self.document_id))
        _try(text("DELETE FROM document_publications WHERE document_id = :d").bindparams(
            d=self.document_id))
        _try(delete(documents).where(documents.c.id == self.document_id))
        _try(delete(people).where(people.c.id == self.person_id))
        _try(delete(households).where(households.c.id == self.household_id))


@pytest.fixture
def fixture():
    f = _Fixture()
    try:
        yield f
    finally:
        f.cleanup()


def _insert_publication(conn, fixture, *, audience_type="person", client_visible=True,
                        revoked=False, person_id=None, household_id=None, organization_id=None):
    return conn.execute(text("""
        INSERT INTO document_publications
            (document_id, audience_type, person_id, household_id, organization_id,
             client_visible, decision_source, revoked_at)
        VALUES (:doc, :audience, :person, :household, :organization,
                :visible, 'staff_manual', CASE WHEN :revoked THEN now() ELSE NULL END)
        RETURNING id
    """).bindparams(
        doc=fixture.document_id, audience=audience_type,
        person=person_id if person_id is not None else (
            fixture.person_id if audience_type == "person" else None),
        household=household_id if household_id is not None else (
            fixture.household_id if audience_type == "household" else None),
        organization=organization_id, visible=client_visible, revoked=revoked)).scalar_one()


def test_audience_anchor_constraint_rejects_an_ambiguous_row(fixture):
    """A row claiming one audience type while carrying another's anchor cannot exist."""
    with pytest.raises(Exception) as exc:
        with engine.begin() as c:
            _insert_publication(c, fixture, audience_type="person",
                                person_id=fixture.person_id, household_id=fixture.household_id)
    assert "ck_document_publications_audience_anchor" in str(exc.value)


def test_audience_anchor_constraint_rejects_a_row_with_no_anchor(fixture):
    with pytest.raises(Exception) as exc:
        with engine.begin() as c:
            c.execute(text("""
                INSERT INTO document_publications
                    (document_id, audience_type, client_visible, decision_source)
                VALUES (:doc, 'person', true, 'staff_manual')
            """).bindparams(doc=fixture.document_id))
    assert "ck_document_publications_audience_anchor" in str(exc.value)


def test_one_live_publication_per_document_and_audience(fixture):
    with engine.begin() as c:
        _insert_publication(c, fixture)
    with pytest.raises(Exception) as exc:
        with engine.begin() as c:
            _insert_publication(c, fixture)
    assert "uq_document_publications_live_person" in str(exc.value)


def test_a_revoked_publication_frees_the_audience_slot(fixture):
    """Re-publishing after a withdrawal is a fresh decision, not a blocked one."""
    with engine.begin() as c:
        _insert_publication(c, fixture, revoked=True)
        second = _insert_publication(c, fixture)
    assert second is not None

    with engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM document_publications WHERE document_id = :d"
        ).bindparams(d=fixture.document_id)).scalar_one() == 2


def test_the_same_document_may_be_published_to_two_audiences(fixture):
    """Requirement 4, at the schema level: person and household grants coexist."""
    with engine.begin() as c:
        a = _insert_publication(c, fixture, audience_type="person")
        b = _insert_publication(c, fixture, audience_type="household")
    assert a != b


def test_decision_source_is_constrained(fixture):
    with pytest.raises(Exception) as exc:
        with engine.begin() as c:
            c.execute(text("""
                INSERT INTO document_publications
                    (document_id, audience_type, person_id, client_visible, decision_source)
                VALUES (:doc, 'person', :person, true, 'because_i_said_so')
            """).bindparams(doc=fixture.document_id, person=fixture.person_id))
    assert "ck_document_publications_decision_source" in str(exc.value)


# --- the downgrade refusal ---------------------------------------------------

def test_downgrade_refuses_while_live_publications_exist(fixture, schema_restored):
    """The rollback guard. A schema rollback must never be a silent withdrawal of client access."""
    with engine.begin() as c:
        _insert_publication(c, fixture, client_visible=True)

    with pytest.raises(RuntimeError) as exc:
        command.downgrade(_alembic_config(), PREVIOUS)
    assert "live client-visible publication" in str(exc.value)

    # And the tables are still there — the refusal happened before any DDL ran.
    assert TABLES[0] in _table_names()


def test_downgrade_proceeds_once_publications_are_revoked(fixture, schema_restored):
    """The documented rollback procedure, executed: revoke first, then roll back."""
    with engine.begin() as c:
        publication_id = _insert_publication(c, fixture, client_visible=True)

    with engine.begin() as c:
        c.execute(text(
            "UPDATE document_publications SET revoked_at = now(), client_visible = false "
            "WHERE id = :p").bindparams(p=publication_id))

    command.downgrade(_alembic_config(), PREVIOUS)
    assert TABLES[0] not in _table_names()


def test_audit_ledger_outlives_the_document_it_describes(fixture):
    """document_id carries no foreign key, so deleting a document cannot erase the record that it
    was once published — which is exactly the fact an audit would be looking for."""
    with engine.begin() as c:
        publication_id = _insert_publication(c, fixture)
        c.execute(text("""
            INSERT INTO document_publication_events
                (publication_id, document_id, action, audience_type, audience_id, client_visible)
            VALUES (:p, :d, 'published', 'person', :a, true)
        """).bindparams(p=publication_id, d=fixture.document_id, a=fixture.person_id))

    with engine.begin() as c:
        c.execute(delete(documents).where(documents.c.id == fixture.document_id))

    with engine.connect() as c:
        # The publication cascaded away with its document; the ledger entry did not.
        assert c.execute(text(
            "SELECT count(*) FROM document_publications WHERE document_id = :d"
        ).bindparams(d=fixture.document_id)).scalar_one() == 0
        surviving = c.execute(text(
            "SELECT publication_id, action FROM document_publication_events WHERE document_id = :d"
        ).bindparams(d=fixture.document_id)).mappings().all()
    assert len(surviving) == 1
    assert surviving[0]["action"] == "published"
    assert surviving[0]["publication_id"] is None      # SET NULL, not CASCADE


def test_publication_tables_are_bound_in_app_db():
    from app.db import document_publication_events, document_publications

    assert document_publications is not None
    assert document_publication_events is not None
    with engine.connect() as c:
        c.execute(select(func.count()).select_from(document_publications))
