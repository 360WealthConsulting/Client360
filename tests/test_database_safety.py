"""The test suite must be structurally unable to touch a real database.

Before 0.9.13 the suite ran against `client360` — the developer's real database —
because `app/db.py` resolves DATABASE_URL from `app/.env`. Nothing prevented it;
it simply was not noticed. These tests pin the guard that now prevents it, and
pin that the demo guard (in use since 0.9.9) is unchanged by the generalisation.
"""
from __future__ import annotations

import pytest

from app.demo.safety import DemoSafetyError, assert_demo_database, is_demo_database
from app.safety import (
    DatabaseSafetyError,
    RehearsalSafetyError,
    SuiteSafetyError,
    assert_rehearsal_database,
    assert_test_database,
    database_name,
    is_test_database,
)

REAL = "postgresql://localhost/client360"


# --- the boundary that matters -------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        REAL,                                          # the developer's real database
        "postgresql://localhost/client360_demo",       # the demo database is not a test target
        "postgresql://user:pw@prod.example.com/client360_production",
        "postgresql://localhost/client360_backup",
    ],
)
def test_test_suite_refuses_any_non_disposable_database(url):
    with pytest.raises(SuiteSafetyError):
        assert_test_database(url)
    assert is_test_database(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost/client360_test",                          # scripts/test.sh
        "postgresql://postgres:postgres@localhost:5432/client360_ci",     # .github/workflows/ci.yml
        "postgresql://localhost/client360_restore_rehearsal",             # scripts/restore_rehearsal.sh
    ],
)
def test_test_suite_accepts_every_real_disposable_context(url):
    """Each accepted suffix corresponds to a context that creates its own database.

    If this list shrinks, CI or the restore rehearsal breaks — both run pytest
    against a database that is not named `*_test`.
    """
    assert assert_test_database(url) == database_name(url)
    assert is_test_database(url) is True


def test_guard_refuses_production_environment_even_for_a_disposable_name(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    with pytest.raises(SuiteSafetyError, match="production"):
        assert_test_database("postgresql://localhost/client360_test")


def test_guard_refuses_when_database_url_is_absent(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SuiteSafetyError, match="DATABASE_URL is not set"):
        assert_test_database(None)


def test_guard_reads_the_ambient_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/client360_test")
    assert assert_test_database() == "client360_test"
    monkeypatch.setenv("DATABASE_URL", REAL)
    with pytest.raises(SuiteSafetyError):
        assert_test_database()


def test_refusal_names_the_database_and_says_what_is_allowed():
    """A guard that refuses without explaining just gets worked around."""
    with pytest.raises(SuiteSafetyError) as exc:
        assert_test_database(REAL)
    message = str(exc.value)
    assert "client360" in message
    assert "_test" in message


# --- the restore rehearsal drops its target, so it needs the same boundary -----

def test_restore_rehearsal_refuses_a_real_database():
    """It runs `dropdb` on its argument — this is the guard standing between a
    mistyped name and the real database."""
    with pytest.raises(RehearsalSafetyError):
        assert_rehearsal_database(REAL)


def test_restore_rehearsal_accepts_its_scratch_default():
    assert assert_rehearsal_database(
        "postgresql://localhost/client360_restore_rehearsal"
    ) == "client360_restore_rehearsal"


# --- the demo guard must be unchanged by the generalisation --------------------

def test_demo_guard_still_accepts_demo_and_refuses_everything_else():
    assert assert_demo_database("postgresql://localhost/client360_demo") == "client360_demo"
    assert is_demo_database("postgresql://localhost/client360_demo") is True
    with pytest.raises(DemoSafetyError):
        assert_demo_database(REAL)
    assert is_demo_database(REAL) is False


def test_demo_error_is_still_catchable_as_its_own_type_and_the_shared_one():
    """`app/demo/smoke.py` catches DemoSafetyError by name; keep that working."""
    assert issubclass(DemoSafetyError, DatabaseSafetyError)
    with pytest.raises(DemoSafetyError):
        assert_demo_database(REAL)
    with pytest.raises(DatabaseSafetyError):
        assert_demo_database(REAL)


def test_database_name_parses_urls_the_suffix_check_depends_on():
    assert database_name("postgresql://localhost/client360_test") == "client360_test"
    assert database_name("postgresql://u:p@host:5432/client360_ci") == "client360_ci"
    assert database_name("postgresql://localhost/client360_test?sslmode=require") == "client360_test"
