"""Read-surface adoption (Phase D.37) — serve read surfaces from projections, with graceful fallback.

D.37 adopts the D.36 read models into user-facing read surfaces WITHOUT changing behavior. An adopted
read consults ``should_use`` and reads its projection ONLY when the projection is healthy + fresh; in
every other case (unbuilt / lagging / stale / an inherently record-scoped read for a non-firm-wide
principal) it FALLS BACK to the authoritative read. Because projections are dark-launched (unbuilt) by
default, adopted reads fall back to the authoritative read by default — so behavior is unchanged until
an operator enables + rebuilds projections.

Hard rules honoured here:
- **Never bypass RBAC / record scope.** A projection carries no record-scope anchor, so a projection is
  only served on the firm-wide (``record.read_all``) path — a record-scoped principal always gets the
  authoritative, scoped read. Firm-level (non-scoped) reads may use the projection for any principal.
- **Never mutate a projection, never reconstruct business logic, never bypass Runtime/Policy.** Adoption
  only READS the read-model table (a count / list); it applies the same status filters the authoritative
  read applies (data filters, not business rules); the caller keeps its capability + runtime + policy
  checks unchanged.
- The authoritative services remain the sole mutation layer; the outbox remains the sole event bus.
"""
from __future__ import annotations

import threading

from sqlalchemy import func, select

from .definitions import PROJECTION_DEFINITIONS

FRESHNESS_LAG_THRESHOLD = 100

_lock = threading.RLock()
_USAGE: dict[str, dict] = {}

# The read surfaces D.37 adopts (projection id → the read function it backs). Governance verifies each
# has an actual adoption site (a static scan of the adoption modules).
ADOPTION_TARGETS = {
    "people.summary": "analytics.client_count",
    "household.summary": "analytics.household_count",
    "opportunity.pipeline": "analytics.projection_open_opportunity_count",
    "operations.tasks": "analytics.open_operational_task_count",
    "operations.projects": "analytics.active_project_count",
    "compliance.queue": "analytics.projection_open_compliance_count",
    "tax.pipeline": "analytics.projection_tax_return_count",
    "insurance.pipeline": "analytics.projection_insurance_case_count",
    "benefits.enrollment": "analytics.projection_benefits_enrollment_count",
    "document.status": "analytics.document_count",
    "exception.dashboard": "analytics.projection_open_exception_count",
    "activity.feed": "activity_timeline.recent_activity_feed",
}

# The modules that host adoption sites (governance scans these for adoption calls).
ADOPTION_MODULES = (
    "app/services/analytics/sources.py",
    "app/services/activity_timeline/service.py",
)

# Adoption inventory — the estimated read-cost of each adopted surface, authoritative vs projection
# (for the before/after / query-reduction report). Counts are the adopted read; join counts are the
# authoritative firm read's joins the projection avoids.
ADOPTION_INVENTORY = {
    "people.summary": {"authoritative": "COUNT(people)", "auth_joins": 0,
                       "projection": "COUNT(rm_people_summary)", "proj_joins": 0, "note": "precomputed count"},
    "household.summary": {"authoritative": "COUNT(households)", "auth_joins": 0,
                          "projection": "COUNT(rm_household_summary)", "proj_joins": 0, "note": "precomputed count"},
    "opportunity.pipeline": {"authoritative": "COUNT(opportunities WHERE status)", "auth_joins": 0,
                             "projection": "COUNT(rm_opportunity_pipeline WHERE status)", "proj_joins": 0,
                             "note": "avoids scanning the full pipeline table"},
    "operations.tasks": {"authoritative": "COUNT(operational_tasks WHERE status)", "auth_joins": 0,
                         "projection": "COUNT(rm_operational_tasks WHERE status)", "proj_joins": 0,
                         "note": "precomputed open-task count"},
    "operations.projects": {"authoritative": "COUNT(projects WHERE status)", "auth_joins": 0,
                            "projection": "COUNT(rm_projects WHERE status)", "proj_joins": 0,
                            "note": "precomputed active-project count"},
    "compliance.queue": {"authoritative": "COUNT(compliance_reviews WHERE open)", "auth_joins": 0,
                         "projection": "COUNT(rm_compliance_queue WHERE decided_at IS NULL)", "proj_joins": 0,
                         "note": "precomputed queue depth"},
    "tax.pipeline": {"authoritative": "COUNT(tax_engagement_returns)", "auth_joins": 4,
                     "projection": "COUNT(rm_tax_pipeline)", "proj_joins": 0,
                     "note": "the authoritative tax dashboard joins 4 tables per read"},
    "insurance.pipeline": {"authoritative": "COUNT(insurance_cases) [+ per-case requirement N+1]",
                           "auth_joins": 1, "projection": "COUNT(rm_insurance_pipeline)", "proj_joins": 0,
                           "note": "the authoritative insurance dashboard has an N+1 over cases"},
    "benefits.enrollment": {"authoritative": "COUNT(benefit_enrollments)", "auth_joins": 1,
                            "projection": "COUNT(rm_benefits_enrollment)", "proj_joins": 0,
                            "note": "avoids the enrollment→employment join"},
    "document.status": {"authoritative": "COUNT(documents WHERE status)", "auth_joins": 0,
                        "projection": "COUNT(rm_document_status WHERE status)", "proj_joins": 0,
                        "note": "precomputed document count"},
    "exception.dashboard": {"authoritative": "COUNT(exceptions WHERE open) [+ Python aging/SLA/trend]",
                            "auth_joins": 0, "projection": "COUNT(rm_exception_dashboard WHERE status)",
                            "proj_joins": 0, "note": "the authoritative dashboard aggregates in Python"},
    "activity.feed": {"authoritative": "3-adapter fan-in + Python merge/sort", "auth_joins": 3,
                      "projection": "SELECT rm_activity_feed ORDER BY id", "proj_joins": 0,
                      "note": "avoids the 3-source activity fan-in"},
}


