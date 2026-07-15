"""Exception ↔ Work Management assignment integration (Sprint 5.5 Phase 5).

Exceptions are assigned through the EXISTING assignment model (`record_assignments`
via `assign_work`/`reassign_work`/`deactivate_assignment`) — there is no second
assignment model. The `exceptions.owner_user_id/owner_team_id` columns are a
denormalized cache of the primary assignment, and each change also appends an
immutable `exception_events` row for the engine's ledger. Source records are never
mutated beyond the owner cache. Tax domain only; authorization is not widened.
"""
from sqlalchemy import select

from app.db import engine, exception_events, exceptions, record_assignments
from app.security.authorization import record_in_scope
from app.services.work_management import assign_work, deactivate_assignment, reassign_work

_OWNER_ROLES = ("primary", "owner")


class ExceptionAssignmentError(RuntimeError):
    """Invalid exception-assignment request."""


class ExceptionAssignmentAuthError(PermissionError):
    """Missing capability or record scope for an exception assignment."""


def _load(connection, exception_id):
    row = connection.execute(
        select(exceptions.c.id, exceptions.c.domain, exceptions.c.status,
               exceptions.c.tax_engagement_return_id, exceptions.c.person_id,
               exceptions.c.household_id).where(exceptions.c.id == exception_id)
    ).mappings().one_or_none()
    if row is None:
        raise ExceptionAssignmentError(f"Exception {exception_id} not found")
    return row


def _authorize(connection, principal, exc, *, write=True):
    if principal is None:
        return
    if not principal.can("exception.write"):
        raise ExceptionAssignmentAuthError("Missing capability: exception.write")
    if principal.can("record.read_all") or principal.can("record.write_all"):
        return
    for entity_type, entity_id in (("tax_return", exc["tax_engagement_return_id"]),
                                   ("person", exc["person_id"]), ("household", exc["household_id"])):
        if entity_id and record_in_scope(principal, entity_type, entity_id, write=write, connection=connection):
            return
    raise ExceptionAssignmentAuthError("Exception is outside your record scope")


def _sync_owner(exception_id, assignment_role, user_id, team_id):
    """Keep the owner cache in step with the PRIMARY assignment only."""
    if assignment_role in _OWNER_ROLES:
        with engine.begin() as c:
            c.execute(exceptions.update().where(exceptions.c.id == exception_id)
                      .values(owner_user_id=user_id, owner_team_id=team_id))


def _append_event(exception_id, event_type, actor_user_id, metadata):
    with engine.begin() as c:
        status = c.scalar(select(exceptions.c.status).where(exceptions.c.id == exception_id))
        c.execute(exception_events.insert().values(
            exception_id=exception_id, event_type=event_type, from_status=status, to_status=status,
            actor_user_id=actor_user_id, metadata=metadata))


def assign_exception(exception_id, *, principal, assignment_role="primary", user_id=None,
                     team_id=None, actor_user_id=None, reason=None, request_id=None):
    """Assign an owner (primary/secondary/supervisor) or a team to an exception."""
    if assignment_role not in ("primary", "secondary", "supervisor", "owner"):
        raise ExceptionAssignmentError(f"Unsupported assignment role: {assignment_role}")
    if user_id is None and team_id is None:
        raise ExceptionAssignmentError("An assignee user or team is required")
    with engine.connect() as c:
        _authorize(c, principal, _load(c, exception_id))
    assignment_id = assign_work(entity_type="exception", entity_id=exception_id,
                                assignment_role=assignment_role, actor_user_id=actor_user_id,
                                user_id=user_id, team_id=team_id, reason=reason, request_id=request_id)
    _sync_owner(exception_id, assignment_role, user_id, team_id)
    _append_event(exception_id, "assigned", actor_user_id,
                  {"assignment_id": assignment_id, "role": assignment_role,
                   "user_id": user_id, "team_id": team_id, "reason": reason})
    return assignment_id


def _assignment(connection, assignment_id):
    a = connection.execute(select(record_assignments.c.entity_type, record_assignments.c.entity_id,
                                  record_assignments.c.assignment_type)
                           .where(record_assignments.c.id == assignment_id)).mappings().one_or_none()
    if a is None or a["entity_type"] != "exception":
        raise ExceptionAssignmentError("Not an exception assignment")
    return a


def reassign_exception(assignment_id, *, principal, user_id=None, team_id=None,
                       actor_user_id=None, reason=None, request_id=None):
    with engine.connect() as c:
        a = _assignment(c, assignment_id)
        _authorize(c, principal, _load(c, a["entity_id"]))
    new_id = reassign_work(assignment_id, actor_user_id=actor_user_id, user_id=user_id,
                           team_id=team_id, reason=reason, request_id=request_id)
    if a["assignment_type"] in _OWNER_ROLES:
        _sync_owner(a["entity_id"], "primary", user_id, team_id)
    _append_event(a["entity_id"], "assigned", actor_user_id,
                  {"assignment_id": new_id, "reassigned_from": assignment_id,
                   "user_id": user_id, "team_id": team_id, "reason": reason})
    return new_id


def remove_exception_assignment(assignment_id, *, principal, actor_user_id=None,
                                reason=None, request_id=None):
    with engine.connect() as c:
        a = _assignment(c, assignment_id)
        _authorize(c, principal, _load(c, a["entity_id"]))
    deactivate_assignment(assignment_id, actor_user_id=actor_user_id, reason=reason, request_id=request_id)
    if a["assignment_type"] in _OWNER_ROLES:
        _sync_owner(a["entity_id"], "primary", None, None)
    _append_event(a["entity_id"], "assignment_removed", actor_user_id,
                  {"assignment_id": assignment_id, "reason": reason})
