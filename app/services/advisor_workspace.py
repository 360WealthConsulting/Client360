"""Advisor Workspace orchestration (Phase D.1).

A thin, read-only composition layer over existing authoritative services — it
creates no new domain, task, workflow, exception, notification, calendar, or
timeline logic and performs no writes. Every panel is record-scoped: tasks and
exceptions come from `work_management.work_items` (already scoped); meetings,
recent activity, and reviews-due are read through authoritative scoped reads keyed
on `accessible_person_ids`. Panels the principal lacks capability for are omitted.

See docs/ADVISOR_WORKSPACE_ARCHITECTURE.md (§1, §7) and
docs/PHASE_D1_ADVISOR_WORKSPACE.md.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import engine, households, people
from app.security.authorization import accessible_person_ids
from app.services.portfolio import accounts_due_for_review
from app.services.timeline import recent_events
from app.services.work_management import work_items

# Firm timezone for "today" boundaries (matches the scheduler's zone).
FIRM_TZ = ZoneInfo("America/New_York")
_CLOSED = {"complete", "completed", "closed", "cancelled", "resolved"}


def _resolve_names(person_ids, household_ids):
    person_ids = {p for p in person_ids if p}
    household_ids = {h for h in household_ids if h}
    people_names, hh_names = {}, {}
    with engine.connect() as conn:
        if person_ids:
            people_names = {r["id"]: r["full_name"] for r in conn.execute(
                select(people.c.id, people.c.full_name).where(people.c.id.in_(tuple(person_ids)))).mappings()}
        if household_ids:
            hh_names = {r["id"]: r["name"] for r in conn.execute(
                select(households.c.id, households.c.name).where(households.c.id.in_(tuple(household_ids)))).mappings()}
    return people_names, hh_names


def _client_link(person_id, household_id):
    if person_id:
        return f"/people/{person_id}"
    if household_id:
        return f"/households/{household_id}"
    return None


def _clients_needing_attention(exceptions):
    """Group open exceptions by client (person, else household). Read-only."""
    by_client = {}
    for exc in exceptions:
        pid, hid = exc.get("person_id"), exc.get("household_id")
        if not pid and not hid:
            continue
        key = ("person", pid) if pid else ("household", hid)
        row = by_client.setdefault(key, {"person_id": pid, "household_id": hid, "open_count": 0, "severities": set()})
        row["open_count"] += 1
        if exc.get("severity"):
            row["severities"].add(exc["severity"])
    people_names, hh_names = _resolve_names(
        {r["person_id"] for r in by_client.values()},
        {r["household_id"] for r in by_client.values()},
    )
    rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    out = []
    for row in by_client.values():
        top = max(row["severities"], key=lambda s: rank.get(s, 0), default=None)
        out.append({
            "person_id": row["person_id"], "household_id": row["household_id"],
            "name": people_names.get(row["person_id"]) or hh_names.get(row["household_id"]) or "Client",
            "open_count": row["open_count"], "top_severity": top,
            "link": _client_link(row["person_id"], row["household_id"]),
        })
    out.sort(key=lambda r: (rank.get(r["top_severity"], -1), r["open_count"]), reverse=True)
    return out


def get_daily_dashboard(principal, *, now=None, limit=20):
    """Compose the read-only advisor daily dashboard from existing services.

    Record-scoped and capability-guarded: a panel whose capability the principal
    lacks is returned empty. No writes; no advisor-intelligence/recommendation
    content (Phase D.1 exclusion).
    """
    now = now or datetime.now(FIRM_TZ)
    today = now.date()
    day_start = datetime.combine(today, time.min, tzinfo=now.tzinfo)
    day_end = day_start + timedelta(days=1)

    with engine.connect() as conn:
        scope = accessible_person_ids(conn, principal)  # None (read_all) | set

    # Tasks + exceptions from the authoritative, already record-scoped work read.
    tasks, exceptions = [], []
    if principal.can("work.read"):
        items = work_items(principal)
        if principal.can("task.read"):
            tasks = [i for i in items if i.get("entity_type") == "task"
                     and str(i.get("status") or "").lower() not in _CLOSED]
        if principal.can("exception.read"):
            exceptions = [i for i in items if i.get("entity_type") == "exception"]

    attention = _clients_needing_attention(exceptions)

    # Meetings today (calendar_event timeline, firm-tz day window), chronological.
    meetings = sorted(
        recent_events(scope, event_types=("calendar_event",), start=day_start, end=day_end, limit=50),
        key=lambda e: e.get("event_time") or day_start,
    )
    for m in meetings:
        m["link"] = _client_link(m.get("person_id"), m.get("household_id"))

    # Recent client activity (scoped timeline, newest-first).
    activity = recent_events(scope, limit=limit)
    for a in activity:
        a["link"] = _client_link(a.get("person_id"), a.get("household_id"))

    # Reviews due (accounts.last_review_date), scoped.
    reviews = accounts_due_for_review(scope, limit=limit, today=today)
    for r in reviews:
        r["link"] = _client_link(r.get("person_id"), r.get("household_id"))

    # Deep links for tasks/exceptions.
    for t in tasks:
        t["link"] = _client_link(t.get("person_id"), t.get("household_id")) or "/tasks"
    for e in exceptions:
        e["link"] = _client_link(e.get("person_id"), e.get("household_id")) or "/exceptions"

    return {
        "today": today,
        "attention": attention,
        "meetings": meetings,
        "reviews": reviews,
        "tasks": tasks,
        "exceptions": exceptions,
        "activity": activity,
        "counts": {
            "attention": len(attention), "meetings": len(meetings), "reviews": len(reviews),
            "tasks": len(tasks), "exceptions": len(exceptions), "activity": len(activity),
        },
    }
