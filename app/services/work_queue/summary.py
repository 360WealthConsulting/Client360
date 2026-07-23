"""Unified Work Queue summary (Phase D.39) — one shared, read-only summary service.

Both the AI-ready ``/work/summary`` model and the D.38 workspace widgets consume THIS module, so the
queue query logic is not duplicated. It generates NO recommendations and never mutates anything — a
future AI feature can read it, not act through it.
"""
from __future__ import annotations

from datetime import UTC, datetime

from .service import _sort_key, _suppress, collect


def _visible(principal, now):
    items, _ = collect(principal, now=now)
    items, _ = _suppress(items, principal)
    return items


def work_queue_summary(principal, *, now=None, top=8) -> dict:
    """AI-ready work-queue summary — counts + top-urgent references + deep links. Read-only."""
    now = now or datetime.now(UTC)
    items = _visible(principal, now)
    mine = [i for i in items if i.assignee_user_id == principal.user_id]
    by_domain = {}
    for i in items:
        by_domain[i.source_domain] = by_domain.get(i.source_domain, 0) + 1
    urgent = sorted(items, key=_sort_key)[:top]
    return {
        "kind": "work_queue_summary",
        "generated_at": now.isoformat(),
        "my_overdue": sum(1 for i in mine if i.overdue),
        "my_open": len(mine),
        "due_today": sum(1 for i in items if i.due_at and i.due_at.date() == now.date()),
        "high_priority": sum(1 for i in items if i.priority in ("urgent", "high")),
        "sla_breaches": sum(1 for i in items if i.sla_state == "breached"),
        "unassigned_team": sum(1 for i in items if i.assignee_user_id is None),
        "total_visible": len(items),
        "by_domain": by_domain,
        "top_urgent": [{"work_item_key": i.work_item_key, "title": i.title,
                        "source_domain": i.source_domain, "priority": i.priority,
                        "sla_state": i.sla_state, "overdue": i.overdue,
                        "due_at": i.due_at.isoformat() if i.due_at else None,
                        "person_id": i.person_id, "workflow_instance_id": i.workflow_instance_id,
                        "exception_id": i.exception_id, "deep_link": i.deep_link}
                       for i in urgent],
    }


def widget_counts(principal, *, now=None) -> dict:
    """Compact counts for the D.38 workspace widgets (My Work, Overdue, Due Today, Unassigned Team,
    SLA Breaches), each deep-linking into a filtered ``/work`` view. Reuses the shared summary."""
    s = work_queue_summary(principal, now=now, top=0)
    return {"my_open": s["my_open"], "my_overdue": s["my_overdue"], "due_today": s["due_today"],
            "unassigned_team": s["unassigned_team"], "sla_breaches": s["sla_breaches"],
            "high_priority": s["high_priority"]}
