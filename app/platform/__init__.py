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
from app.platform.workflow_registry import (
    ImmutableTemplateError,
    IncompatibleTemplateError,
    UnknownTemplateError,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
    build_default_registry,
    default_registry,
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
]
