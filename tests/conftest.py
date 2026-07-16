"""Test-suite safety boundary.

Until 0.9.13 the suite ran against the real development database: `app/db.py`
loads `app/.env` → `postgresql://localhost/client360`, and there was no test
database, no fixtures and no cleanup. Every run inserted rows and left them
there. That single fact produced three distinct defects — a securities-symbol
collision, importers writing client data on import, and firm-wide SLA escalation
over thousands of leftover workflow steps — and made the suite 8x slower locally
than in CI.

This module makes the boundary structural rather than a matter of remembering:
the suite **cannot** run against a database that is not obviously disposable.

The guard resolves DATABASE_URL exactly the way `app/db.py` does — `app/.env`
first, environment override second — because `app/.env` is precisely what used to
point the suite at `client360`. Checking `os.environ` alone would miss it.

Run the suite with `scripts/test.sh run` (resets the database first). See #24.
"""
from __future__ import annotations

import os
import warnings

import pytest
from dotenv import load_dotenv

# Must mirror app/db.py's resolution, and must run before app.db is imported
# during collection.
load_dotenv("app/.env")

from app.safety import SuiteSafetyError, assert_test_database  # noqa: E402

SETUP_HINT = "Run `scripts/test.sh setup` to create and migrate the test database."


def pytest_configure(config: pytest.Config) -> None:
    """Refuse to collect a single test against a non-disposable database.

    Runs before collection, so it fires before any test module imports app.db
    and reflects the schema.
    """
    try:
        name = assert_test_database()
    except SuiteSafetyError as exc:
        raise pytest.UsageError(f"{exc}\n\n{SETUP_HINT}") from exc

    _verify_schema(name)


def _verify_schema(name: str) -> None:
    """Fail with a usable message if the test database is missing or unmigrated.

    Without this, a missing database surfaces as an opaque psycopg2 error from
    app/db.py's import-time reflection.
    """
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.exc import SQLAlchemyError

    engine = create_engine(os.environ.get("DATABASE_URL") or "")
    try:
        with engine.connect() as connection:
            if "alembic_version" not in set(inspect(engine).get_table_names()):
                raise pytest.UsageError(
                    f"Database {name!r} has no schema (no alembic_version table).\n\n{SETUP_HINT}"
                )
            leftovers = connection.execute(text("select count(*) from workflow_steps")).scalar()
    except SQLAlchemyError as exc:
        raise pytest.UsageError(
            f"Cannot reach test database {name!r}: {exc.__class__.__name__}.\n\n{SETUP_HINT}"
        ) from exc
    finally:
        engine.dispose()

    if leftovers:
        # Soft signal: leftover rows are what made this suite flaky and slow, but
        # they are not a correctness failure, so warn rather than fail.
        warnings.warn(
            f"{name!r} already holds {leftovers:,} workflow_steps from earlier runs. "
            "Accumulated rows are what made this suite flaky and slow — "
            "`scripts/test.sh reset` starts from a clean schema.",
            stacklevel=2,
        )
