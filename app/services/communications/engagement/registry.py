"""Communication interaction registry (Phase D.44) — the single declarative catalog of every interaction
type the unified engagement layer knows about.

Each interaction type declares its authoritative owner, source service, visibility, retention class,
participant type, rendering + search adapter keys, deep-link destination, supported actions, lifecycle, and
compliance owner. This is the ONE place a new communication type is registered. The registry is also how
the timeline classifier maps a raw ``(source, event_type)`` from the authoritative timeline projection onto
a governed interaction type — so a new source is onboarded declaratively here, not by scattering
type-checks across the surfaces. Governance verifies completeness against it.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import BOTH, EXTERNAL, INTERNAL

# Retention classes (governed data-lifecycle buckets — declarative only; enforcement stays with the
# authoritative owner, this records the classification for governance/audit).
RETENTION_STANDARD = "standard"
RETENTION_REGULATORY = "regulatory"      # must be retained per compliance policy (messages, signatures)
RETENTION_TRANSIENT = "transient"        # low-value operational (notifications)

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


@dataclass(frozen=True)
class InteractionType:
    key: str                      # interaction_type
    source_service: str           # the module/service that reads it
    authoritative_owner: str      # the subsystem that OWNS the record (mutations happen only there)
    visibility: str               # internal | external | both
    retention_class: str
    participant_type: str         # advisor_client | staff_client | inbound | system | client_action
    rendering_adapter: str        # adapter key that normalizes a raw event → Interaction
    search_adapter: str           # adapter key that supplies searchable text
    deep_link: str                # deep-link destination template / base
    supported_actions: tuple[str, ...]   # governed actions (each a deep link to the authoritative surface)
    lifecycle: str
    compliance_owner: str
    # Raw authoritative-timeline signals that classify onto this type: (source, event_type) pairs.
    # Empty when the type is not sourced from the timeline projection (e.g. live portal notifications).
    timeline_signals: tuple[tuple[str, str], ...] = ()


def _t(key, source_service, owner, visibility, retention, participant, adapter, deep_link, actions,
       signals=(), lifecycle="active", compliance="Compliance"):
    return InteractionType(key, source_service, owner, visibility, retention, participant, adapter, adapter,
                           deep_link, actions, lifecycle, compliance, signals)


# The catalog. `timeline_signals` list the authoritative timeline (source, event_type) pairs that classify
# onto each type — the timeline projection remains the single deduped store; this only *labels* its rows.
REGISTRY = (
    _t("secure_message", "portal.service", "portal", BOTH, RETENTION_REGULATORY, "advisor_client",
       "message", "/communications", ("reply", "open_thread"),
       signals=(("client_portal", "secure_message"),)),
    _t("communication", "communications.service", "communications", INTERNAL, RETENTION_REGULATORY,
       "staff_client", "conversation", "/communications", ("open_conversation",),
       signals=(("communication", "conversation_opened"), ("communication", "communication_logged"))),
    _t("email", "microsoft.mail_sync", "microsoft365", INTERNAL, RETENTION_STANDARD, "inbound",
       "email", "/integrations/microsoft/inbox", ("review_inbox",),
       signals=(("microsoft", "email_received"),)),
    _t("appointment", "scheduling.service", "scheduling", BOTH, RETENTION_STANDARD, "advisor_client",
       "appointment", "/scheduling", ("open_meeting", "reschedule"),
       signals=(("scheduling", "calendar_event"), ("schedule", "calendar_event"),
                ("meeting_outcome", "calendar_event"))),
    _t("document", "document_platform", "document_platform", INTERNAL, RETENTION_STANDARD, "system",
       "document", "/document-library", ("open_document",),
       signals=(("document", "document_uploaded"), ("document_platform", "document_uploaded"))),
    _t("document_request", "portal.service", "portal", EXTERNAL, RETENTION_STANDARD, "advisor_client",
       "request", "/portal/requests", ("open_request",),
       signals=(("document", "document_requested"), ("client_portal", "document_requested"))),
    _t("signature_request", "portal.signatures", "signature", EXTERNAL, RETENTION_REGULATORY,
       "advisor_client", "signature", "/portal/documents",
       ("open_signature",),
       signals=(("signature_provider", "signature_requested"),
                ("signature_provider", "signature_completed"))),
    _t("client_request", "exception_engine", "exception_engine", EXTERNAL, RETENTION_STANDARD,
       "client_action", "request", "/portal/action-needed", ("open_action",),
       signals=(("exception", "exception_client_action"),)),
    _t("workflow_milestone", "workflow_automation", "workflow", INTERNAL, RETENTION_STANDARD, "system",
       "milestone", "/workflows", ("open_workflow",),
       signals=(("workflow_automation", "workflow_step_completed"),
                ("workflow", "workflow_milestone"))),
    _t("note", "notes", "notes", INTERNAL, RETENTION_STANDARD, "system", "note", "/people",
       ("open_note",),
       signals=(("activity_note", "activity_note_added"), ("advisor", "note_updated"))),
    # Live (non-timeline) sources — the client-facing engagement surface reuses the D.43 portal reads.
    _t("notification", "notifications", "notification_ledger", BOTH, RETENTION_TRANSIENT, "system",
       "notification", "/portal/notifications", ("open_notification",)),
)

_BY_KEY = {t.key: t for t in REGISTRY}
# (source, event_type) → interaction_type, built from the declared timeline signals.
_SIGNAL_INDEX = {}
# event_type → interaction_type. The authoritative composed projection (activity_timeline) preserves the
# original event_type but normalizes the source label, so classification falls back to event_type alone.
_EVENT_INDEX = {}
for _t_def in REGISTRY:
    for _sig in _t_def.timeline_signals:
        _SIGNAL_INDEX[_sig] = _t_def.key
        _EVENT_INDEX.setdefault(_sig[1], _t_def.key)

INTERNAL_STATES = (INTERNAL,)
EXTERNAL_STATES = (EXTERNAL, BOTH)
# Interaction types that must NEVER be surfaced to an external portal principal.
INTERNAL_ONLY_TYPES = tuple(t.key for t in REGISTRY if t.visibility == INTERNAL)


def interaction_type(key) -> InteractionType | None:
    return _BY_KEY.get(key)


def classify(source, event_type) -> str | None:
    """Map an authoritative timeline (source, event_type) onto a registered interaction type, or None if
    the event is not a communication interaction (e.g. portfolio import, assignment change). Prefers the
    exact (source, event_type) signal, then falls back to event_type alone (the composed projection
    normalizes the source label but preserves the original event_type)."""
    return _SIGNAL_INDEX.get((source, event_type)) or _EVENT_INDEX.get(event_type)


def timeline_backed_types() -> list[InteractionType]:
    return [t for t in REGISTRY if t.timeline_signals]


def externally_visible(key) -> bool:
    t = _BY_KEY.get(key)
    return bool(t and t.visibility in EXTERNAL_STATES and t.lifecycle not in ("deprecated", "retired"))


def coverage() -> dict:
    return {
        "total_types": len(REGISTRY),
        "timeline_backed": len(timeline_backed_types()),
        "internal_only": sum(1 for t in REGISTRY if t.visibility == INTERNAL),
        "external": sum(1 for t in REGISTRY if t.visibility in EXTERNAL_STATES),
        "regulatory_retention": sum(1 for t in REGISTRY if t.retention_class == RETENTION_REGULATORY),
        "signal_count": len(_SIGNAL_INDEX),
    }
