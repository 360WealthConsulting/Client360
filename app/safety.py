"""Database target guards.

Destructive or seeding tooling must never point at a real database. Every such
tool routes through :func:`assert_database_suffix`, which refuses anything whose
name does not carry an expected throwaway suffix, and refuses to run in a
production environment at all.

This generalises the guard that Developer Demo Mode has used since 0.9.9
(`app/demo/safety.py`, which now delegates here) so the test harness and the
restore rehearsal can reuse it rather than copy it.

The suffix is the whole point: `client360_test` is obviously disposable,
`client360` obviously is not, and the difference is mechanical rather than a
matter of remembering.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit


class DatabaseSafetyError(RuntimeError):
    """Raised when tooling is pointed at a database it must not touch."""


class SuiteSafetyError(DatabaseSafetyError):
    """Raised when the test suite is pointed at a non-throwaway database.

    Named Suite* rather than Test* so pytest does not try to collect it.
    """


class RehearsalSafetyError(DatabaseSafetyError):
    """Raised when the restore rehearsal is pointed at a non-scratch database."""


# Suffixes that mark a database as disposable. Each corresponds to a real
# context that creates and destroys its own database:
#   _test              scripts/test.sh and local runs
#   _ci                the CI service container (.github/workflows/ci.yml)
#   _restore_rehearsal scripts/restore_rehearsal.sh scratch target
DISPOSABLE_SUFFIXES = ("_test", "_ci", "_restore_rehearsal")

REHEARSAL_SUFFIXES = ("_restore_rehearsal", "_test", "_ci")


def database_name(database_url: str) -> str:
    """Extract the database name from a SQLAlchemy/PostgreSQL URL."""
    path = urlsplit(database_url).path or ""
    return path.lstrip("/").split("?")[0]


def assert_database_suffix(
    required_suffixes,
    *,
    database_url: str | None = None,
    tool: str = "this tooling",
    error_cls: type[DatabaseSafetyError] = DatabaseSafetyError,
    example: str = "client360_test",
) -> str:
    """Return the target database name, or raise if it is unsafe to touch.

    Refuses unless: the environment is not ``production``, a URL is present, a
    name is parseable from it, and the name ends in one of ``required_suffixes``.
    """
    if isinstance(required_suffixes, str):
        required_suffixes = (required_suffixes,)

    if os.getenv("CLIENT360_ENVIRONMENT", "development").lower() == "production":
        raise error_cls(f"Refusing to run {tool} with CLIENT360_ENVIRONMENT=production.")

    url = database_url if database_url is not None else os.getenv("DATABASE_URL", "")
    if not url:
        raise error_cls(
            f"DATABASE_URL is not set. {tool} only runs against an explicit "
            f"database whose name ends in {_join(required_suffixes)}."
        )

    name = database_name(url)
    if not name:
        raise error_cls(f"Could not determine a database name from: {url!r}")

    if not name.endswith(tuple(required_suffixes)):
        raise error_cls(
            f"Refusing to operate on database {name!r}: {tool} only touches a database "
            f"whose name ends in {_join(required_suffixes)}. Point DATABASE_URL at "
            f"e.g. postgresql://localhost/{example}."
        )

    return name


def assert_test_database(database_url: str | None = None) -> str:
    """Guard the test suite. Raises unless the target is disposable."""
    return assert_database_suffix(
        DISPOSABLE_SUFFIXES,
        database_url=database_url,
        tool="the test suite",
        error_cls=SuiteSafetyError,
        example="client360_test",
    )


def is_test_database(database_url: str | None = None) -> bool:
    try:
        assert_test_database(database_url)
        return True
    except SuiteSafetyError:
        return False


def assert_rehearsal_database(database_url: str | None = None) -> str:
    """Guard the restore rehearsal, which drops its target before restoring."""
    return assert_database_suffix(
        REHEARSAL_SUFFIXES,
        database_url=database_url,
        tool="the restore rehearsal",
        error_cls=RehearsalSafetyError,
        example="client360_restore_rehearsal",
    )


def _join(suffixes) -> str:
    quoted = [f"'{s}'" for s in suffixes]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + f" or {quoted[-1]}"
