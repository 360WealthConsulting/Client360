"""Portal interaction adapters (Phase D.44) — the client-facing engagement spine.

External portal principals are not staff principals and cannot use the advisor activity timeline. These
adapters REUSE the D.43 portal scoped reads (``client_threads`` / ``client_notifications`` /
``client_document_requests`` and the dashboard meetings) — already grant-scoped to the portal account — and
normalize them into the unified ``Interaction`` model. Read-only, fail-closed, never copies message bodies.
Only externally-visible interaction types are ever produced here.
"""
from __future__ import annotations

from .. import registry, stats
from ..model import EXTERNAL, INBOUND, OUTBOUND, SYSTEM, Interaction, make_preview


def _def(key):
    return registry.interaction_type(key)


def portal_message_interactions(principal, scope=None) -> list[Interaction]:
    """Secure message threads visible to the portal account (client channel only — internal notes are
    already filtered by the authoritative portal read)."""
    try:
        from app.portal.service import client_threads
        rows = client_threads(principal, scope)
    except Exception:
        stats.note("adapter_failures", source="portal_messages")
        return []
    tdef = _def("secure_message")
    out = []
    for r in rows:
        out.append(Interaction(
            interaction_id=f"portal:thread:{r['id']}",
            source_system=tdef.authoritative_owner, interaction_type="secure_message",
            timestamp=r.get("updated_at") or r.get("created_at"),
            subject=r.get("subject") or "Secure message", preview=make_preview(r.get("subject")),
            visibility=EXTERNAL, direction=OUTBOUND,
            related_person_id=r.get("person_id"), related_household_id=r.get("household_id"),
            deep_link="/portal/messages", lifecycle=tdef.lifecycle,
            source_freshness=r.get("updated_at"), retention_class=tdef.retention_class))
        stats.note("portal_composed", interaction_type="secure_message")
    return out


def portal_notification_interactions(principal) -> list[Interaction]:
    """In-app notifications for the portal account (unread = not yet read)."""
    try:
        from app.portal.service import client_notifications
        rows = client_notifications(principal)
    except Exception:
        stats.note("adapter_failures", source="portal_notifications")
        return []
    tdef = _def("notification")
    out = []
    for r in rows:
        out.append(Interaction(
            interaction_id=f"portal:notification:{r['id']}",
            source_system=tdef.authoritative_owner, interaction_type="notification",
            timestamp=r.get("created_at"), subject=r.get("title") or "Notification",
            preview=make_preview(r.get("title")), visibility=EXTERNAL, direction=SYSTEM,
            unread=r.get("read_at") is None, deep_link="/portal/notifications",
            lifecycle=tdef.lifecycle, source_freshness=r.get("created_at"),
            retention_class=tdef.retention_class))
        stats.note("portal_composed", interaction_type="notification")
    return out


def portal_request_interactions(principal, scope=None) -> list[Interaction]:
    """Open/uploaded document requests — action required until satisfied."""
    try:
        from app.portal.service import client_document_requests
        rows = client_document_requests(principal, scope)
    except Exception:
        stats.note("adapter_failures", source="portal_requests")
        return []
    tdef = _def("document_request")
    out = []
    for r in rows:
        out.append(Interaction(
            interaction_id=f"portal:request:{r['id']}",
            source_system=tdef.authoritative_owner, interaction_type="document_request",
            timestamp=r.get("created_at") or r.get("due_date"),
            subject=r.get("title") or "Document request", preview=make_preview(r.get("description") or r.get("title")),
            visibility=EXTERNAL, direction=INBOUND,
            related_person_id=r.get("person_id"), related_household_id=r.get("household_id"),
            action_required=r.get("status") == "open", deep_link="/portal/requests",
            lifecycle=tdef.lifecycle, retention_class=tdef.retention_class))
        stats.note("portal_composed", interaction_type="document_request")
    return out


def portal_appointment_interactions(meetings) -> list[Interaction]:
    """Upcoming appointments — from the already-composed portal dashboard ``meetings`` (scheduling-owned
    ``calendar_event`` timeline reads). Takes the pre-scoped rows so it stays a pure normalizer."""
    tdef = _def("appointment")
    out = []
    for m in (meetings or []):
        try:
            mid = m.get("id") or m.get("external_id") or m.get("event_time")
            out.append(Interaction(
                interaction_id=f"portal:appointment:{mid}",
                source_system=tdef.authoritative_owner, interaction_type="appointment",
                timestamp=m.get("event_time"), subject=m.get("title") or "Appointment",
                preview=make_preview(m.get("summary") or m.get("title")),
                visibility=EXTERNAL, direction=OUTBOUND,
                related_person_id=m.get("person_id"), related_household_id=m.get("household_id"),
                deep_link="/portal/appointments", lifecycle=tdef.lifecycle,
                source_freshness=m.get("event_time"), retention_class=tdef.retention_class))
            stats.note("portal_composed", interaction_type="appointment")
        except Exception:
            stats.note("adapter_failures", source="portal_appointments")
    return out
