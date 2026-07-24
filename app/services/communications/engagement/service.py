"""Unified engagement composition service (Phase D.44).

Composes the read-only interaction adapters into a single, governed engagement view — WITHOUT a second
messaging/timeline/notification store. The advisor/staff surfaces compose over the authoritative activity
timeline (scoped, deduped, ordered, redacted); the client surface composes over the D.43 portal scoped
reads. Everything here is read-only, references authoritative records, and never copies source content.
"""
from __future__ import annotations

from datetime import datetime

from . import gate, registry, stats
from .adapters import (
    portal_appointment_interactions,
    portal_message_interactions,
    portal_notification_interactions,
    portal_request_interactions,
    timeline_interactions,
)

_MAX_PAGE_SIZE = 100


def _sorted(interactions):
    return sorted(interactions, key=lambda i: (i.timestamp or datetime.min, i.sort_key), reverse=True)


def _apply_filters(interactions, *, interaction_type=None, unread=None, action_required=None,
                   has_attachment=None, visibility=None, direction=None, source=None):
    out = interactions
    if interaction_type:
        out = [i for i in out if i.interaction_type == interaction_type]
    if unread is not None:
        out = [i for i in out if i.unread == unread]
    if action_required is not None:
        out = [i for i in out if i.action_required == action_required]
    if has_attachment is not None:
        out = [i for i in out if i.attachments_available == has_attachment]
    if visibility:
        out = [i for i in out if i.visibility == visibility or i.visibility == "both"]
    if direction:
        out = [i for i in out if i.direction == direction]
    if source:
        out = [i for i in out if i.source_system == source]
    return out


def _paginate(rows, page, page_size):
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    page = max(1, page)
    total = len(rows)
    start = (page - 1) * page_size
    return {"rows": [r.to_dict() for r in rows[start:start + page_size]], "total": total, "page": page,
            "page_size": page_size, "pages": (total + page_size - 1) // page_size}


def _disabled():
    return {"enabled": False, "rows": [], "total": 0, "page": 1, "page_size": 25, "pages": 0}


def engagement_timeline(principal, *, person_id=None, household_id=None, event_type=None,
                        interaction_type=None, date_from=None, date_to=None, search=None,
                        unread=None, action_required=None, has_attachment=None, visibility=None,
                        direction=None, source=None, page=1, page_size=25):
    """The unified advisor/staff engagement timeline for a person or household. Returns None when out of
    scope (mirrors the authoritative timeline's 404 contract). Gate-aware and fail-closed."""
    if not gate.enabled():
        return _disabled()
    surface_gate = "household.timeline.enabled" if household_id is not None else "advisor.timeline.enabled"
    if not gate.gate(surface_gate):
        return _disabled()
    import time
    t0 = time.monotonic()
    interactions = timeline_interactions(principal, person_id=person_id, household_id=household_id,
                                         event_type=event_type, date_from=date_from, date_to=date_to,
                                         search=search)
    if interactions is None:
        return None   # out of scope / unavailable → the route returns 404
    filtered = _apply_filters(interactions, interaction_type=interaction_type, unread=unread,
                              action_required=action_required, has_attachment=has_attachment,
                              visibility=visibility, direction=direction, source=source)
    result = _paginate(_sorted(filtered), page, page_size)
    stats.note("timeline_composed")
    stats.note_ms((time.monotonic() - t0) * 1000)
    result["enabled"] = True
    result["suppressed"] = len(interactions) - len(filtered)
    return result


def search_interactions(principal, *, person_id=None, household_id=None, query=None, **filters):
    """Unified communication search. Delegates the text match + scope to the authoritative timeline (via
    the adapter) and applies interaction-attribute filters. Gate-aware."""
    if not gate.gate("engagement.search.enabled") or not gate.enabled():
        return _disabled()
    stats.note("searches")
    page = filters.pop("page", 1)
    page_size = filters.pop("page_size", 25)
    return engagement_timeline(principal, person_id=person_id, household_id=household_id, search=query,
                               page=page, page_size=page_size, **filters)


def engagement_summary(principal, *, person_id=None, household_id=None):
    """Compact, low-detail engagement summary for the Advisor Workspace / Client 360 / Household 360
    sections and (via those) AI Assist grounding. Counts + a safe last-interaction descriptor only — never
    message bodies. Never raises: returns a disabled/empty summary on gate-off or out-of-scope."""
    if not gate.enabled():
        return {"enabled": False, "total": 0, "unread": 0, "action_required": 0, "by_type": {},
                "last_interaction": None}
    interactions = timeline_interactions(principal, person_id=person_id, household_id=household_id)
    if not interactions:
        return {"enabled": True, "total": 0, "unread": 0, "action_required": 0, "by_type": {},
                "last_interaction": None}
    by_type = {}
    for i in interactions:
        by_type[i.interaction_type] = by_type.get(i.interaction_type, 0) + 1
    latest = _sorted(interactions)[0]
    stats.note("summaries")
    return {
        "enabled": True,
        "total": len(interactions),
        "unread": sum(1 for i in interactions if i.unread),
        "action_required": sum(1 for i in interactions if i.action_required),
        "by_type": by_type,
        "last_interaction": {"interaction_type": latest.interaction_type, "subject": latest.subject,
                             "timestamp": latest.timestamp.isoformat() if latest.timestamp else None,
                             "deep_link": latest.deep_link},
    }


def portal_engagement(principal):
    """The external client engagement surface — recent interactions for a portal account, composed from the
    D.43 portal scoped reads. Gated by ``portal.timeline.enabled`` (opt-in). Only externally-visible
    interaction types are produced (the adapters guarantee this)."""
    if not gate.enabled() or not gate.gate("portal.timeline.enabled"):
        return {"enabled": False, "rows": [], "unread": 0, "action_required": 0}
    from app.portal.service import portal_scope
    try:
        scope = portal_scope(principal.account_id)
    except Exception:
        return {"enabled": True, "rows": [], "unread": 0, "action_required": 0}
    interactions = []
    interactions += portal_message_interactions(principal, scope)
    interactions += portal_notification_interactions(principal)
    interactions += portal_request_interactions(principal, scope)
    # Appointments come from the already-composed dashboard meetings (scheduling-owned calendar events).
    try:
        from app.portal.service import dashboard
        meetings = dashboard(principal).get("meetings", [])
        interactions += portal_appointment_interactions([dict(m) for m in meetings])
    except Exception:
        stats.note("adapter_failures", source="portal_dashboard")
    # Defence in depth: never surface an internal-only interaction type externally.
    interactions = [i for i in interactions if i.interaction_type not in registry.INTERNAL_ONLY_TYPES]
    ordered = _sorted(interactions)
    return {
        "enabled": True,
        "rows": [i.to_dict() for i in ordered[:50]],
        "unread": sum(1 for i in interactions if i.unread),
        "action_required": sum(1 for i in interactions if i.action_required),
    }
