"""The test suite must be structurally unable to touch a real database.

Before 0.9.13 the suite ran against `client360` — the developer's real database —
because `app/db.py` resolves DATABASE_URL from `app/.env`. Nothing prevented it;
it simply was not noticed. These tests pin the guard that now prevents it, and
pin that the demo guard (in use since 0.9.9) is unchanged by the generalisation.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

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


@pytest.mark.parametrize(
    "url",
    [
        REAL,
        "postgresql://prod.example.com/client360_production",
        "postgresql://localhost/client360_demo",
    ],
)
def test_restore_rehearsal_refuses_production_like_targets(url):
    with pytest.raises(RehearsalSafetyError):
        assert_rehearsal_database(url)


def test_restore_rehearsal_refuses_production_environment(monkeypatch):
    monkeypatch.setenv("CLIENT360_ENVIRONMENT", "production")
    with pytest.raises(RehearsalSafetyError, match="production"):
        assert_rehearsal_database("postgresql://localhost/client360_restore_rehearsal")


# --- the script wires the guard in front of `dropdb` ---------------------------
#
# The unit tests above prove the guard's logic. These prove the *script* consults
# it before it destroys anything — which is the property that actually matters,
# since `dropdb --if-exists "$DB"` previously ran on an unvalidated argument.

REHEARSAL_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "restore_rehearsal.sh"


def _run_rehearsal(db, *, force=False, env=None):
    argv = [str(REHEARSAL_SCRIPT)]
    if force:
        argv.append("--force")
    argv += ["/nonexistent-dump-file.sql", db]
    return subprocess.run(
        argv,
        cwd=str(REHEARSAL_SCRIPT.parents[1]),
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_rehearsal_script_refuses_before_it_drops_anything():
    """Deliberately uses a production-*like* name that does not exist.

    If the guard ever regresses, this test must not itself be the thing that
    destroys a real database — so it never names `client360`. The unit tests
    above cover that name, where no dropdb is involved.
    """
    result = _run_rehearsal("client360_production_lookalike")
    assert result.returncode == 2, result.stderr
    assert "REFUSED" in result.stderr
    # Ordering is the point: the guard must fire before the drop, not after.
    assert "(re)creating scratch DB" not in result.stdout


def test_rehearsal_force_does_not_override_the_production_environment():
    """The override is for the name check only — never for production."""
    result = _run_rehearsal(
        "client360_restore_rehearsal",
        force=True,
        env={"CLIENT360_ENVIRONMENT": "production"},
    )
    assert result.returncode == 2, result.stderr
    assert "REFUSED" in result.stderr
    assert "(re)creating scratch DB" not in result.stdout


def test_rehearsal_override_is_explicit_and_unmistakable():
    """`--force` must be spelled out; no short flag, no env-var backdoor."""
    source = REHEARSAL_SCRIPT.read_text()
    assert '"${1:-}" = "--force"' in source
    assert "-f)" not in source
    # A silent override would defeat the guard; the script must announce it loudly.
    assert source.count("WARNING") >= 2


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
