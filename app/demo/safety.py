"""Demo database safety guards.

Every destructive or seeding operation MUST pass through these guards. They
refuse to run against anything that is not an obvious throwaway demo database
(name ending in `_demo`) and refuse to run in a production environment. This is
the single most important safety boundary of Developer Demo Mode.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

REQUIRED_SUFFIX = "_demo"


class DemoSafetyError(RuntimeError):
    """Raised when a demo operation is pointed at a non-demo target."""


def database_name(database_url: str) -> str:
    """Extract the database name from a SQLAlchemy/PostgreSQL URL."""
    path = urlsplit(database_url).path or ""
    return path.lstrip("/").split("?")[0]


def assert_demo_database(database_url: str | None = None) -> str:
    """Return the demo DB name, or raise if it is unsafe to touch.

    Refuses unless: an explicit URL/name is present, the database name ends in
    ``_demo``, and the environment is not ``production``.
    """
    if os.getenv("CLIENT360_ENVIRONMENT", "development").lower() == "production":
        raise DemoSafetyError(
            "Refusing to run demo tooling with CLIENT360_ENVIRONMENT=production."
        )
    url = database_url if database_url is not None else os.getenv("DATABASE_URL", "")
    if not url:
        raise DemoSafetyError(
            "DATABASE_URL is not set. Demo tooling only runs against an explicit "
            f"'{REQUIRED_SUFFIX}' database."
        )
    name = database_name(url)
    if not name:
        raise DemoSafetyError(f"Could not determine a database name from: {url!r}")
    if not name.endswith(REQUIRED_SUFFIX):
        raise DemoSafetyError(
            f"Refusing to operate on database {name!r}: demo tooling only touches a "
            f"database whose name ends in '{REQUIRED_SUFFIX}'. Point DATABASE_URL at "
            f"e.g. postgresql://localhost/client360{REQUIRED_SUFFIX}."
        )
    return name


def is_demo_database(database_url: str | None = None) -> bool:
    try:
        assert_demo_database(database_url)
        return True
    except DemoSafetyError:
        return False
