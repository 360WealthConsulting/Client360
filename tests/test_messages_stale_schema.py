"""A database that predates the Communication Hub migration fails CLEARLY, not with a 500.

``app/db.py`` builds its schema with ``metadata.reflect(bind=engine)``, so ``portal_threads.c.topic``
resolves against whatever columns the connected database actually has. On a database that has not been
migrated to ``commhub01`` those columns are absent and the first attribute access raised a bare
``AttributeError: topic`` from inside SQLAlchemy - surfacing as a 500 with a stack trace and no hint
that the real problem was a pending migration. That is exactly the failure mode the demo database hit
while every test stayed green, because the test database is migrated to head.

The guard mirrors the tolerant bind ``app/db.py`` already uses for ``person_merge_history`` and
``resolution_knowledge``: detect the missing schema, refuse with an actionable message. It is not a
fallback and it swallows nothing - the query is unchanged and any other error still propagates.
"""
from __future__ import annotations

import sqlalchemy as sa

from app.portal import communication_hub as hub

# The pre-commhub01 shape of portal_threads, column for column (migration f640a6c4e5f6).
_LEGACY = sa.Table(
    "portal_threads", sa.MetaData(),
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("household_id", sa.Integer),
    sa.Column("person_id", sa.Integer),
    sa.Column("subject", sa.String(255)),
    sa.Column("status", sa.String(30)),
    sa.Column("created_by_portal_account_id", sa.Integer),
    sa.Column("created_by_user_id", sa.Integer),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)


def test_a_current_database_passes_the_guard():
    """The guard must be silent on a migrated database, or it would break every environment."""
    hub._require_hub_schema()


def test_a_stale_database_raises_a_named_actionable_error(monkeypatch):
    monkeypatch.setattr(hub, "portal_threads", _LEGACY)
    try:
        hub._require_hub_schema()
    except hub.CommunicationHubSchemaError as exc:
        message = str(exc)
    else:
        raise AssertionError("the guard did not fire on a pre-commhub01 schema")

    # The message has to name the migration and the remedy, or it is no better than the
    # AttributeError it replaces.
    assert "commhub01" in message
    assert "alembic upgrade head" in message
    assert "topic" in message


def test_the_guard_names_every_missing_column(monkeypatch):
    monkeypatch.setattr(hub, "portal_threads", _LEGACY)
    try:
        hub._require_hub_schema()
    except hub.CommunicationHubSchemaError as exc:
        message = str(exc)
    for column in hub._HUB_THREAD_COLUMNS:
        assert column in message, column


def test_staff_inbox_refuses_rather_than_raising_attribute_error(monkeypatch):
    """The entry point the route calls. Previously this was AttributeError -> 500."""
    monkeypatch.setattr(hub, "portal_threads", _LEGACY)
    principal = object()          # never reached: the guard runs before any query
    try:
        hub.staff_inbox(principal)
    except hub.CommunicationHubSchemaError:
        pass
    except AttributeError as exc:                                   # the old behaviour
        raise AssertionError(f"still raising a bare AttributeError: {exc}") from exc
    else:
        raise AssertionError("staff_inbox did not refuse on a pre-commhub01 schema")


def test_the_guard_covers_the_columns_that_migration_actually_adds():
    """Pins the guard's column list to the migration, so a future column added to commhub01
    without being listed here cannot reintroduce the same opaque 500."""
    import pathlib
    import re

    source = pathlib.Path("migrations/versions/commhub01_communication_hub.py").read_text(encoding="utf-8")
    block = source[source.index("_THREAD_COLUMNS = ("):source.index(")\n\n\ndef upgrade")]
    migration_columns = set(re.findall(r'\(\s*"([a-z_]+)"\s*,\s*sa\.Column', block))

    assert migration_columns, "could not parse the migration's column list"
    # Every guarded column really is one this migration introduces...
    assert set(hub._HUB_THREAD_COLUMNS) <= migration_columns
    # ...and the guard covers everything staff_inbox actually selects from that set.
    import inspect
    selected = inspect.getsource(hub.staff_inbox)
    for column in migration_columns:
        if f"portal_threads.c.{column}" in selected:
            assert column in hub._HUB_THREAD_COLUMNS, column


def test_the_guard_performs_no_database_write_and_no_migration(monkeypatch):
    """The guard is READ-ONLY: it inspects the reflected column set and raises. It must never open a
    connection, issue DDL, write a row, or attempt a migration — a stale environment is reported,
    never silently repaired."""
    monkeypatch.setattr(hub, "portal_threads", _LEGACY)

    def _no_connections(*a, **kw):                       # any DB access at all is a failure
        raise AssertionError("the schema guard opened a database connection")

    monkeypatch.setattr(hub.engine, "connect", _no_connections)
    monkeypatch.setattr(hub.engine, "begin", _no_connections)
    try:
        hub._require_hub_schema()
    except hub.CommunicationHubSchemaError:
        pass
    else:
        raise AssertionError("the guard did not fire")


def test_the_guard_is_not_a_fallback_and_swallows_nothing(monkeypatch):
    """On a migrated database the guard is transparent: an unrelated failure inside staff_inbox must
    still propagate untouched, not be converted into a schema complaint."""
    sentinel = RuntimeError("unrelated database failure")

    def _boom(*a, **kw):
        raise sentinel

    monkeypatch.setattr(hub.engine, "connect", _boom)
    try:
        hub.staff_inbox(object())
    except RuntimeError as exc:
        assert exc is sentinel, "an unrelated error was swallowed or reshaped by the guard"
    else:
        raise AssertionError("expected the unrelated error to propagate")
