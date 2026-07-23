"""Advisor Workspace digest helpers (Phase D.38) — the TODAY summary counts and the deterministic
PRIORITIES bucketing. Shared by the workspace service and the AI-ready summary models.

Both are read-only and capability-aware. The priority bucketing is DETERMINISTIC (severity + overdue),
not AI: it only re-shapes data the record-scoped daily dashboard already computed.
"""
from __future__ import annotations

from app.services.analytics import sources

_HIGH_SEVERITY = {"critical", "high", "urgent"}


def today_counts(principal, *, now=None) -> dict:
    """The TODAY summary row — each figure gated by the principal's capability (0 if not held).
    Count figures use the D.37 projection-backed sources (projection when healthy+fresh, else
    authoritative scoped fallback)."""
    from .widgets import _calendar_today

    def _g(cap, fn):
        return int(fn() or 0) if principal.can(cap) else 0

    return {
        "appointments": (_calendar_today(principal, now=now)["value"]
                         if principal.can("client.read") else 0),
        "compliance": _g("compliance.read", lambda: sources.projection_open_compliance_count(principal)),
        "tax": _g("tax.read", lambda: sources.projection_tax_return_count(principal)),
        "insurance": _g("insurance.read", lambda: sources.projection_insurance_case_count(principal)),
        "benefits": _g("benefits.read", lambda: sources.projection_benefits_enrollment_count(principal)),
        "exceptions": _g("exception.read", lambda: sources.projection_open_exception_count(principal)),
    }


def _bucket_for(severity, overdue=False):
    sev = (severity or "").lower()
    if overdue or sev in _HIGH_SEVERITY:
        return "high"
    if sev == "medium":
        return "medium"
    return "low"


def priorities(dashboard, *, limit=12) -> dict:
    """Deterministically bucket the daily dashboard's attention items into High/Medium/Low.
    Uses exception severity, task overdue-ness, and reviews (low). No AI, no new data."""
    today = dashboard.get("today")
    counts = {"high": 0, "medium": 0, "low": 0}
    items = []
    for e in dashboard.get("exceptions", []) or []:
        b = _bucket_for(e.get("severity"))
        counts[b] += 1
        items.append({"kind": "exception", "title": e.get("title") or e.get("code"),
                      "severity": e.get("severity"), "priority": b, "link": e.get("link")})
    for t in dashboard.get("tasks", []) or []:
        due = t.get("due_date")
        overdue = bool(due and today and due < today)
        b = _bucket_for(t.get("priority"), overdue=overdue)
        counts[b] += 1
        items.append({"kind": "task", "title": t.get("title"), "overdue": overdue,
                      "priority": b, "link": t.get("link")})
    for r in dashboard.get("reviews", []) or []:
        counts["low"] += 1
        items.append({"kind": "review", "title": r.get("account_name") or r.get("account_number"),
                      "priority": "low", "link": r.get("link")})
    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: order[i["priority"]])
    # Key is "entries" (not "items") so Jinja attribute lookup does not collide with dict.items.
    return {**counts, "total": sum(counts.values()), "entries": items[:limit]}
