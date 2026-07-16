"""Demo database safety guards.

Every destructive or seeding operation MUST pass through these guards. They
refuse to run against anything that is not an obvious throwaway demo database
(name ending in `_demo`) and refuse to run in a production environment. This is
the single most important safety boundary of Developer Demo Mode.

The mechanism now lives in `app/safety.py` so the test harness and the restore
rehearsal reuse it rather than copy it. This module keeps its own name, suffix
and exception type — callers (`app/demo/seed.py`, `app/demo/demo_app.py`,
`app/demo/smoke.py`, `scripts/demo.sh`) are unchanged.
"""
from __future__ import annotations

from app.safety import DatabaseSafetyError, assert_database_suffix

REQUIRED_SUFFIX = "_demo"


class DemoSafetyError(DatabaseSafetyError):
    """Raised when a demo operation is pointed at a non-demo target."""


def assert_demo_database(database_url: str | None = None) -> str:
    """Return the demo DB name, or raise if it is unsafe to touch.

    Refuses unless: an explicit URL/name is present, the database name ends in
    ``_demo``, and the environment is not ``production``.
    """
    return assert_database_suffix(
        REQUIRED_SUFFIX,
        database_url=database_url,
        tool="demo tooling",
        error_cls=DemoSafetyError,
        example=f"client360{REQUIRED_SUFFIX}",
    )


def is_demo_database(database_url: str | None = None) -> bool:
    try:
        assert_demo_database(database_url)
        return True
    except DemoSafetyError:
        return False
