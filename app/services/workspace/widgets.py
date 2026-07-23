"""Advisor Workspace widget compute functions (Phase D.38).

Each function returns a small, read-only data dict for one widget. Count widgets consult the
D.37 projection-backed analytics sources (``analytics.sources``), which serve from a projection
when healthy + fresh on the firm-wide path and fall back to the authoritative, record-scoped read
otherwise — so RBAC / record-scope is never bypassed and behavior is unchanged by default. List
widgets call the existing record-scoped read services directly. No widget mutates anything, and a
widget that raises is isolated (rendered as an error state) so it can never break the home page.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select

from app.services.analytics import sources
from app.services.analytics.sources import book_scope

# Firm timezone for the "today" window (reuse the advisor-workspace anchor).
try:
    from app.services.advisor_workspace import FIRM_TZ
except Exception:   # pragma: no cover - defensive
    FIRM_TZ = UTC


def _day_window(now):
    now = now or datetime.now(FIRM_TZ)
    day_start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    return day_start, day_start + timedelta(days=1)


# --- count widgets (projection-backed via analytics sources) -----------------

def _active_clients(principal, **_):
    return {"value": int(sources.client_count(principal) or 0), "unit": "count"}


def _workflow_exceptions(principal, **_):
    return {"value": int(sources.projection_open_exception_count(principal) or 0), "unit": "count"}


def _operational_tasks(principal, **_):
    return {"value": int(sources.open_operational_task_count(principal) or 0), "unit": "count"}


def _revenue_pipeline(principal, **_):
    return {"value": int(sources.projection_open_opportunity_count(principal) or 0), "unit": "count"}


def _compliance_queue(principal, **_):
    return {"value": int(sources.projection_open_compliance_count(principal) or 0), "unit": "count"}


def _tax_pipeline(principal, **_):
    return {"value": int(sources.projection_tax_return_count(principal) or 0), "unit": "count"}


def _insurance_pipeline(principal, **_):
    return {"value": int(sources.projection_insurance_case_count(principal) or 0), "unit": "count"}


def _benefits_pipeline(principal, **_):
    return {"value": int(sources.projection_benefits_enrollment_count(principal) or 0), "unit": "count"}


def _team_workload(principal, **_):
    from app.services.operations.capacity import over_capacity_count
    return {"value": int(over_capacity_count(principal) or 0), "unit": "count"}


def _document_review(principal, **_):
    # Documents awaiting review, record-scoped (authoritative; no projection for review status).
    from app.db import documents, engine
    ids = book_scope(principal)
    if ids is not None and not ids:
        return {"value": 0, "unit": "count"}
    stmt = (select(func.count()).select_from(documents)
            .where(documents.c.review_status.in_(("pending", "ready_for_review")))
            .where(documents.c.archived_at.is_(None)))
    if ids is not None:
        stmt = stmt.where(documents.c.person_id.in_(ids))
    with engine.connect() as c:
        return {"value": int(c.scalar(stmt) or 0), "unit": "count"}


# --- list widgets (record-scoped read services) ------------------------------

def _calendar_today(principal, *, now=None, **_):
    from app.services.timeline import recent_events
    scope = book_scope(principal)
    day_start, day_end = _day_window(now)
    rows = sorted(
        recent_events(scope, event_types=("calendar_event",), start=day_start, end=day_end, limit=10),
        key=lambda e: e.get("event_time") or day_start)
    for r in rows:
        r["link"] = (f"/workspace/meetings/{r['person_id']}?event={r['id']}"
                     if r.get("person_id") else None)
    return {"rows": rows, "value": len(rows), "unit": "count"}


def _recent_activity(principal, **_):
    from app.services.timeline import recent_events
    scope = book_scope(principal)
    rows = recent_events(scope, limit=8)
    return {"rows": rows, "value": len(rows), "unit": "count"}


# Unified Work Queue widgets (Phase D.39) — all read from the ONE shared queue-summary service so the
# queue query logic is not duplicated in the workspace.
def _work_summary(principal, now=None):
    from app.services.work_queue.summary import widget_counts
    return widget_counts(principal, now=now)


def _work_my(principal, *, now=None, **_):
    return {"value": int(_work_summary(principal, now)["my_open"]), "unit": "count"}


def _work_overdue(principal, *, now=None, **_):
    return {"value": int(_work_summary(principal, now)["my_overdue"]), "unit": "count"}


def _work_due_today(principal, *, now=None, **_):
    return {"value": int(_work_summary(principal, now)["due_today"]), "unit": "count"}


def _work_unassigned(principal, *, now=None, **_):
    return {"value": int(_work_summary(principal, now)["unassigned_team"]), "unit": "count"}


def _work_sla_breaches(principal, *, now=None, **_):
    return {"value": int(_work_summary(principal, now)["sla_breaches"]), "unit": "count"}


COMPUTE = {
    "calendar_today": _calendar_today,
    "active_clients": _active_clients,
    "workflow_exceptions": _workflow_exceptions,
    "operational_tasks": _operational_tasks,
    "recent_activity": _recent_activity,
    "revenue_pipeline": _revenue_pipeline,
    "compliance_queue": _compliance_queue,
    "tax_pipeline": _tax_pipeline,
    "insurance_pipeline": _insurance_pipeline,
    "benefits_pipeline": _benefits_pipeline,
    "document_review": _document_review,
    "team_workload": _team_workload,
    "work_my": _work_my,
    "work_overdue": _work_overdue,
    "work_due_today": _work_due_today,
    "work_unassigned": _work_unassigned,
    "work_sla_breaches": _work_sla_breaches,
}


def compute_widget(key, principal, *, now=None, filters=None):
    """Compute one widget's data. Never raises — a failure yields an error state so the home
    page always renders (per-widget failure isolation)."""
    fn = COMPUTE.get(key)
    if fn is None:
        return {"error": "unknown widget"}
    try:
        return fn(principal, now=now, filters=filters or {})
    except Exception as exc:   # pragma: no cover - defensive isolation
        return {"error": str(exc)}
