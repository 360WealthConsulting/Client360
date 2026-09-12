"""The hard database denylist — no tooling may point at production or the shared test database.

The suffix rule alone is a shape check, and a shape check cannot say "that one is spoken for".
``client360_test`` ends in ``_test``, so the suffix rule admits it, and every suite run drops and
recreates its schema — two sessions sharing it corrupt each other. ``client360`` is production.

These tests pin the refusal as behaviour rather than as a comment, including the part that is easy
to regress: the denylist must be checked BEFORE the suffix rule, or a forbidden name that happens to
carry a disposable suffix walks straight through.
"""
import pytest

from app.safety import (
    FORBIDDEN_DATABASES,
    DatabaseSafetyError,
    RehearsalSafetyError,
    SuiteSafetyError,
    assert_database_suffix,
    assert_rehearsal_database,
    assert_test_database,
    database_name,
    is_test_database,
)

FORBIDDEN_URLS = [f"postgresql://localhost/{name}" for name in sorted(FORBIDDEN_DATABASES)]


def test_the_denylist_names_production_and_the_shared_test_database():
    assert FORBIDDEN_DATABASES == frozenset({"client360", "client360_test"})


@pytest.mark.parametrize("url", FORBIDDEN_URLS)
def test_the_test_suite_guard_refuses_every_forbidden_database(url):
    with pytest.raises(SuiteSafetyError) as exc:
        assert_test_database(url)
    assert "forbidden list" in str(exc.value)
    assert not is_test_database(url)


def test_client360_test_is_refused_despite_carrying_a_disposable_suffix():
    """The regression this denylist exists to prevent. The suffix rule would have allowed it."""
    url = "postgresql://localhost/client360_test"
    assert url.endswith("_test")
    with pytest.raises(SuiteSafetyError):
        assert_test_database(url)


@pytest.mark.parametrize("url", FORBIDDEN_URLS)
def test_the_rehearsal_guard_refuses_them_too(url):
    with pytest.raises(RehearsalSafetyError):
        assert_rehearsal_database(url)


def test_the_refusal_is_checked_before_the_suffix_rule():
    """Both error paths raise, so the distinguishing evidence is WHICH message comes back."""
    with pytest.raises(DatabaseSafetyError) as exc:
        assert_database_suffix(("_test",), database_url="postgresql://localhost/client360_test",
                               tool="a tool")
    assert "forbidden list" in str(exc.value)


def test_credentials_and_ports_do_not_smuggle_a_forbidden_name_through():
    with pytest.raises(SuiteSafetyError):
        assert_test_database("postgresql://user:pw@db.internal:5432/client360")


def test_a_branch_specific_database_is_still_accepted():
    url = "postgresql://client360_app@localhost:5432/client360_pubbridge_test"
    assert assert_test_database(url) == "client360_pubbridge_test"
    assert is_test_database(url)


@pytest.mark.parametrize("name", ["client360_ci", "client360_restore_rehearsal",
                                  "client360_anything_test"])
def test_other_disposable_databases_are_unaffected(name):
    assert assert_test_database(f"postgresql://localhost/{name}") == name


def test_the_suggested_example_is_not_itself_forbidden():
    """A refusal that tells the reader to use a database the next check also refuses is a bug."""
    with pytest.raises(SuiteSafetyError) as exc:
        assert_test_database("postgresql://localhost/client360")
    message = str(exc.value)
    suggested = database_name(message.split("postgresql://localhost/")[-1].strip().rstrip("."))
    assert suggested not in FORBIDDEN_DATABASES


def test_this_suite_is_not_running_against_a_forbidden_database():
    """The guard, applied to the run that is happening right now."""
    import os

    from dotenv import load_dotenv

    load_dotenv("app/.env")
    name = database_name(os.getenv("DATABASE_URL", ""))
    assert name not in FORBIDDEN_DATABASES, f"this suite must not run against {name!r}"
