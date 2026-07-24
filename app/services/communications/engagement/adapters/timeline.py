"""Timeline interaction adapter (Phase D.44) — the advisor/staff engagement spine.

Delegates to the AUTHORITATIVE composed activity timeline (``activity_timeline.client_timeline`` /
``household_timeline``), which is already record-scoped, deduplicated, ordered, redacted, and paginated.
This adapter does NOT re-query domains and does NOT create a second timeline — it CLASSIFIES each row onto
a registered communication interaction type (dropping non-communication activity) and normalizes it to the
unified ``Interaction`` model. Fail-closed: any error yields ``None`` so the surface degrades gracefully.
"""
from __future__ import annotations

from datetime import datetime

from .. import registry, stats
from ..model import INBOUND, INTERNAL_NOTE, OUTBOUND, SYSTEM, Interaction, make_preview

# Direction heuristics by interaction type (used only for display; never a security decision).
_INBOUND_TYPES = {"email", "client_request"}
_SYSTEM_TYPES = {"document", "workflow_milestone", "note", "notification", "signature_request"}
# The authoritative projection caps per-source at 500 and page_size at 100; we pull one bounded window of
# recent activity and classify it. This is a recent-interactions view, not a deep archive read.
_WINDOW = 100


def _direction(interaction_type: str) -> str:
    if interaction_type in _INBOUND_TYPES:
        return INBOUND
    if interaction_type == "note":
        return INTERNAL_NOTE
    if interaction_type in _SYSTEM_TYPES:
        return SYSTEM
    return OUTBOUND


def render_timeline_event(row: dict) -> Interaction | None:
    """Normalize one activity-timeline row (a ``TimelineEvent.to_dict()``) into an ``Interaction``, or
    ``None`` if it is not a registered communication interaction. Pure + independently testable."""
    event_type = row.get("event_type") or ""
    # activity_timeline namespaces some domains (e.g. "advisor_work.completed"); take the leaf too.
    source = row.get("source_domain") or ""
    itype = registry.classify(source, event_type) or registry.classify(source, event_type.split(".")[-1])
    if itype is None:
        return None
    tdef = registry.interaction_type(itype)
    occurred = row.get("occurred_at") or row.get("event_time")
    if isinstance(occurred, str):
        try:
            occurred = datetime.fromisoformat(occurred)
        except ValueError:
            occurred = None
    meta = row.get("metadata") or {}
    subject = row.get("title") or tdef.key.replace("_", " ").title()
    return Interaction(
        interaction_id=str(row.get("event_id") or f"timeline:{itype}:{row.get('source_record_id')}"),
        source_system=tdef.authoritative_owner,
        interaction_type=itype,
        timestamp=occurred,
        subject=subject,
        preview=make_preview(row.get("summary") or subject),
        visibility=tdef.visibility,
        direction=_direction(itype),
        related_person_id=row.get("person_id"),
        related_household_id=row.get("household_id"),
        participants=tuple(p for p in (row.get("actor_display_name"),) if p),
        attachments_available=bool(meta.get("attachments") or meta.get("has_attachments")),
        unread=False,
        action_required=itype in ("document_request", "client_request", "signature_request"),
        deep_link=row.get("source_url") or tdef.deep_link,
        lifecycle=tdef.lifecycle,
        source_freshness=occurred,
        retention_class=tdef.retention_class,
    )


def timeline_interactions(principal, *, person_id=None, household_id=None, event_type=None,
                          date_from=None, date_to=None, search=None):
    """Return the classified communication interactions for a person or household, newest-first. Delegates
    scope + composition to the authoritative activity timeline. Returns a flat list (the service layer
    paginates over the composed, multi-source set); ``None`` when out of scope / unavailable."""
    from app.services.activity_timeline.service import client_timeline, household_timeline
    try:
        if person_id is not None:
            result = client_timeline(principal, person_id, event_type=event_type, date_from=date_from,
                                     date_to=date_to, search=search, page=1, page_size=_WINDOW)
        elif household_id is not None:
            result = household_timeline(principal, household_id, event_type=event_type, date_from=date_from,
                                        date_to=date_to, search=search, page=1, page_size=_WINDOW)
        else:
            return None
    except Exception:
        stats.note("adapter_failures", source="activity_timeline")
        return None
    if result is None:
        return None
    out = []
    for row in result.get("rows", []):
        r = row.to_dict() if hasattr(row, "to_dict") else row
        interaction = render_timeline_event(r)
        if interaction is not None:
            out.append(interaction)
            stats.note("timeline_composed", interaction_type=interaction.interaction_type)
    return out
