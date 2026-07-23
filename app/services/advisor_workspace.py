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
from app.services.benefits_domain import client_benefits_summary
from app.services.exception_engine import open_count_for_client, open_exceptions_for_client
from app.services.insurance import client_policy_summary
from app.services.notes import add_person_note, list_person_notes
from app.services.portfolio import accounts_due_for_review, get_person_portfolio
from app.services.tasks import create_task, tasks_with_assignee
from app.services.tax_domain import client_engagement_summary
from app.services.timeline import add_timeline_event, get_event, get_person_timeline, recent_events
from app.services.work_management import work_items
from app.services.workflow_automation import launch_workflow

# Review workflow templates an advisor may schedule from a meeting outcome. The authoritative
# decision now lives in the Runtime Policy Engine (workflow.review_routing, Phase D.32); this set is
# retained only as the documented reference of the templates the policy approves.
_REVIEW_TEMPLATES = frozenset({"annual_review", "insurance_review"})

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
    # (D.32) Section inclusion is decided by the centralized Runtime Policy Engine, which consumes the
    # runtime context (the runtime engine remains the sole evaluator). RBAC is preserved and NEVER
    # bypassed (ADR-004): a section is shown only when the principal holds the capability AND the
    # policy decision permits it. Behavior-preserving — the seeded runtime section features are
    # enabled, so the sections are shown exactly as before. The section.tasks/exceptions policies
    # compose section.work, matching the nested gating.
    from app.services.policy import evaluate as policy_evaluate
    from app.services.runtime import consumption
    _rt = consumption.runtime_context()
    tasks, exceptions = [], []
    if principal.can("work.read") and policy_evaluate(
            "advisor_workspace.section.work", context=_rt).decision:
        items = work_items(principal)
        if principal.can("task.read") and policy_evaluate(
                "advisor_workspace.section.tasks", context=_rt).decision:
            tasks = [i for i in items if i.get("entity_type") == "task"
                     and str(i.get("status") or "").lower() not in _CLOSED]
        if principal.can("exception.read") and policy_evaluate(
                "advisor_workspace.section.exceptions", context=_rt).decision:
            exceptions = [i for i in items if i.get("entity_type") == "exception"]

    attention = _clients_needing_attention(exceptions)

    # Meetings today (calendar_event timeline, firm-tz day window), chronological.
    meetings = sorted(
        recent_events(scope, event_types=("calendar_event",), start=day_start, end=day_end, limit=50),
        key=lambda e: e.get("event_time") or day_start,
    )
    for m in meetings:
        # "Meetings today" opens the Meeting Workspace brief for that client,
        # pre-loaded with the synced calendar event (Phase D.3 entry point).
        m["link"] = (f"/workspace/meetings/{m['person_id']}?event={m['id']}"
                     if m.get("person_id") else _client_link(m.get("person_id"), m.get("household_id")))

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


def get_client_snapshot(person_id, household_id=None, *, portfolio=None, open_task_count=0):
    """Read-only per-domain relationship snapshot for the Client 360 summary
    (Phase D.2). Composition-only over existing person-keyed services; every value
    is presented side by side and is NEVER summed into a single composite figure
    (the units are not comparable). No advisor intelligence / recommendations.

    `portfolio` and `open_task_count` are passed in from the already-computed
    person-profile context to avoid recomputing them.
    """
    portfolio = portfolio or {}
    household = portfolio.get("household") or {}
    return {
        "person_id": person_id,
        "household_id": household_id,
        # Wealth (reused from the person portfolio; canonical keys with legacy fallback).
        "aum": portfolio.get("aum", portfolio.get("total_aum")) or 0,
        "household_aum": household.get("aum", household.get("total_aum")) or 0,
        "cash": portfolio.get("cash") or 0,
        "cash_percent": portfolio.get("cash_percent") or 0,
        # Insurance / tax (small authoritative reads).
        "insurance": client_policy_summary(person_id, household_id),
        "tax": client_engagement_summary(person_id, household_id),
        # Attention / agenda.
        "open_exceptions": open_count_for_client(person_id, household_id),
        "open_tasks": open_task_count,
    }


_CLOSED_TASK = {"complete", "completed", "closed", "cancelled"}


def _person_row(person_id):
    with engine.connect() as conn:
        return conn.execute(
            select(people.c.id, people.c.full_name, people.c.primary_email,
                    people.c.primary_phone, people.c.household_id).where(people.c.id == person_id)
        ).mappings().first()


def _household_name(household_id):
    if not household_id:
        return None
    with engine.connect() as conn:
        return conn.scalar(select(households.c.name).where(households.c.id == household_id))


