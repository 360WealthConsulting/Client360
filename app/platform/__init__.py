"""Client360 platform infrastructure (E1.6+).

Low-level, domain-agnostic primitives shared across the application:
  * the transactional outbox & dispatcher (Backlog F1.3), and
  * the canonical event envelope & schema versioning (Backlog F1.4).
"""

from app.platform.events import (
    SCHEMA_VERSION,
    Envelope,
    EnvelopeError,
    is_envelope,
    new_event,
    upgrade_envelope,
)
from app.platform.outbox import (
    clear_subscribers,
    dispatch_pending,
    publish,
    publish_event,
    subscribe,
)
from app.platform.workflow_adapter import (
    RegistryBinding,
    bind_instance_template,
    get_binding,
    launch_from_registry,
    resolve_registry_template,
    set_registry_resolver,
)
from app.platform.workflow_approval_state import (
    APPROVAL_DECISIONS,
    APPROVAL_STATES,
    TERMINAL_APPROVAL_STATES,
    can_reassign,
    check_assigned_approver,
    check_decider_not_requester,
    check_independent_requester,
    validate_decidable,
    validate_decision,
    validate_reassignable,
)
from app.platform.workflow_events import (
    APPROVAL_EVENT_TYPES,
    TRANSITION_EVENT_TYPES,
    approval_event_type,
    emit_approval_event,
    emit_transition_event,
    transition_event_type,
    workflow_event_id,
)
from app.platform.workflow_registry import (
    ImmutableTemplateError,
    IncompatibleTemplateError,
    UnknownTemplateError,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
    build_default_registry,
    default_registry,
)
from app.platform.workflow_state_machine import (
    ACTIVE_STEP_STATES,
    STEP_STATES,
    WORKFLOW_ACTIONS,
    WORKFLOW_STATES,
    WORKFLOW_TRANSITIONS,
    assert_lifecycle_invariants,
    dependencies_satisfied,
    instance_is_complete,
    is_valid_transition,
    next_state,
    valid_actions,
    validate_transition,
)

__all__ = [
    # transport (F1.3)
    "publish",
    "publish_event",
    "subscribe",
    "clear_subscribers",
    "dispatch_pending",
    # envelope (F1.4)
    "Envelope",
    "EnvelopeError",
    "new_event",
    "is_envelope",
    "upgrade_envelope",
    "SCHEMA_VERSION",
    # workflow template registry (F1.5)
    "WorkflowTemplate",
    "WorkflowTemplateRegistry",
    "default_registry",
    "build_default_registry",
    "UnknownTemplateError",
    "ImmutableTemplateError",
    "IncompatibleTemplateError",
    # workflow execution adapter (F4.1 / Epic 4, ADR-016)
    "RegistryBinding",
    "resolve_registry_template",
    "bind_instance_template",
    "get_binding",
    "launch_from_registry",
    "set_registry_resolver",
    # workflow state machine (F4.2 / Epic 4, ADR-016)
    "WORKFLOW_STATES",
    "WORKFLOW_TRANSITIONS",
    "WORKFLOW_ACTIONS",
    "STEP_STATES",
    "ACTIVE_STEP_STATES",
    "next_state",
    "is_valid_transition",
    "valid_actions",
    "validate_transition",
    "dependencies_satisfied",
    "instance_is_complete",
    "assert_lifecycle_invariants",
    # workflow event publication (F4.3 / Epic 4, ADR-016)
    "emit_transition_event",
    "transition_event_type",
    "workflow_event_id",
    "TRANSITION_EVENT_TYPES",
    # workflow approval engine (F4.5 / Epic 4, ADR-016)
    "APPROVAL_STATES",
    "APPROVAL_DECISIONS",
    "TERMINAL_APPROVAL_STATES",
    "can_reassign",
    "validate_decision",
    "validate_decidable",
    "validate_reassignable",
    "check_independent_requester",
    "check_decider_not_requester",
    "check_assigned_approver",
    "APPROVAL_EVENT_TYPES",
    "approval_event_type",
    "emit_approval_event",
]
