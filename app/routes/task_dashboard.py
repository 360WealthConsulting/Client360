"""The canonical staff task dashboard — the ONE staff surface over the authoritative client
``tasks`` table (ADR-025: ``tasks`` is the authoritative client-task store; ``operational_tasks``
is the separate firm-work store and is not read here).

The list is optionally narrowed to a single client. "Create Task" on a person or household
workspace lands here with that client already applied, so staff never re-search the record the
workspace already knew. The household narrowing reproduces the household workspace's own Tasks
tab exactly — same members, same authoritative table — so the two surfaces cannot disagree.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, household_relationships, households, people, tasks
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.person_names import person_row_display_name

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _client_scope_label(principal, person_id, household_id):
    """(label, in_scope) for the active client filter.

    The name is resolved ONLY after record scope passes, so an id typed into the query string can
    never reveal an inaccessible person's or household's name. Out of scope returns a neutral
    label and ``False``; the caller then lists nothing rather than falling back to the firm-wide
    list. Mirrors the same helper on /operations/task-list so the two pages behave identically.
    """
    from app.security.authorization import record_in_scope
    if not person_id and not household_id:
        return None, True
    generic = "Tasks for the selected client"
    if person_id and not record_in_scope(principal, "person", person_id):
        return generic, False
    if household_id and not record_in_scope(principal, "household", household_id):
        return generic, False
    with engine.connect() as c:
        if person_id:
            row = c.execute(select(people.c.full_name, people.c.first_name, people.c.last_name)
                            .where(people.c.id == person_id)).mappings().first()
            if row is None:
                return generic, False
            name = person_row_display_name(row)
        else:
            name = c.scalar(select(households.c.name).where(households.c.id == household_id))
            if name is None:
                return generic, False
    return f"Tasks for {name}", True


def _household_member_ids(connection, principal, household_id):
    """The household's members, resolved EXACTLY as the household workspace resolves them.

    A canonical client task is keyed to a person (``tasks.person_id`` is NOT NULL) and
    ``app.services.tasks.create_task`` never writes ``tasks.household_id``, so filtering on that
    column would miss every normally-created task. The household workspace never reads it either:
    its Tasks tab is ``client360.household._tasks`` -> ``sections.tasks`` over ``ctx["member_ids"]``,
    where the roster comes from ``portfolio._household_members`` (the
    ``household_relationships`` -> ``people`` join) narrowed by ``accessible_person_ids``. This
    reproduces that member set — same join, same scope narrowing — rather than inventing a second
    household-task ownership model. A test asserts the two sets are identical.
    """
    from app.security.authorization import accessible_person_ids
    ids = [i for i in connection.scalars(
        select(household_relationships.c.person_id)
        .where(household_relationships.c.household_id == household_id,
               household_relationships.c.person_id.isnot(None))) if i]
    accessible = accessible_person_ids(connection, principal)   # None = unrestricted
    if accessible is not None:
        ids = [i for i in ids if i in accessible]
    return ids


@router.get("/tasks")
def task_dashboard(request: Request, limit: int = 100, offset: int = 0,
                   person_id: int | None = None, household_id: int | None = None,
                   principal: Principal = Depends(require_capability("task.read"))):
    # Bound the read so the page is O(page size), not O(all tasks) (RC9).
    limit = max(1, min(limit, 500)); offset = max(0, offset)
    label, in_scope = _client_scope_label(principal, person_id, household_id)

    task_rows = []
    # Out of scope narrows to nothing. It never widens back to the firm-wide list -- that fallback
    # would turn an unauthorised id into a way to see every client's tasks.
    if in_scope:
        with engine.connect() as connection:
            members = (_household_member_ids(connection, principal, household_id)
                       if household_id else None)
            # Conservative AND. With both ids the person must ALSO be a member of that household,
            # so the member set collapses to that one person (or to nothing, which lists nothing).
            # Never an OR -- a client filter can only ever narrow this list.
            if members is not None and person_id:
                members = [i for i in members if i == person_id]

            stmt = (
                select(
                    tasks,
                    people.c.full_name,
                    people.c.first_name,
                    people.c.last_name,
                )
                .join(
                    people,
                    people.c.id == tasks.c.person_id,
                )
            )
            if household_id:
                stmt = stmt.where(tasks.c.person_id.in_(members))
            elif person_id:
                stmt = stmt.where(tasks.c.person_id == person_id)

            # An empty member set (childless household, or a person who is not a member) matches
            # nothing; skip the query rather than risk an unbounded IN ().
            if members is None or members:
                rows = connection.execute(
                    stmt
                    .order_by(
                        tasks.c.status,
                        tasks.c.priority.desc(),
                        tasks.c.due_date.asc().nullslast(),
                    )
                    .limit(limit)
                    .offset(offset)
                ).mappings().all()
                # full_name is not populated for every person; resolving through the canonical
                # helper renders the real name instead of "None" for first/last-only rows.
                task_rows = [{**r, "person_name": person_row_display_name(r)}
                             for r in rows]

    return templates.TemplateResponse(
        request=request,
        name="tasks/dashboard.html",
        context={
            "tasks": task_rows,
            "limit": limit,
            "offset": offset,
            "heading": label,
            "client_filter": bool(person_id or household_id),
            "client_in_scope": in_scope,
        },
    )