def get_meeting_brief(person_id, *, event_id=None):
    """Read-only meeting-preparation brief for one client (Phase D.3).

    Composition-only over existing authoritative, PERSON-KEYED reads — no writes,
    no meeting/task/follow-up creation, no notifications, no recommendations. All
    lists are keyed to `person_id` (household_id NOT passed to the person-keyed
    reads) so the brief cannot expose other household members' data. The financial
    section is CURRENT values only (no historical change — see Phase D.3 note).

    The route is responsible for record-scope on `person_id`; `event_id` is
    validated here to belong to this person and to be a calendar event, else it is
    ignored (a general brief is rendered).
    """
    person = _person_row(person_id)
    if person is None:
        return None
    household_id = person["household_id"]

    # Event-to-person validation: exists, is a calendar event, belongs to this
    # person. Anything else -> no event context (never surface another client's).
    meeting_event = None
    if event_id is not None:
        ev = get_event(event_id)
        if ev and ev.get("event_type") == "calendar_event" and ev.get("person_id") == person_id:
            meeting_event = ev

    portfolio = get_person_portfolio(person_id)
    open_tasks = [
        t for t in tasks_with_assignee(person_id)
        if str(t.get("status") or "").lower() not in _CLOSED_TASK
    ]
    # Snapshot keyed to the PERSON only (household_id=None) so counts never fold in
    # other household members. get_client_snapshot is the Phase D.2 reuse.
    snapshot = get_client_snapshot(person_id, None, portfolio=portfolio, open_task_count=len(open_tasks))

    return {
        "person": dict(person),
        "household_id": household_id,
        "household_name": _household_name(household_id),
        "meeting_event": meeting_event,
        "snapshot": snapshot,
        "benefits": client_benefits_summary(person_id),
        "open_tasks": open_tasks,
        "open_exceptions": open_exceptions_for_client(person_id),
        "reviews": accounts_due_for_review({person_id}),
        "activity": get_person_timeline(person_id, limit=10),
        "notes": list_person_notes(person_id)[:10],
    }


def get_meeting_outcome_context(person_id, *, event_id=None):
    """Read-only context for the Meeting Outcome page — reuses the Phase D.3
    meeting brief (client identity, household, Client 360 snapshot, agenda, open
    work, exceptions). No new reads."""
    return get_meeting_brief(person_id, event_id=event_id)


def record_meeting_outcome(person_id, *, actor_user_id, completed=False, meeting_notes="",
                           decisions="", comments="", follow_ups=(), next_review_code=None,
                           request_id=None):
    """Record factual meeting outcomes by transitioning agreed work into EXISTING
    authoritative services (Phase D.4). Orchestration only — no new task/note/
    timeline/workflow model, no direct table writes, no notifications, no
    recommendations. Returns a summary of what was recorded.

    - meeting completed  -> Timeline (`add_timeline_event`)
    - notes/decisions/comments -> Notes (`add_person_note`, note_type="meeting")
    - follow-up actions  -> Work Management (`create_task`, idempotent -> no dupes)
    - next review        -> Workflow engine (`launch_workflow`, whitelisted template)

    The caller (route) is responsible for capability + person write-scope.
    """
    person = _person_row(person_id)
    if person is None:
        return None
    household_id = person["household_id"]
    result = {"timeline": False, "notes": 0, "tasks": 0, "workflow": None}

    if completed:
        add_timeline_event(
            source="advisor", event_type="meeting_completed",
            title="Client meeting completed", person_id=person_id, household_id=household_id,
            event_metadata={"recorded_by_user_id": actor_user_id},
        )
        result["timeline"] = True

    for label, text in (("", meeting_notes), ("Decisions made: ", decisions),
                        ("Additional comments: ", comments)):
        text = (text or "").strip()
        if text:
            add_person_note(person_id, f"{label}{text}", author_user_id=actor_user_id, note_type="meeting")
            result["notes"] += 1

    seen = set()
    for title in follow_ups:
        title = (title or "").strip()
        key = title.lower()
        if not title or key in seen:
            continue
        seen.add(key)
        # Stable idempotency key per (person, action) so a double-submit never
        # creates duplicate tasks (create_task also applies its own guard).
        task_id = create_task(
            person_id, title=title, actor_user_id=actor_user_id, source="meeting_outcome",
            idempotency_key=f"mtg-outcome:{person_id}:{key}", request_id=request_id,
        )
        if task_id is not None:
            result["tasks"] += 1

    # (D.33) The review-workflow launch is ORCHESTRATED through the Workflow Orchestration Engine (the
    # workflow.review definition): the routing decision (Runtime Policy Engine workflow.review_routing)
    # and the launch are coordinated by the engine, not embedded here. Behavior-preserving: the workflow
    # launches iff the policy permits exactly the same templates (annual_review / insurance_review), and
    # the launched workflow-instance id is returned (else None).
    from app.services.orchestration import execution as orchestration
    result["workflow"] = orchestration.orchestrate_review(
        next_review_code, actor_user_id=actor_user_id, person_id=person_id, household_id=household_id,
        launcher=lambda: launch_workflow(
            next_review_code, actor_user_id=actor_user_id, person_id=person_id, household_id=household_id,
            idempotency_key=f"mtg-review:{person_id}:{next_review_code}", request_id=request_id))

    return result
