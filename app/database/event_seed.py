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

# ============================================================================================
# Phase D.35 — Domain Event Producer Adoption.
# Typed, versioned contracts for the audited business write boundaries, so major domains publish
# typed domain facts (past-tense) through the standardized publisher + the existing outbox. Each
# contract is a COMPLETED business fact (not a command), references-only (ids/codes/statuses only —
# never PII/secrets/financials/health/tax figures/document contents), and has a documented producer,
# owner, version, schema, and a (dark-launched) subscriber. Event names use stable, past-tense
# terminology consistent with the codebase; non-existent flows (document e-signature, a compliance
# "completed" status, a compliance exception domain) are intentionally OMITTED — never invented.
# ============================================================================================

# Every D.35 contract is consumed (for now) by a single dark-launched read-model projector — a future
# analytics/timeline consumer — so the model is complete and governable without changing behavior.
_PROJECTION = "analytics.projection"


def _c(event_type, category, name, owner, producer, payload_schema, description):
    return {"event_type": event_type, "category": category, "name": name, "owner": owner,
            "producer": producer, "schema_version": 1, "payload_schema": payload_schema,
            "depends_on": [], "subscribers": [_PROJECTION], "description": description}


D35_CONTRACTS_SEED = [
    # --- people & households ------------------------------------------------------------------
    _c("people.person_created", "people", "Person created", "people", "people.promotion",
       {"person_id": "int", "match_method": "str"},
       "A canonical person record was created (via promotion / manual resolve)."),
    _c("people.person_updated", "people", "Person updated", "people", "people.service",
       {"person_id": "int", "changed_fields": "list"},
       "A canonical person record's contact fields were changed (field names only, no values)."),
    _c("people.identity_merged", "people", "Canonical identity merged", "people", "people.merge",
       {"person_id": "int", "source_contact_count": "int"},
       "Source contacts were merged onto a surviving canonical person."),
    _c("households.household_created", "households", "Household created", "households", "households.service",
       {"household_id": "int"}, "A household was created."),
    _c("households.membership_changed", "households", "Household membership changed", "households",
       "households.service", {"household_id": "int", "person_id": "int", "relationship_type": "str"},
       "A person's household membership was added or updated."),
    # --- opportunities & referrals ------------------------------------------------------------
    _c("opportunity.created", "opportunity", "Opportunity created", "opportunity", "opportunity.service",
       {"opportunity_id": "int", "pipeline_id": "int", "stage_id": "int", "status": "str"},
       "A sales opportunity was created."),
    _c("opportunity.stage_changed", "opportunity", "Opportunity stage changed", "opportunity",
       "opportunity.service",
       {"opportunity_id": "int", "to_stage_id": "int", "from_status": "str", "to_status": "str"},
       "An opportunity moved to a new (non-terminal) pipeline stage."),
    _c("opportunity.won", "opportunity", "Opportunity won", "opportunity", "opportunity.service",
       {"opportunity_id": "int", "status": "str"}, "An opportunity was closed won."),
    _c("opportunity.lost", "opportunity", "Opportunity lost", "opportunity", "opportunity.service",
       {"opportunity_id": "int", "status": "str"}, "An opportunity was closed lost."),
    _c("referral.recorded", "referral", "Referral source recorded", "referral", "referral.service",
       {"referral_source_id": "int", "source_type": "str", "status": "str"},
       "A referral source was recorded."),
    # --- work & operations --------------------------------------------------------------------
    _c("operations.task_created", "operations", "Operational task created", "operations",
       "operations.tasks", {"task_id": "int", "project_id": "int", "status": "str", "priority": "str"},
       "An operational (firm-work) task was created."),
    _c("operations.task_completed", "operations", "Operational task completed", "operations",
       "operations.tasks", {"task_id": "int", "from_status": "str", "to_status": "str"},
       "An operational task was completed."),
    _c("operations.project_created", "operations", "Operational project created", "operations",
       "operations.projects", {"project_id": "int", "category": "str", "status": "str"},
       "An operational project was created."),
    _c("operations.project_status_changed", "operations", "Operational project status changed",
       "operations", "operations.projects",
       {"project_id": "int", "from_status": "str", "to_status": "str"},
       "An operational project changed status."),
    # --- exceptions (the shared engine — covers tax/benefits/insurance/operations) -------------
    _c("exception.opened", "exceptions", "Exception opened", "operations", "exception.engine",
       {"exception_id": "int", "code": "str", "domain": "str", "category": "str", "severity": "str",
        "status": "str"}, "An operational exception was opened (or reopened) by the shared engine."),
    _c("exception.resolved", "exceptions", "Exception resolved", "operations", "exception.engine",
       {"exception_id": "int", "resolution_code": "str", "from_status": "str", "to_status": "str"},
       "An operational exception was resolved."),
    # --- documents ----------------------------------------------------------------------------
    _c("document.registered", "documents", "Document registered", "documents", "document.platform",
       {"document_id": "int", "classification": "str", "status": "str"},
       "A document was registered in the document platform."),
    _c("document.status_changed", "documents", "Document status changed", "documents", "document.platform",
       {"document_id": "int", "from_status": "str", "to_status": "str"},
       "A document changed lifecycle status."),
    _c("document.archived", "documents", "Document archived", "documents", "document.platform",
       {"document_id": "int", "from_status": "str", "to_status": "str"}, "A document was archived."),
    # --- compliance (REGULATORY — events added AFTER the authoritative ledger write) -----------
    _c("compliance.review_opened", "compliance", "Compliance review opened", "compliance",
       "compliance.reviews",
       {"review_id": "int", "status": "str", "governing_rule": "str", "rule_version": "str"},
       "A compliance review was opened (submitted for assignment)."),
    _c("compliance.approval_granted", "compliance", "Compliance approval granted", "compliance",
       "compliance.reviews", {"review_id": "int", "decision_id": "int", "decision": "str"},
       "A compliance review decision granted approval (a completed, approved review)."),
    _c("compliance.approval_denied", "compliance", "Compliance approval denied", "compliance",
       "compliance.reviews", {"review_id": "int", "decision_id": "int", "decision": "str"},
       "A compliance review decision denied/returned approval (a completed, non-approved review)."),
    # --- tax ----------------------------------------------------------------------------------
    _c("tax.engagement_created", "tax", "Tax engagement created", "tax", "tax.domain",
       {"engagement_id": "int", "return_id": "int", "tax_year": "int", "return_type_code": "str"},
       "A tax engagement (and its initial return) was created."),
    _c("tax.return_status_changed", "tax", "Tax return status changed", "tax", "tax.lifecycle",
       {"return_id": "int", "from_status": "str", "to_status": "str"},
       "A tax return moved to a new production status."),
    _c("tax.filing_submitted", "tax", "Tax filing submitted", "tax", "tax.lifecycle",
       {"return_id": "int", "filing_status": "str", "provider_key": "str"},
       "A tax return filing was submitted."),
    _c("tax.filing_acknowledged", "tax", "Tax filing acknowledged", "tax", "tax.lifecycle",
       {"return_id": "int", "filing_status": "str"},
       "A tax return filing acknowledgment was received (accepted / rejected)."),
    # --- insurance & benefits (SENSITIVE domain — references only; NO policy numbers/premiums) -
    _c("insurance.case_created", "insurance", "Insurance case created", "insurance", "insurance.service",
       {"case_id": "int", "case_type": "str", "status": "str"}, "An insurance case was created."),
    _c("insurance.application_status_changed", "insurance", "Insurance application status changed",
       "insurance", "insurance.service",
       {"case_id": "int", "from_status": "str", "to_status": "str"},
       "An insurance case/application changed status."),
    _c("insurance.policy_issued", "insurance", "Insurance policy issued", "insurance", "insurance.service",
       {"policy_id": "int", "status": "str", "carrier_id": "int"},
       "An insurance policy was issued (status → issued)."),
    _c("benefits.enrollment_created", "benefits", "Benefits enrollment created", "benefits",
       "benefits.enrollment",
       {"enrollment_id": "int", "plan_year_id": "int", "coverage_tier": "str", "status": "str"},
       "A benefits enrollment was created."),
    _c("benefits.enrollment_status_changed", "benefits", "Benefits enrollment status changed", "benefits",
       "benefits.enrollment", {"enrollment_id": "int", "from_status": "str", "to_status": "str"},
       "A benefits enrollment changed status."),
]

# The service files that host the D.35 publishing sites — governance scans these to verify every
# registered producer has an actual publish call and every publish call uses a registered event type.
ADOPTION_MODULES = (
    "app/matching/promote.py",
    "app/services/people.py",
    "app/services/person_merge.py",
    "app/routes/households.py",
    "app/services/opportunity/service.py",
    "app/services/referral/service.py",
    "app/services/operations/tasks.py",
    "app/services/operations/projects.py",
    "app/services/exception_engine.py",
    "app/services/document_platform/service.py",
    "app/services/compliance/reviews.py",
    "app/services/tax_domain.py",
    "app/services/tax_return_lifecycle.py",
    "app/services/insurance.py",
    "app/services/benefits_enrollment.py",
)

# The distinct D.35 event categories (business domains adopted).
D35_DOMAINS = sorted({c["category"] for c in D35_CONTRACTS_SEED})

# The event domains the model governs (D.34 + D.35), for coverage reporting.
EVENT_DOMAINS = sorted({c[1] for c in DOMAIN_EVENT_CONTRACTS_SEED} | set(D35_DOMAINS))
