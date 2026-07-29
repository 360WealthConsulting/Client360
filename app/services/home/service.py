"""Staff Home dashboard composer — capability-driven, record-scoped, single-pass.

Everything here REUSES existing scope-safe reads; it introduces no new store and no schema:

  * work counts + priority + waiting: ONE ``work_queue.service.collect`` pass, capability-suppressed
    (``_suppress``) and filtered per view with the existing ``_match`` (no N+1, no duplicated queue
    logic); names resolved once via ``_resolve_names``.
  * reviews the user may decide: ``compliance.reviews.list_reviews`` + pending ``work_approvals``.
  * portal activity: ``portal.staff_activity.portal_activity_for_staff`` (record-scoped).
  * recent client activity: ``timeline.recent_events`` over the principal's book scope.
  * persona/audience + quick actions: derived from ``Principal.can`` only (never employee names).

Unauthorized principals (e.g. external IT lacking client/work capabilities) get empty panels — the
data is never fetched-then-hidden; each read self-suppresses on capability/scope.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.services.analytics.sources import book_scope
from app.services.exception_reporting import default_audience
from app.services.work_queue import views as qv
from app.services.work_queue.service import _match, _resolve_names, _suppress, collect

# Priority ordering for the Priority Work panel: critical → overdue → due-today → high → rest.
_PRIORITY_RANK = {"urgent": 0, "critical": 0, "high": 1, "normal": 2, "low": 3}

# The summary cards, in display order: (key, label, /work view, required capabilities). A card is
# shown only if the principal holds one of its capabilities AND the view resolves (so external IT,
# lacking work.read, sees no work cards at all — never fetched-then-hidden).
_CARDS = [
    ("my_work", "My active work", "my_work", ("work.read",)),
    ("overdue", "Overdue", "overdue", ("work.read",)),
    ("due_today", "Due today", "due_today", ("work.read",)),
    ("waiting", "Waiting", "waiting", ("work.read",)),
    ("reviews", "Reviews", "reviews", ("compliance.review.read", "work.approve", "documents.view")),
    ("unassigned", "Unassigned", "unassigned", ("capacity.read", "record.read_all")),
]


def _is_critical(it):
    return it.priority in ("urgent", "critical")


def _priority_sort_key(it, today):
    due_today = bool(it.due_at and it.due_at.date() == today)
    return (0 if _is_critical(it) else 1, 0 if it.overdue else 1, 0 if due_today else 1,
            _PRIORITY_RANK.get(it.priority, 2), it.due_at.isoformat() if it.due_at else "~")


def home_summary(principal, *, now=None, priority_limit=10, panel_limit=8):
    now = now or datetime.now(UTC)
    today = now.date()

    # --- ONE work-queue pass (capability-suppressed; scope enforced inside the adapters) ----------
    items, _ = collect(principal, now=now)
    items, _ = _suppress(items, principal)

    def view_count(view_key):
        f = qv.resolve_view(view_key, principal)
        if f is None:                       # principal cannot open this view
            return None
        return sum(1 for it in items if _match(it, f, principal, now))

    cards = []
    for key, label, view, caps in _CARDS:
        if not any(principal.can(cap) for cap in caps):     # capability-gate the card itself
            continue
        count = view_count(view)
        if count is None:                                   # view not resolvable for this principal
            continue
        cards.append({"key": key, "label": label, "count": count, "href": f"/work?view={view}"})

    # Priority Work panel (critical → overdue → due-today → high), names resolved once.
    priority_items = sorted(items, key=lambda it: _priority_sort_key(it, today))[:priority_limit]
    _resolve_names(priority_items)
    priority_work = [it.to_dict() for it in priority_items]

    # Waiting & Blocked panel (status_group == waiting), names resolved once.
    waiting_filter = qv.resolve_view("waiting", principal) or {"status": "waiting"}
    waiting_items = [it for it in items if _match(it, waiting_filter, principal, now)][:panel_limit]
    _resolve_names(waiting_items)
    waiting = [it.to_dict() for it in waiting_items]

    # --- Reviews the principal may decide (compliance reviews + my pending work approvals) --------
    reviews = _reviews(principal, panel_limit)

    # --- Portal + Vault activity (record-scoped) --------------------------------------------------
    from app.portal.staff_activity import portal_activity_for_staff
    portal_activity = portal_activity_for_staff(principal, limit=panel_limit)

    # --- Recent client activity (book-scoped timeline) --------------------------------------------
    recent_activity = _recent_activity(principal, panel_limit)

    audience = default_audience(principal)
    return {
        "greeting": principal.display_name or "there",
        "today": today.isoformat(),
        "audience": audience,
        "counts": {c["key"]: c["count"] for c in cards},
        "cards": cards,
        "priority_work": priority_work,
        "waiting": waiting,
        "reviews": reviews,
        "portal_activity": portal_activity,
        "recent_activity": recent_activity,
        "quick_actions": _quick_actions(principal),
        "can": {
            "work_read": principal.can("work.read"),
            "work_write": principal.can("work.write"),
            "client_read": principal.can("client.read"),
            "unassigned": principal.can("capacity.read") or principal.can("record.read_all"),
            "reviews": principal.can("compliance.review.read") or principal.can("work.approve"),
            "portal": principal.can("client.read"),
        },
    }


def _reviews(principal, limit):
    out = []
    if principal.can("compliance.review.read"):
        try:
            from app.services.compliance.reviews import list_reviews
            result = list_reviews(principal, status="pending_review", page_size=limit)
            for r in result.get("rows", []):
                out.append({"kind": "compliance_review", "id": r.get("id"),
                            "title": r.get("governing_rule") or "Compliance review",
                            "person_id": r.get("person_id"), "submitted_at": _iso(r.get("submitted_at")),
                            "submitted_by": r.get("submitted_by"),
                            "review_type": r.get("recommendation_type"),
                            "href": f"/compliance/reviews/{r.get('id')}"})
        except Exception:
            pass
    if principal.can("work.approve"):
        try:
            from sqlalchemy import desc, select

            from app.db import engine, work_approvals
            with engine.connect() as c:
                rows = c.execute(
                    select(work_approvals).where(
                        work_approvals.c.status == "pending",
                        work_approvals.c.approver_user_id == principal.user_id)
                    .order_by(desc(work_approvals.c.created_at)).limit(limit)).mappings().all()
            for r in rows:
                out.append({"kind": "work_approval", "id": r["id"],
                            "title": r["approval_type"], "person_id": None,
                            "submitted_at": _iso(r["created_at"]),
                            "submitted_by": r["requested_by_user_id"], "review_type": r["approval_type"],
                            "href": f"/work/{r['entity_type']}/{r['entity_id']}"})
        except Exception:
            pass
    return out[:limit]


def _recent_activity(principal, limit):
    try:
        from app.services.timeline import recent_events
        scope = book_scope(principal)
        if scope is not None and not scope:
            return []
        events = recent_events(scope, limit=limit)
        return [{"title": e.get("title"), "event_type": e.get("event_type"),
                 "event_time": str(e.get("event_time")) if e.get("event_time") else None,
                 "person_id": e.get("person_id"), "household_id": e.get("household_id")}
                for e in events][:limit]
    except Exception:
        return []


def _quick_actions(principal):
    """Only the actions the principal is authorized to perform (capability-gated)."""
    candidates = [
        ("create_work", "Create work item", "/work?view=my_work", "work.write"),
        ("my_work", "Open My Work", "/work?view=my_work", "work.read"),
        ("find_client", "Find client", "/people", "client.read"),
        ("upload_document", "Upload document", "/work", "vault.upload"),
        ("invite_client", "Invite client to portal", "/admin/client-portal", "client.write"),
        ("review_unassigned", "Review unassigned work", "/work?view=unassigned", "capacity.read"),
        ("compliance_reviews", "Open compliance reviews", "/compliance/reviews", "compliance.review.read"),
    ]
    return [{"key": k, "label": lbl, "href": href}
            for k, lbl, href, cap in candidates if principal.can(cap)]


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value
