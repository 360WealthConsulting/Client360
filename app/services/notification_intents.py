"""Event-driven notification intent creation (F5.4 / Epic 5, ADR-017).

The canonical **event-to-notification-intent** layer. It consumes *approved* business
or workflow events (delivered post-commit via the F1.3 transactional outbox) and
deterministically records **notification intents** in the F5.1 ledger. It **stops at
intent creation** — it never dispatches, invokes a channel provider, schedules retries,
records delivery attempts, emits notification audit/evidence, exposes routes, or adds
capabilities.

Core rule (ADR-017): business/workflow events remain authoritative for *what happened*;
a notification intent only records that a *communication may need to occur*. Creating or
suppressing an intent never completes a task, satisfies an obligation, or changes
workflow/domain/evidence state or the originating event. This is a **derived
communication-intent layer only**.

Design:
- **Explicit, reviewable mappings.** ``NotificationMapping`` entries (a canonical
  registry, no notification content) define exactly which event types produce intents,
  the notification purpose, channel, recipient resolver, and consent requirement. Unknown
  or unmapped events produce an explicit ``not_applicable`` no-op — never fuzzy matching.
- **Deterministic recipients.** A mapping's resolver derives the recipient **reference**
  from the event itself. If the event lacks enough information to derive a recipient
  safely, **no intent is created** (``not_applicable``).
- **F5.3 governs eligibility.** Every intent runs through the F5.3 decision layer; F5.4
  never bypasses it and never mutates preference/consent records.
- **Idempotent & durable.** The intent's ``dedupe_key`` is derived deterministically from
  the mapping + source event id + recipient + channel + purpose; the F5.1 unique
  ``dedupe_key`` constraint is the durable backstop, and the outbox's
  ``outbox_processed_events`` gives a second consumer-level layer — a re-processed event
  never creates a duplicate intent, across restarts and repeated invocation.
- **Post-commit only.** Consumers run in the outbox dispatcher after the authoritative
  transaction commits (an event exists iff its transition committed), so an intent is
  never created for a rolled-back event and nothing dispatches inside the originating
  transaction.
- **Content-minimal.** The created row carries references only — a template *reference*
  (not a rendered body), the source/correlation/causation references, and reference ids.
  No domain payloads, contact details, or rendered content are copied into the ledger.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.platform.workflow_events import approval_event_type
from app.services import notifications as ledger
from app.services.notification_preferences import (
    ALLOWED,
    evaluate_delivery,
)
from app.services.notification_preferences import (
    DISABLED as DECISION_DISABLED,
)
from app.services.notification_preferences import (
    NOT_APPLICABLE as DECISION_NOT_APPLICABLE,
)
from app.services.notification_preferences import (
    SUPPRESSED as DECISION_SUPPRESSED,
)

# --- intent-creation outcomes ------------------------------------------------

CREATED = "created"
ALREADY_EXISTS = "already_exists"
SUPPRESSED = "suppressed"
DISABLED = "disabled"
NOT_APPLICABLE = "not_applicable"
FAILED = "failed"
OUTCOMES: frozenset[str] = frozenset(
    {CREATED, ALREADY_EXISTS, SUPPRESSED, DISABLED, NOT_APPLICABLE, FAILED}
)

#: Deterministic dedupe-key namespace so F5.4 intents never collide with other producers.
DEDUPE_NAMESPACE = "f5.4"


# --- structured, content-free result -----------------------------------------

@dataclass(frozen=True)
class IntentResult:
    """Structured, deterministic, **content-free** result of processing one event.

    Carries references and a machine-readable outcome/reason only — never a notification
    ``title`` or ``body``.
    """

    outcome: str
    source_event_id: str | None
    source_event_type: str | None
    mapping_id: str | None = None
    channel: str | None = None
    recipient_ref: str | None = None
    notification_uid: str | None = None
    decision_reason_code: str | None = None
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome, "source_event_id": self.source_event_id,
            "source_event_type": self.source_event_type, "mapping_id": self.mapping_id,
            "channel": self.channel, "recipient_ref": self.recipient_ref,
            "notification_uid": self.notification_uid,
            "decision_reason_code": self.decision_reason_code, "description": self.description,
        }


# --- event access (Envelope or bare {"event_id","name","payload"}) -----------

def _ev(event, envelope_attr, view_key, default=None):
    val = getattr(event, envelope_attr, None)
    if val is not None:
        return val
    if isinstance(event, dict):
        return event.get(view_key, default)
    return default


def _event_type(event) -> str | None:
    return _ev(event, "event_type", "name")


def _event_id(event) -> str | None:
    return _ev(event, "event_id", "event_id")


def _payload(event) -> dict:
    return _ev(event, "payload", "payload", {}) or {}


def _subject_ref(event) -> str | None:
    return _ev(event, "subject_ref", "subject_ref")


def _correlation_id(event) -> str | None:
    return _ev(event, "correlation_id", "correlation_id")


def _causation_id(event) -> str | None:
    return _ev(event, "causation_id", "causation_id")


# --- recipient resolvers (deterministic; derive a *reference* only) ----------

def _user_ref(user_id) -> str | None:
    """A recipient reference for a user id, or ``None`` when absent (→ no intent)."""
    return f"user:{user_id}" if user_id else None


def _resolve_requested_approver(event) -> str | None:
    """The assigned approver of a ``workflow.approval.requested`` event."""
    return _user_ref(_payload(event).get("approver_user_id"))


def _resolve_reassigned_approver(event) -> str | None:
    """The newly assigned approver of a ``workflow.approval.reassigned`` event."""
    return _user_ref(_payload(event).get("to_approver"))


# --- canonical mapping registry (explicit, reviewable, content-free) ---------

@dataclass(frozen=True)
class NotificationMapping:
    """An explicit, versioned event-to-notification mapping. Contains **no** content."""

    mapping_id: str
    source_event_type: str
    notification_purpose: str
    recipient_resolver: Callable[[object], str | None]
    channel: str = "in_app"
    recipient_type: str = "user"
    consent_required: bool = False
    enabled: bool = True
    version: int = 1
    #: A stable *template reference* stored in the ledger ``title`` — never a rendered body.
    template_ref: str = ""

    def template(self) -> str:
        return self.template_ref or f"template:{self.notification_purpose}"


_MAPPINGS: dict[str, NotificationMapping] = {}


def register_mapping(mapping: NotificationMapping) -> None:
    """Register an event-to-notification mapping (one per event type; rejects a duplicate)."""
    if mapping.source_event_type in _MAPPINGS:
        raise ValueError(f"Mapping already registered for event type: {mapping.source_event_type!r}")
    _MAPPINGS[mapping.source_event_type] = mapping


def get_mapping(event_type: str | None) -> NotificationMapping | None:
    """The mapping for an event type, or ``None`` when unmapped."""
    return _MAPPINGS.get(event_type) if event_type else None


def mappings() -> dict[str, NotificationMapping]:
    """A copy of the current mapping registry."""
    return dict(_MAPPINGS)


def clear_mappings() -> None:
    """Remove all mappings (test/support helper)."""
    _MAPPINGS.clear()


#: The F5.4 approved initial mappings — deliberately narrow. Both derive an explicit,
#: unambiguous recipient (the assigned approver) directly from the event payload; both
#: already carry validated notification meaning ("you have an approval to act on").
def build_default_mappings() -> list[NotificationMapping]:
    return [
        NotificationMapping(
            mapping_id="workflow.approval.requested.v1",
            source_event_type=approval_event_type("requested"),
            notification_purpose=approval_event_type("requested"),
            recipient_resolver=_resolve_requested_approver,
        ),
        NotificationMapping(
            mapping_id="workflow.approval.reassigned.v1",
            source_event_type=approval_event_type("reassigned"),
            notification_purpose=approval_event_type("reassigned"),
            recipient_resolver=_resolve_reassigned_approver,
        ),
    ]


def install_default_mappings() -> None:
    """Install the approved default mappings (idempotent)."""
    for mapping in build_default_mappings():
        if mapping.source_event_type not in _MAPPINGS:
            register_mapping(mapping)


# --- idempotency -------------------------------------------------------------

def intent_dedupe_key(mapping: NotificationMapping, source_event_id: str | None, recipient_ref: str) -> str:
    """Deterministic, restart-surviving dedupe key. The F5.1 unique constraint enforces it."""
    return ":".join((
        DEDUPE_NAMESPACE, mapping.mapping_id, source_event_id or "-",
        recipient_ref, mapping.channel, mapping.notification_purpose,
    ))


# --- decision -> ledger status ----------------------------------------------

#: Normative F5.3-outcome policy. ``allowed`` -> a pending intent; ``suppressed`` and
#: ``disabled`` -> a non-deliverable ledger record preserving the decision; ``not_applicable``
#: -> no row.
_DECISION_TO_STATUS: dict[str, str] = {
    ALLOWED: ledger.PENDING,
    DECISION_SUPPRESSED: ledger.SUPPRESSED,
    DECISION_DISABLED: ledger.DISABLED,
}
_STATUS_TO_OUTCOME: dict[str, str] = {
    ledger.PENDING: CREATED,
    ledger.SUPPRESSED: SUPPRESSED,
    ledger.DISABLED: DISABLED,
}


# --- the intent-creation service ---------------------------------------------

def create_intent_for_event(event, *, registry=None, decision_fn=None, now=None) -> IntentResult:
    """Derive and record (or suppress) a notification intent for one event.

    Decision + intent only — never dispatches, calls a provider, retries, records a
    delivery attempt, mutates workflow/domain/evidence/preference/consent state, or
    emits notification audit/evidence. Deterministic and idempotent.
    """
    event_type = _event_type(event)
    source_event_id = _event_id(event)
    mapping = get_mapping(event_type)

    def _mk(outcome, *, mapping_id=None, channel=None, recipient_ref=None,
            notification_uid=None, decision_reason_code=None, description=""):
        return IntentResult(
            outcome=outcome, source_event_id=source_event_id, source_event_type=event_type,
            mapping_id=mapping_id, channel=channel, recipient_ref=recipient_ref,
            notification_uid=notification_uid, decision_reason_code=decision_reason_code,
            description=description,
        )

    # 1. unmapped or disabled mapping -> explicit no-op (never fuzzy matching)
    if mapping is None:
        return _mk(NOT_APPLICABLE, description="no notification mapping for event type")
    if not mapping.enabled:
        return _mk(NOT_APPLICABLE, mapping_id=mapping.mapping_id, channel=mapping.channel,
                   description="notification mapping is disabled")

    # 2. deterministic recipient reference; absent -> no intent
    recipient_ref = mapping.recipient_resolver(event)
    if not recipient_ref:
        return _mk(NOT_APPLICABLE, mapping_id=mapping.mapping_id, channel=mapping.channel,
                   description="recipient reference is not derivable from the event")

    # 3. F5.3 decision (never bypassed; never mutates preference/consent)
    decide = decision_fn or evaluate_delivery
    decision = decide(
        mapping.recipient_type, recipient_ref, mapping.channel, mapping.notification_purpose,
        registry=registry, consent_required=({mapping.channel} if mapping.consent_required else frozenset()),
        now=now,
    )

    # 4. not_applicable -> no row
    if decision.decision == DECISION_NOT_APPLICABLE:
        return _mk(NOT_APPLICABLE, mapping_id=mapping.mapping_id, channel=mapping.channel,
                   recipient_ref=recipient_ref, decision_reason_code=decision.reason_code,
                   description="channel is not applicable for this recipient")

    status = _DECISION_TO_STATUS.get(decision.decision)
    if status is None:  # defensive: unrecognized decision -> deterministic, safe no-op
        return _mk(FAILED, mapping_id=mapping.mapping_id, channel=mapping.channel,
                   recipient_ref=recipient_ref, decision_reason_code=decision.reason_code,
                   description="unrecognized delivery decision")

    # 5. idempotent record in the F5.1 ledger (references only; content-minimal)
    key = intent_dedupe_key(mapping, source_event_id, recipient_ref)
    existing = ledger.get_notification(dedupe_key=key)
    metadata = {
        "mapping_id": mapping.mapping_id, "mapping_version": mapping.version,
        "source_event_type": event_type, "purpose": mapping.notification_purpose,
        "correlation_id": _correlation_id(event), "causation_id": _causation_id(event),
        "decision": {
            "decision": decision.decision, "reason_code": decision.reason_code,
            "source_ref": decision.source_ref, "effective_ref": decision.effective_ref,
        },
        "references": _reference_ids(event),
    }
    record = ledger.record_notification(
        notification_type=mapping.notification_purpose, recipient_type=mapping.recipient_type,
        recipient_ref=recipient_ref, channel=mapping.channel, title=mapping.template(),
        body=None, status=status, dedupe_key=key, source_event_id=source_event_id,
        source_ref=_subject_ref(event), metadata=metadata,
    )
    outcome = ALREADY_EXISTS if existing is not None else _STATUS_TO_OUTCOME[status]
    return _mk(outcome, mapping_id=mapping.mapping_id, channel=mapping.channel,
               recipient_ref=recipient_ref, notification_uid=record.notification_uid,
               decision_reason_code=decision.reason_code,
               description=f"intent {outcome} ({decision.reason_code})")


def _reference_ids(event) -> dict:
    """Reference ids from the event payload — references only, never PII/content."""
    p = _payload(event)
    keep = ("workflow_instance_id", "workflow_step_id", "approval_id", "kind")
    return {k: p[k] for k in keep if k in p}


# --- consumer registration (dark-launched, like F4.4) ------------------------

def on_notification_event(event) -> None:
    """Outbox handler: create the notification intent for an approved event.

    Idempotent (dedupe_key + outbox ``outbox_processed_events``); infrastructure errors
    propagate so the at-least-once outbox retries without creating a duplicate intent.
    """
    create_intent_for_event(event)


def register_notification_consumers() -> None:
    """Install default mappings and subscribe the intent consumer (idempotent).

    Dark-launched: invoked only from the gated outbox block in the scheduler, so no
    subscriber exists until the dispatcher is explicitly enabled.
    """
    from app.platform.outbox import subscribe

    install_default_mappings()
    for event_type in _MAPPINGS:
        subscribe(event_type, on_notification_event)
