"""E1.3 — Database foundation & migration baseline acceptance tests.

Validation-only (read-only): asserts the invariants that make the existing
database foundation reliable. No schema changes, no new migrations.
"""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def test_single_migration_head():
    """Exactly one Alembic head (a divergent graph breaks `upgrade head`)."""
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert len(heads) == 1, f"expected one head, found {sorted(heads)}"


def test_database_schema_is_at_head():
    """The connected (test) database is migrated to the single head."""
    from app.db import engine

    heads = set(ScriptDirectory.from_config(_alembic_config()).get_heads())
    with engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    assert current == heads, f"schema at {current}, head is {heads}"


def test_declared_tables_all_exist_in_database():
    """Every table declared in target_metadata (schema.py) exists in the DB.

    A declared table missing from the DB means schema.py and the migrations have
    diverged. (Equality is intentionally NOT asserted — target_metadata is a
    partial declaration; see docs/DATABASE.md and test_partial_metadata_is_known.)
    """
    from app.database.schema import metadata as declared
    from app.db import metadata as reflected

    reflected_tables = set(reflected.tables) - {"alembic_version"}
    missing = set(declared.tables) - reflected_tables
    assert not missing, f"declared tables missing from the DB: {sorted(missing)}"


def test_every_table_has_a_primary_key():
    """Schema hygiene: no table lacks a primary key."""
    from app.db import metadata as reflected

    no_pk = sorted(
        name for name, table in reflected.tables.items()
        if name != "alembic_version" and not table.primary_key.columns
    )
    assert not no_pk, f"tables without a primary key: {no_pk}"


def test_partial_metadata_is_known():
    """The partial-metadata condition is a documented invariant, not drift.

    target_metadata declares fewer tables than the DB holds because later
    domains are created by hand-written migrations. This test pins that fact so
    that anyone who makes autogenerate 'work' by dropping tables trips a failure.
    """
    from app.database.schema import metadata as declared
    from app.db import metadata as reflected

    reflected_tables = set(reflected.tables) - {"alembic_version"}
    # Declared is a proper subset of what the DB holds (hand-written migrations).
    assert set(declared.tables) < reflected_tables


def test_env_exposes_target_metadata_and_url():
    """migrations/env.py imports metadata + DATABASE_URL from app.database.schema."""
    from app.database import schema

    assert hasattr(schema, "metadata")
    assert hasattr(schema, "DATABASE_URL")


def test_consistency_script_present():
    assert (REPO_ROOT / "scripts" / "check_schema_consistency.py").is_file()
    assert (REPO_ROOT / "docs" / "DATABASE.md").is_file()
