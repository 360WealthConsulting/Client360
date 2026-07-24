"""Engagement interaction adapters (Phase D.44).

Each adapter is READ-ONLY, scope-aware (it delegates to an authoritative scoped reader), and fail-closed
(any error yields an empty result — a source outage never breaks the composed surface). Adapters normalize
an authoritative record into the unified ``Interaction`` model; they never copy source content and never
mutate. They are independently testable in isolation.
"""
from .portal import (
                     portal_appointment_interactions,
                     portal_message_interactions,
                     portal_notification_interactions,
                     portal_request_interactions,
)
from .timeline import render_timeline_event, timeline_interactions

__all__ = [
    "timeline_interactions", "render_timeline_event",
    "portal_message_interactions", "portal_notification_interactions",
    "portal_request_interactions", "portal_appointment_interactions",
]
