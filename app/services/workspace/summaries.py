"""Advisor Workspace AI-ready summary models (Phase D.38).

Clean, structured, read-only summary dicts a future natural-language assistant can consume WITHOUT
changing the dashboard architecture. Each summary only composes existing record-scoped reads — it
performs no AI, no mutation, and no new business logic, and preserves RBAC / record-scope exactly as
the underlying services do. The five models: Daily Brief, Client Snapshot, Meeting Prep, Opportunity
Summary, Compliance Summary.
"""
from __future__ import annotations

from datetime import datetime

from app.security.authorization import record_in_scope
from app.services.advisor_workspace import get_client_snapshot, get_meeting_brief

from . import digest
from .common import as_json
from .service import _greeting
from .widgets import FIRM_TZ


def daily_brief(principal, *, now=None) -> dict:
    """Firm/book daily brief — greeting, today's counts, priorities, and the top attention items."""
    from app.services.advisor_workspace import get_daily_dashboard
    now = now or datetime.now(FIRM_TZ)
    dashboard = get_daily_dashboard(principal)
    pri = digest.priorities(dashboard)
    return as_json({
        "kind": "daily_brief",
        "generated_at": now.isoformat(),
        "advisor": getattr(principal, "display_name", None),
        "greeting": _greeting(now),
        "date": now.date().isoformat(),
        "today": digest.today_counts(principal, now=now),
        "priorities": {"high": pri["high"], "medium": pri["medium"], "low": pri["low"],
                       "total": pri["total"], "items": pri["entries"]},
        "attention": [{"name": a.get("name"), "open_count": a.get("open_count"),
                       "top_severity": a.get("top_severity")} for a in dashboard.get("attention", [])],
        "meetings_today": dashboard.get("counts", {}).get("meetings", 0),
    })


def client_snapshot(principal, person_id, *, household_id=None) -> dict | None:
    """Per-client snapshot (wealth/insurance/tax/attention side-by-side). Record-scope enforced."""
    if not record_in_scope(principal, "person", person_id):
        return None
    snap = get_client_snapshot(person_id, household_id)
    return as_json({"kind": "client_snapshot", "person_id": person_id, **snap})


def meeting_prep(principal, person_id, *, event_id=None) -> dict | None:
    """Meeting-preparation summary for one client. Record-scope enforced."""
    if not record_in_scope(principal, "person", person_id):
        return None
    brief = get_meeting_brief(person_id, event_id=event_id)
    if brief is None:
        return None
    return as_json({"kind": "meeting_prep", "person_id": person_id, "event_id": event_id, **brief})


def opportunity_summary(principal) -> dict:
    """Pipeline summary — counts, open value, win rate, aging (record-scoped, authoritative)."""
    from app.services.opportunity.reporting import pipeline_report
    report = pipeline_report(principal)
    return as_json({
        "kind": "opportunity_summary",
        "counts": report.get("counts"),
        "open_value": report.get("open_value"),
        "win_rate": report.get("win_rate"),
        "conversion": report.get("conversion"),
        "by_stage": report.get("by_stage"),
        "aging": report.get("aging"),
    })


def compliance_summary(principal) -> dict:
    """Compliance queue summary — open count + the newest open reviews (record-scoped)."""
    from app.services.compliance.reviews import list_reviews
    page = list_reviews(principal, page=1, page_size=10)
    return as_json({
        "kind": "compliance_summary",
        "open_total": page.get("total"),
        "reviews": [{"id": r.get("id"), "status": r.get("status"),
                     "governing_rule": r.get("governing_rule"),
                     "submitted_at": r.get("submitted_at")} for r in page.get("rows", [])],
    })
