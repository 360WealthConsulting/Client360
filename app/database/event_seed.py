"""Pure declarative seed data for the Phase D.34 domain-event contracts (no app dependencies).

Shared by the D.34 Alembic migration (which seeds ``domain_event_contracts`` +
``domain_event_subscriptions``) and the service-layer contract catalog
(``app/services/events/contracts.py``) so the registry metadata and the executable contracts cannot
drift. Contains only plain data — the typed, versioned contracts for the domain events that flow over
the existing transactional outbox, and the durable subscription registry (which consumer subscribes to
which event type). Payload schemas are references-only (ids/codes) — never PII or secrets.

The workflow.* and runtime.coordination contracts formalize event flows that ALREADY exist (the
workflow event envelopes + the D.29 coordination bus); the orchestration.lifecycle contract is the new
D.34 event the orchestration engine publishes (so processes publish domain events rather than directly
invoking every downstream service).
"""

# (event_type, category, name, producer, schema_version, payload_schema, depends_on, description)
DOMAIN_EVENT_CONTRACTS_SEED = [
    ("workflow.transition", "workflow", "Workflow lifecycle transition", "workflow.execution", 1,
     {"instance_id": "int", "from": "str", "to": "str", "action": "str"}, [],
     "A workflow-template instance changed lifecycle state (launch/pause/resume/cancel/complete)."),
    ("workflow.approval", "workflow", "Workflow approval decision", "workflow.approvals", 1,
     {"approval_id": "int", "step_id": "int", "decision": "str"}, [],
     "A workflow approval was requested / decided / reassigned."),
    ("workflow.sla", "workflow", "Workflow SLA escalation", "workflow.sla", 1,
     {"escalation_id": "int", "step_id": "int", "level": "int"}, [],
     "A workflow step breached its SLA and was escalated."),
    ("orchestration.lifecycle", "orchestration", "Orchestration lifecycle event", "orchestration.engine", 1,
     {"instance_id": "int", "definition": "str", "event": "str", "stage": "str"}, [],
     "A major orchestration lifecycle event (launched / completed / failed / cancelled / compensated)."),
    ("runtime.coordination", "runtime", "Runtime coordination event", "runtime.coordination", 1,
     {"generation": "int", "worker": "str", "event": "str"}, [],
     "A distributed-runtime coordination event (generation activated / cache invalidation)."),
]

# (event_type, consumer, owner, description) — the durable subscription registry.
DOMAIN_EVENT_SUBSCRIPTIONS_SEED = [
    ("workflow.transition", "notification.dispatch", "notifications",
     "Notification intents react to workflow transitions."),
    ("workflow.approval", "notification.dispatch", "notifications",
     "Notification intents react to workflow approval decisions."),
    ("workflow.sla", "workflow.automation", "workflow",
     "Workflow automation consumers react to SLA escalations."),
    ("orchestration.lifecycle", "observability.sink", "observability",
     "The observability sink records orchestration lifecycle events."),
    ("runtime.coordination", "runtime.worker", "runtime",
     "Runtime workers converge on coordination events (D.29)."),
]

# The event domains D.34 governs (for coverage reporting) — the distinct categories.
EVENT_DOMAINS = sorted({c[1] for c in DOMAIN_EVENT_CONTRACTS_SEED})
