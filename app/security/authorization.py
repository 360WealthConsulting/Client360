"""Canonical record-scope authorization service.

This module is the single entry point for record-level authorization decisions
used by the fixed Release 0.9.7 routes (work assignments, relationships, client
profile pickers, and — via the tax helpers that reuse ``list_engagements`` — tax
returns). It intentionally builds on the existing :func:`has_record_scope`
model in :mod:`app.security.policy` rather than introducing a second, parallel
authorization model: every check here ultimately resolves against the same
``record_assignments`` table and the same ``record.read_all`` /
``record.write_all`` bypass capabilities.

Layering note: helpers raise plain :class:`PermissionError` (services) or return
booleans/sets. Route handlers translate those into HTTP responses. This module
never imports FastAPI so it stays usable from any layer.
"""
from datetime import date

from sqlalchemy import and_, false as sql_false, or_, select

from app.db import engine, people, record_assignments, team_memberships
from app.security.policy import has_record_scope

# Assigning a user to one of these entity types grants access to a client record
# itself, so it is treated as assignment administration rather than ordinary work.
CLIENT_ENTITY_TYPES = frozenset({"person", "household"})


def _active(table):
    today = date.today()
    return and_(
        table.c.effective_date <= today,
        or_(table.c.inactive_date.is_(None), table.c.inactive_date >= today),
    )


def team_ids(connection, principal):
    """Active team memberships for the principal."""
    return list(
        connection.scalars(
            select(team_memberships.c.team_id).where(
                team_memberships.c.user_id == principal.user_id,
                _active(team_memberships),
            )
        )
    )


def record_in_scope(principal, entity_type, entity_id, *, write=False, connection=None):
    """Canonical record-scope check. Delegates to :func:`has_record_scope`."""
    if entity_id is None:
        return False
    if connection is not None:
        return has_record_scope(
            connection, principal, entity_type, entity_id,
            record_assignments=record_assignments, write=write,
        )
    with engine.connect() as conn:
        return has_record_scope(
            conn, principal, entity_type, entity_id,
            record_assignments=record_assignments, write=write,
        )


def organization_in_scope(principal, organization_id, *, write=False, connection=None):
    """Organization-anchored record scope (Release 0.9.11 / ADR-18).

    ``record.read_all`` / ``record.write_all`` bypass; otherwise the principal must
    hold a **user or team** assignment on the organization
    (``record_assignments`` with ``entity_type='organization'``). Resolves against
    the same assignment table and bypass capabilities as every other check here —
    it is not a second authorization model, just a new anchor entity type.
    """
    if organization_id is None:
        return False
    bypass = "record.write_all" if write else "record.read_all"
    if principal.can(bypass):
        return True

    def _check(conn):
        tids = team_ids(conn, principal)
        scope = or_(
            record_assignments.c.user_id == principal.user_id,
            record_assignments.c.team_id.in_(tids) if tids else sql_false(),
        )
        return conn.scalar(
            select(record_assignments.c.id).where(
                record_assignments.c.entity_type == "organization",
                record_assignments.c.entity_id == organization_id,
                _active(record_assignments), scope,
            ).limit(1)
        ) is not None

    if connection is not None:
        return _check(connection)
    with engine.connect() as conn:
        return _check(conn)


def benefits_in_scope(principal, *, organization_id=None, person_id=None,
                      household_id=None, write=False, connection=None):
    """Benefits record scope: in scope via the organization anchor (team-aware) OR
    an assigned person OR an assigned household. Firm-wide readers/writers bypass."""
    if organization_id is not None and organization_in_scope(
            principal, organization_id, write=write, connection=connection):
        return True
    for entity_type, entity_id in (("person", person_id), ("household", household_id)):
        if entity_id is not None and record_in_scope(
                principal, entity_type, entity_id, write=write, connection=connection):
            return True
    return False


def assignment_manageable(connection, principal, assignment_row):
    """True if the principal may reassign/remove an existing assignment row.

    Assignment administrators (``assignment.manage``) and firm-wide writers
    (``record.write_all``) may manage any assignment; everyone else may only
    manage assignments they themselves hold or that belong to one of their
    active teams. This preserves least privilege while preventing a user from
    manipulating another user's assignments (H8).
    """
    if principal.can("assignment.manage") or principal.can("record.write_all"):
        return True
    if assignment_row["user_id"] == principal.user_id:
        return True
    team_id = assignment_row["team_id"]
    return team_id is not None and team_id in team_ids(connection, principal)


def accessible_person_ids(connection, principal):
    """Person ids the principal may see, or ``None`` for unrestricted access.

    ``None`` is returned when the principal holds ``record.read_all`` (they may
    see every person). Otherwise the returned set is limited to people the
    principal is assigned to directly or via a team, plus people belonging to
    households the principal is assigned to. Used to scope client pickers/search
    helpers so they cannot enumerate the whole firm (H6).
    """
    if principal.can("record.read_all"):
        return None
    tids = team_ids(connection, principal)
    scope = or_(
        record_assignments.c.user_id == principal.user_id,
        record_assignments.c.team_id.in_(tids) if tids else sql_false(),
    )
    active = _active(record_assignments)
    person_ids = set(
        connection.scalars(
            select(record_assignments.c.entity_id).where(
                record_assignments.c.entity_type == "person", active, scope
            )
        )
    )
    household_ids = set(
        connection.scalars(
            select(record_assignments.c.entity_id).where(
                record_assignments.c.entity_type == "household", active, scope
            )
        )
    )
    if household_ids:
        person_ids |= set(
            connection.scalars(
                select(people.c.id).where(people.c.household_id.in_(household_ids))
            )
        )
    return person_ids
