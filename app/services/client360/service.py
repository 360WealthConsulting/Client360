"""Client 360 Workspace orchestrator (Phase D.40).

``get_workspace`` verifies record scope ONCE at the boundary (returns None → route 404), builds a shared
context (subject row, portfolio, household, last contact / next activity, scope ids), then fans out to
the capability-gated section builders (each timed + fail-closed), the compact snapshot, the relationship
graph, and the deep-link quick actions. Read-only, RBAC/record-scope preserving, never mutating.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from app.security.authorization import record_in_scope

from . import snapshot as snapshot_mod
from .registry import SECTIONS, visible_quick_actions, visible_sections


def _subject(entity_type, entity_id):
    from sqlalchemy import select

    from app.db import engine, households, people
    tbl = people if entity_type == "person" else households
    with engine.connect() as c:
        row = c.execute(select(tbl).where(tbl.c.id == entity_id)).mappings().first()
    return dict(row) if row else None


def _members(household_id):
    from sqlalchemy import select

    from app.db import engine, household_relationships, people
    with engine.connect() as c:
        rows = c.execute(
            select(people.c.id, people.c.full_name, household_relationships.c.relationship_type,
                   household_relationships.c.is_primary)
            .select_from(household_relationships.join(people,
                         people.c.id == household_relationships.c.person_id))
            .where(household_relationships.c.household_id == household_id)).mappings().all()
    return [dict(r) for r in rows]


def _contact_and_next(scope_ids, ctx):
    """Last contact + next scheduled activity, derived from the client's timeline (no new data)."""
    from app.services.timeline import recent_events
    now = datetime.now(UTC)
    try:
        events = recent_events(scope_ids, limit=50)
    except Exception:
        return None, None
    relevant = [e for e in events if _event_matches(e, ctx)]
    past = [e for e in relevant if e.get("event_time") and e["event_time"] <= now]
    future = [e for e in relevant
              if e.get("event_type") == "calendar_event" and e.get("event_time") and e["event_time"] > now]
    last = max(past, key=lambda e: e["event_time"]) if past else None
    nxt = min(future, key=lambda e: e["event_time"]) if future else None

    def _fmt(e):
        return {"title": e.get("title"), "event_time": str(e.get("event_time")),
                "event_type": e.get("event_type")} if e else None
    return _fmt(last), _fmt(nxt)


def _event_matches(e, ctx):
    pid, hid = ctx.get("person_id"), ctx.get("household_id")
    return (pid and e.get("person_id") == pid) or (hid and e.get("household_id") == hid)


def get_workspace(principal, *, person_id=None, household_id=None, page=1, section_timings=True):
    """Compose the Client 360 workspace. Returns None if the client is out of record scope.

    The household path (Phase D.41) delegates to the full Household 360 workspace builder — one entry
    point, no forked household implementation."""
    if person_id:
        entity_type, entity_id = "person", int(person_id)
    elif household_id:
        from .household import get_household_workspace
        return get_household_workspace(principal, household_id, page=page)
    else:
        return None
    if not record_in_scope(principal, entity_type, entity_id):
        return None

    subject = _subject(entity_type, entity_id)
    if subject is None:
        return None

    # Resolve household + portfolio + member roster (reusing authoritative reads).
    if entity_type == "person":
        household_id = subject.get("household_id")
        from app.services.portfolio import get_person_portfolio
        portfolio = _safe(lambda: get_person_portfolio(entity_id), {})
        members = _members(household_id) if household_id else []
        scope_ids = {entity_id}
    else:
        from app.services.portfolio import get_household_portfolio
        portfolio = _safe(lambda: get_household_portfolio(entity_id), {})
        members = _members(entity_id)
        scope_ids = {m["id"] for m in members} or {-1}

    ctx = {"entity_type": entity_type, "entity_id": entity_id, "person_id": person_id and entity_id,
           "household_id": household_id, "subject": subject, "portfolio": portfolio,
           "members": members, "scope_ids": scope_ids, "page": page,
           "household_name": None}
    if household_id and entity_type == "person":
        hh = _subject("household", household_id)
        ctx["household_name"] = hh.get("name") if hh else None
    elif entity_type == "household":
        ctx["household_name"] = subject.get("name")

    # Snapshot (person-keyed reads reuse get_client_snapshot).
    ctx["snapshot"] = _client_snapshot(principal, ctx)
    ctx["last_contact"], ctx["next_activity"] = _contact_and_next(scope_ids, ctx)

    built, timings, suppressed = {}, {}, []
    for sect in SECTIONS:
        if sect.capability is not None and not principal.can(sect.capability):
            suppressed.append(sect.key)
            continue
        t0 = time.perf_counter()
        try:
            built[sect.key] = sect.builder(principal, ctx)
        except Exception as exc:   # per-section failure isolation
            built[sect.key] = {"error": str(exc)}
        if section_timings:
            timings[sect.key] = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "entity_type": entity_type, "entity_id": entity_id, "subject": subject,
        "household_id": household_id, "household_name": ctx["household_name"],
        "display_name": subject.get("full_name") or subject.get("name") or f"{entity_type} {entity_id}",
        "snapshot": snapshot_mod.build(principal, ctx),
        "sections": built,
        "section_keys": [s.key for s in visible_sections(principal)],
        "suppressed_sections": suppressed,
        "quick_actions": visible_quick_actions(principal, ctx["person_id"], household_id),
        "timings": timings,
        "relationship_graph": built.get("relationships", {}).get("graph"),
    }


def _client_snapshot(principal, ctx):
    pid, hid = ctx.get("person_id"), ctx.get("household_id")
    if not pid:
        # household mode — assemble the comparable figures without a person anchor.
        from app.services.exception_engine import open_count_for_client
        p = ctx["portfolio"]
        return {"aum": p.get("aum", p.get("total_aum")) or 0, "cash": p.get("cash") or 0,
                "insurance": {"policy_count": 0, "total_face": 0}, "tax": {"active": 0},
                "open_exceptions": _safe(lambda: open_count_for_client(None, hid), 0), "open_tasks": 0}
    from app.services.advisor_workspace import get_client_snapshot
    return _safe(lambda: get_client_snapshot(pid, hid, portfolio=ctx["portfolio"], open_task_count=0), {})


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default
