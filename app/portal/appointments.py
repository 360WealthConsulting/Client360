"""Client Portal appointment requests (Phase D.43).

Appointments are OWNED by the authoritative scheduling service; the portal never books a meeting directly
(that would put an external principal in the internal scheduling write path). Instead a client *requests*
an appointment, which is delegated as a governed secure-message thread to the servicing team — the advisor
then books the real meeting in the scheduling domain. Reads (upcoming meetings) already come through the
dashboard's timeline (``calendar_event``); this module only handles the delegated request action.

Gated by ``portal.appointments_enabled`` and scoped by the portal grant. Fails closed.
"""
from __future__ import annotations

from app.portal import stats
from app.portal.gate import gate
from app.portal.service import create_thread, require_scope


def request_appointment(principal, *, person_id, household_id, preferred_window, reason):
    """Record a client's appointment request as a secure-message thread the servicing team actions.
    Requires the appointments gate + person/household scope (via the ``messages`` grant, since the request
    is delivered as a secure message). Returns the created thread id."""
    if not gate("portal.appointments_enabled"):
        raise PermissionError("Appointment requests are not available")
    # Authorization is enforced inside create_thread (messages permission + scope); require_scope here
    # surfaces the denial deterministically for the appointments surface.
    require_scope(principal, person_id=person_id, household_id=household_id, permission="messages")
    window = (preferred_window or "").strip()[:200]
    detail = (reason or "").strip()[:1000]
    body = f"Appointment request.\nPreferred timing: {window or 'no preference given'}\n\n{detail}".strip()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Appointment request", body=body)
    stats.note("appointment_requests")
    return thread_id
