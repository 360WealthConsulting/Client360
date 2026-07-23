"""Unified Work Queue views (Phase D.39) — built-in immutable views + per-user saved views.

Views are PRESENTATION STATE ONLY: a saved filter/sort selection. They never alter a source work
record. Built-in views are immutable Python constants; user views live in ``work_queue_saved_views``
(one row per user+name) and the user's default view / last-used filters live in
``work_queue_preferences`` (one row per user). All reads/writes are scoped to the acting user's own
``user_id``. Mutations are gated by ``work_queue.saved_views`` at the route.
"""
from __future__ import annotations

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import engine, work_queue_preferences, work_queue_saved_views

# Built-in immutable views (key → {label, filters, tab}). ``tab`` marks the ones shown as queue tabs.
BUILTIN_VIEWS = {
    "all":            {"label": "All Work", "filters": {}, "tab": True},
    "my_work":        {"label": "My Work", "filters": {"assignee": "me"}, "tab": True},
    "team_work":      {"label": "Team Work", "filters": {}, "tab": True, "capability": "capacity.read"},
    "unassigned":     {"label": "Unassigned", "filters": {"unassigned": True}, "tab": True,
                       "capability": "capacity.read"},
    "overdue":        {"label": "Overdue", "filters": {"overdue": True}, "tab": True},
    "due_today":      {"label": "Due Today", "filters": {"due": "today"}, "tab": True},
    "due_week":       {"label": "Due This Week", "filters": {"due": "week"}, "tab": True},
    "high_priority":  {"label": "High Priority", "filters": {"priority": ["urgent", "high"]}, "tab": True},
    "sla_breaches":   {"label": "SLA Breaches", "filters": {"sla": "breached"}, "tab": True},
    "workflow_exceptions": {"label": "Workflow Exceptions",
                            "filters": {"domain": ["workflow", "exceptions"]}, "tab": True,
                            "capability": "exception.read"},
    "compliance_queue": {"label": "Compliance Queue", "filters": {"domain": "compliance"}, "tab": True,
                         "capability": "compliance.review.read"},
    "tax_season":     {"label": "Tax Season", "filters": {"domain": "tax"}, "tab": True,
                       "capability": "tax.read"},
    "insurance":      {"label": "Insurance", "filters": {"domain": "insurance"}, "tab": True,
                       "capability": "insurance.read"},
    "document_review": {"label": "Document Review", "filters": {"domain": "documents"}, "tab": True,
                        "capability": "documents.view"},
    "opportunities":  {"label": "Opportunities", "filters": {"domain": "opportunities"}, "tab": True,
                       "capability": "opportunity.read"},
    "meetings":       {"label": "Meetings", "filters": {"domain": "meetings"}, "tab": True,
                       "capability": "scheduling.view"},
}

DEFAULT_VIEW = "my_work"
# Filter keys the queue understands — used by governance to detect unknown saved-view filter keys.
KNOWN_FILTER_KEYS = frozenset({
    "domain", "status", "priority", "sla", "overdue", "unassigned", "assignee", "team",
    "person_id", "household_id", "search", "due", "due_from", "due_to",
})


def visible_tabs(principal) -> list[dict]:
    """Built-in tab views the principal has capability to open (never shown-then-403)."""
    out = []
    for key, spec in BUILTIN_VIEWS.items():
        if not spec.get("tab"):
            continue
        cap = spec.get("capability")
        if cap and not principal.can(cap):
            continue
        out.append({"key": key, "label": spec["label"], "filters": spec["filters"]})
    return out


def resolve_view(key, principal) -> dict | None:
    """Resolve a view key to its filter dict — a built-in key or ``user:{id}``. Returns None if unknown
    or not permitted."""
    if key in BUILTIN_VIEWS:
        spec = BUILTIN_VIEWS[key]
        cap = spec.get("capability")
        if cap and not principal.can(cap):
            return None
        return dict(spec["filters"])
    if isinstance(key, str) and key.startswith("user:"):
        try:
            vid = int(key.split(":", 1)[1])
        except ValueError:
            return None
        row = _get_user_view(principal.user_id, vid)
        return dict(row["filters"] or {}) if row else None
    return None


# --- per-user saved views ----------------------------------------------------

def list_views(user_id) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(work_queue_saved_views)
                         .where(work_queue_saved_views.c.user_id == user_id)
                         .order_by(work_queue_saved_views.c.name.asc())).mappings().all()
    return [dict(r) for r in rows]


def _get_user_view(user_id, view_id):
    with engine.connect() as c:
        row = c.execute(select(work_queue_saved_views).where(
            work_queue_saved_views.c.id == view_id,
            work_queue_saved_views.c.user_id == user_id)).mappings().first()
    return dict(row) if row else None


def save_view(user_id, name, filters, *, sort=None):
    """Create/update a named saved view (upsert on user+name). Presentation state only."""
    name = (name or "").strip()
    if not name:
        return None
    clean = {k: v for k, v in (filters or {}).items() if k in KNOWN_FILTER_KEYS}
    stmt = pg_insert(work_queue_saved_views).values(
        user_id=user_id, name=name, filters=clean, sort=sort)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_work_queue_view_user_name",
        set_={"filters": clean, "sort": sort, "updated_at": func.now()})
    with engine.begin() as c:
        c.execute(stmt)


def rename_view(user_id, view_id, new_name):
    new_name = (new_name or "").strip()
    if not new_name:
        return
    with engine.begin() as c:
        c.execute(update(work_queue_saved_views)
                  .where(work_queue_saved_views.c.id == view_id,
                         work_queue_saved_views.c.user_id == user_id)
                  .values(name=new_name, updated_at=func.now()))


def delete_view(user_id, view_id):
    with engine.begin() as c:
        c.execute(delete(work_queue_saved_views)
                  .where(work_queue_saved_views.c.id == view_id,
                         work_queue_saved_views.c.user_id == user_id))
    # if this was the default, clear the default back to the system default.
    prefs = get_preferences(user_id)
    if prefs.get("default_view") == f"user:{view_id}":
        set_default(user_id, DEFAULT_VIEW)


# --- per-user preferences (default view + remembered filters) ----------------

def get_preferences(user_id) -> dict:
    with engine.connect() as c:
        row = c.execute(select(work_queue_preferences)
                        .where(work_queue_preferences.c.user_id == user_id)).mappings().first()
    if row is None:
        return {"default_view": DEFAULT_VIEW, "last_filters": {}}
    return {"default_view": row["default_view"] or DEFAULT_VIEW,
            "last_filters": dict(row["last_filters"] or {})}


def _write_prefs(user_id, values):
    values = {**values, "updated_at": func.now()}
    with engine.begin() as c:
        existing = c.execute(select(work_queue_preferences.c.id)
                             .where(work_queue_preferences.c.user_id == user_id)).scalar()
        if existing is None:
            c.execute(insert(work_queue_preferences).values(user_id=user_id, **values))
        else:
            c.execute(update(work_queue_preferences)
                      .where(work_queue_preferences.c.user_id == user_id).values(**values))


def set_default(user_id, view_key):
    _write_prefs(user_id, {"default_view": view_key})


def reset_default(user_id):
    _write_prefs(user_id, {"default_view": DEFAULT_VIEW})


def remember_filters(user_id, filters):
    clean = {k: v for k, v in (filters or {}).items() if k in KNOWN_FILTER_KEYS}
    _write_prefs(user_id, {"last_filters": clean})
