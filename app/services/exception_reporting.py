"""Exception reporting & dashboard metrics (Release 0.9.10 / Sprint 5.5, Phase 8).

Aggregations for staff dashboards over the platform Exception Engine. Every report
is built from ``exception_engine.list_exceptions(principal)`` — so **record-scope
authorization is applied before any aggregation** (a principal only ever aggregates
exceptions they may see; ``record.read_all`` sees firm-wide). Tax domain only.

Only metrics the system actually stores are computed — opened/acknowledged/resolved
timestamps, escalation level, SLA due, ownership, category/severity, and the
append-only event ledger. Nothing about revenue, productivity, or synthetic history
is fabricated; trends are derived from real ``opened_at`` / ``resolved_at`` values.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import engine, exception_events
from app.services import exception_engine as ee

# Which metric panels each staff audience sees (role-appropriate presentation).
# The report dict always carries the full computed metrics; the audience only
# selects what a given dashboard renders. Aggregation is identical and always
# scope-filtered — audiences never widen what data is visible.
AUDIENCE_PANELS = {
    "advisor":    ("summary", "by_client_return", "aging", "sla"),
    "operations": ("summary", "by_owner_team", "unassigned", "aging", "escalation", "throughput", "trend"),
    "tax":        ("summary", "by_category", "by_client_return", "aging", "sla", "trend"),
    "compliance": ("summary", "compliance", "sla", "aging", "reopen"),
    "management": ("summary", "by_category", "by_owner_team", "throughput", "sla", "reopen", "escalation", "trend"),
}
AUDIENCES = frozenset(AUDIENCE_PANELS)


def default_audience(principal):
    """Pick the most role-appropriate audience from the principal's capabilities."""
    if principal.can("identity.manage") or principal.can("record.write_all"):
        return "management"
    if principal.can("exception.compliance"):
        return "compliance"
    if principal.can("capacity.read"):
        return "operations"
    if principal.can("tax.read"):
        return "tax"
    return "advisor"


def dashboard_summary(principal, *, audience=None):
    """Compact, scope-filtered exception summary for embedding a tile strip in an
    existing staff dashboard. Returns ``None`` when the viewer lacks
    ``exception.read`` (so the host page simply omits the section)."""
    if principal is None or not principal.can("exception.read"):
        return None
    try:
        report = exception_report(principal, audience=audience)
    except ee.ExceptionEngineError:
        return None
    return {"audience": report["audience"], "summary": report["summary"]}


def _now():
    return datetime.now(timezone.utc)


def _mean_seconds(deltas):
    return round(sum(deltas) / len(deltas), 1) if deltas else None


def _aging_bucket(age_seconds):
    days = age_seconds / 86400
    if days < 1:
        return "lt_1d"
    if days < 3:
        return "1_3d"
    if days < 7:
        return "3_7d"
    return "gt_7d"


def _reopen_counts(exception_ids):
    """Real reopen signal from the immutable event ledger, scoped to the given ids."""
    if not exception_ids:
        return 0
    with engine.connect() as c:
        reopened = c.execute(
            select(exception_events.c.exception_id)
            .where(exception_events.c.exception_id.in_(tuple(exception_ids)),
                   exception_events.c.event_type == "reopened")
            .distinct()
        ).all()
    return len(reopened)