def adoption_diagnostics() -> dict:
    """Read-surface adoption diagnostics: usage (reads vs fallbacks), per-target freshness (lag), the
    adopted surfaces, and the query-reduction inventory. Read-only."""
    from . import engine
    usage = usage_stats()
    targets = []
    for pid, read_fn in ADOPTION_TARGETS.items():
        st = engine.state(pid)
        inv = ADOPTION_INVENTORY.get(pid, {})
        targets.append({"projection": pid, "read": read_fn, "health": st.get("health"),
                        "lag": engine.lag(pid), "usable": should_use(pid, None, firm_level=True),
                        "reads": usage["by_projection"].get(pid, {}).get("reads", 0),
                        "fallbacks": usage["by_projection"].get(pid, {}).get("fallbacks", 0),
                        "auth_joins_avoided": inv.get("auth_joins", 0), "note": inv.get("note")})
    return {"usage": usage, "adopted_surfaces": len(ADOPTION_TARGETS), "targets": targets,
            "joins_avoided_total": sum(t["auth_joins_avoided"] for t in targets)}


def _note(projection_id: str, kind: str):
    with _lock:
        rec = _USAGE.setdefault(projection_id, {"reads": 0, "fallbacks": 0})
        rec[kind] += 1


def usage_stats() -> dict:
    with _lock:
        by = {k: dict(v) for k, v in _USAGE.items()}
    reads = sum(v["reads"] for v in by.values())
    fallbacks = sum(v["fallbacks"] for v in by.values())
    total = reads + fallbacks
    return {"by_projection": by, "reads": reads, "fallbacks": fallbacks,
            "projection_read_pct": round(reads / total * 100, 1) if total else None}


def reset_usage():
    with _lock:
        _USAGE.clear()


def should_use(projection_id: str, principal=None, *, firm_level: bool = True) -> bool:
    """Whether an adopted read may serve from the projection: it must be healthy + fresh, and — for a
    record-scoped read — the principal must be firm-wide (``record.read_all``). Never raises."""
    try:
        if not firm_level and (principal is None or not principal.can("record.read_all")):
            return False
        from . import engine
        st = engine.state(projection_id)
        if st.get("health") != "healthy":
            return False
        return engine.lag(projection_id) <= FRESHNESS_LAG_THRESHOLD
    except Exception:
        return False


def _read_table(projection_id: str):
    d = PROJECTION_DEFINITIONS.get(projection_id)
    if d is None:
        return None
    from app.db import metadata
    return metadata.tables.get(d.read_table)


def count(projection_id: str, principal=None, *, firm_level: bool = True, status_col: str | None = None,
          status_in=None, status_not_in=None, null_col: str | None = None) -> int | None:
    """Return the projection row count (with optional status filters) when the projection is usable,
    else None (the caller must fall back to the authoritative read). Records read/fallback usage."""
    if not should_use(projection_id, principal, firm_level=firm_level):
        _note(projection_id, "fallbacks")
        return None
    t = _read_table(projection_id)
    if t is None:
        _note(projection_id, "fallbacks")
        return None
    try:
        stmt = select(func.count()).select_from(t)
        if status_in is not None:
            stmt = stmt.where(t.c[status_col].in_(tuple(status_in)))
        if status_not_in is not None:
            stmt = stmt.where(t.c[status_col].notin_(tuple(status_not_in)))
        if null_col is not None:
            stmt = stmt.where(t.c[null_col].is_(None))
        from app.db import engine as db
        with db.connect() as c:
            n = int(c.scalar(stmt) or 0)
        _note(projection_id, "reads")
        return n
    except Exception:
        _note(projection_id, "fallbacks")
        return None


def recent_feed(principal=None, *, limit: int = 50) -> list[dict] | None:
    """The firm activity feed from the ``activity.feed`` projection, when usable; else None (fall back
    to the authoritative timeline). Firm-wide only (references-only rows carry no record-scope anchor)."""
    if not should_use("activity.feed", principal, firm_level=False):
        _note("activity.feed", "fallbacks")
        return None
    t = _read_table("activity.feed")
    if t is None:
        _note("activity.feed", "fallbacks")
        return None
    try:
        from app.db import engine as db
        with db.connect() as c:
            rows = [dict(r) for r in c.execute(select(t).order_by(t.c.outbox_event_id.desc())
                                               .limit(min(500, max(1, limit)))).mappings()]
        _note("activity.feed", "reads")
        return rows
    except Exception:
        _note("activity.feed", "fallbacks")
        return None