def exception_report(principal, *, audience=None, trend_days=30, now=None, domain="tax"):
    """Comprehensive, authorization-filtered exception metrics for a staff principal.

    ``audience`` (advisor/operations/tax/compliance/management) selects the panel
    list only; the returned metrics are complete and identical regardless. ``domain``
    (``tax`` or ``benefits``) selects which exception domain to report on — the same
    reporting engine serves both. Raises the engine's ``UnsupportedDomainError`` for an
    unimplemented domain and ``ExceptionAuthorizationError`` when the principal lacks
    ``exception.read``.
    """
    audience = audience if audience in AUDIENCES else default_audience(principal)
    now = now or _now()
    rows = ee.list_exceptions(principal, domain=domain)  # <-- record-scope enforced here
    open_rows = [r for r in rows if r["status"] not in ee.CLOSED_STATUSES]

    ack_deltas = [(r["acknowledged_at"] - r["opened_at"]).total_seconds()
                  for r in rows if r["acknowledged_at"] and r["opened_at"]]
    res_deltas = [(r["resolved_at"] - r["opened_at"]).total_seconds()
                  for r in rows if r["resolved_at"] and r["opened_at"]]
    resolved_with_sla = [r for r in rows if r["resolved_at"] and r["sla_due_at"]]
    met_sla = sum(1 for r in resolved_with_sla if r["resolved_at"] <= r["sla_due_at"])

    aging = Counter(_aging_bucket((now - r["opened_at"]).total_seconds()) for r in open_rows)
    escalation = Counter(r["escalation_level"] for r in open_rows)

    # Real opened/resolved-per-day trend over the window (no synthetic points).
    window_start = (now - timedelta(days=trend_days)).date()
    opened_by_day, resolved_by_day = Counter(), Counter()
    for r in rows:
        if r["opened_at"] and r["opened_at"].date() >= window_start:
            opened_by_day[r["opened_at"].date().isoformat()] += 1
        if r["resolved_at"] and r["resolved_at"].date() >= window_start:
            resolved_by_day[r["resolved_at"].date().isoformat()] += 1
    trend = [{"date": (window_start + timedelta(days=i)).isoformat(),
              "opened": opened_by_day.get((window_start + timedelta(days=i)).isoformat(), 0),
              "resolved": resolved_by_day.get((window_start + timedelta(days=i)).isoformat(), 0)}
             for i in range(trend_days + 1)]

    reopened_count = _reopen_counts([r["id"] for r in rows])

    def _owner_label(r):
        if r["owner_user_id"]:
            return f"user:{r['owner_user_id']}"
        if r["owner_team_id"]:
            return f"team:{r['owner_team_id']}"
        return "unassigned"

    return {
        "audience": audience,
        "panels": AUDIENCE_PANELS[audience],
        "generated_at": now.isoformat(),
        "summary": {
            "total": len(rows),
            "open": len(open_rows),
            "blocker": sum(1 for r in open_rows if r["severity"] == "blocker"),
            "high": sum(1 for r in open_rows if r["severity"] == "high"),
            "at_risk": sum(1 for r in open_rows if r.get("sla_state") == "at_risk"),
            "breached": sum(1 for r in open_rows if r.get("sla_state") == "breached"),
            "unassigned": sum(1 for r in open_rows if not r["owner_user_id"] and not r["owner_team_id"]),
            "compliance": sum(1 for r in open_rows if r["category"] == "compliance"),
            "escalated": sum(1 for r in open_rows if r["status"] == "escalated"),
        },
        "by_category": dict(Counter(r["category"] for r in open_rows)),
        "by_severity": dict(Counter(r["severity"] for r in open_rows)),
        "by_status": dict(Counter(r["status"] for r in rows)),
        "by_owner_team": dict(Counter(_owner_label(r) for r in open_rows)),
        "by_client": dict(Counter(f"person:{r['person_id']}" for r in open_rows if r["person_id"])),
        "by_return": dict(Counter(f"return:{r['tax_engagement_return_id']}"
                                  for r in open_rows if r["tax_engagement_return_id"])),
        "aging": {b: aging.get(b, 0) for b in ("lt_1d", "1_3d", "3_7d", "gt_7d")},
        "escalation_distribution": {str(k): escalation[k] for k in sorted(escalation)},
        "unassigned": sum(1 for r in open_rows if not r["owner_user_id"] and not r["owner_team_id"]),
        "compliance_open": sum(1 for r in open_rows if r["category"] == "compliance"),
        "throughput": {
            "mean_time_to_acknowledge_seconds": _mean_seconds(ack_deltas),
            "mean_time_to_resolve_seconds": _mean_seconds(res_deltas),
            "acknowledged_count": len(ack_deltas),
            "resolved_count": len(res_deltas),
        },
        "reopen": {
            "reopened_exceptions": reopened_count,
            "total_exceptions": len(rows),
            "reopen_rate": round(reopened_count / len(rows), 4) if rows else 0.0,
        },
        "sla": {
            "resolved_with_sla": len(resolved_with_sla),
            "met_sla": met_sla,
            "sla_compliance_rate": round(met_sla / len(resolved_with_sla), 4) if resolved_with_sla else None,
        },
        "trend": trend,
        "trend_days": trend_days,
    }
