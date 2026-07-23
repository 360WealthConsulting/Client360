"""Pure declarative seed data for the Phase D.36 projection definitions (no app dependencies).

Shared by the D.36 Alembic migration (which seeds ``projection_definitions`` + an initial
``projection_state`` row per projection) and the service-layer projection catalog
(``app/services/projections/definitions.py``, which attaches the executable apply handlers by
projection id) so the registry metadata and the executable definitions cannot drift.

Each projection consumes a set of D.34/D.35 domain-event types from the transactional outbox and
projects them into one query-optimized read-model table. Read models are disposable and rebuildable.
"""

# (projection_id, name, category, owner, read_table, subscribed_events, schema_version, depends_on, desc)
PROJECTION_DEFINITIONS_SEED = [
    ("people.summary", "People Summary", "people", "people", "rm_people_summary",
     ["people.person_created", "people.person_updated", "people.identity_merged"], 1, [],
     "Per-person event summary (create/update/merge counts, last activity)."),
    ("household.summary", "Household Summary", "households", "households", "rm_household_summary",
     ["households.household_created", "households.membership_changed"], 1, [],
     "Per-household summary (creation, membership-change count, last activity)."),
    ("opportunity.pipeline", "Opportunity Pipeline", "opportunity", "opportunity",
     "rm_opportunity_pipeline",
     ["opportunity.created", "opportunity.stage_changed", "opportunity.won", "opportunity.lost"], 1, [],
     "Per-opportunity current stage/status + close (pipeline board)."),
    ("operations.tasks", "Operational Tasks", "operations", "operations", "rm_operational_tasks",
     ["operations.task_created", "operations.task_completed"], 1, [],
     "Per-operational-task status + timestamps."),
    ("operations.projects", "Projects", "operations", "operations", "rm_projects",
     ["operations.project_created", "operations.project_status_changed"], 1, [],
     "Per-operational-project status + category."),
    ("compliance.queue", "Compliance Queue", "compliance", "compliance", "rm_compliance_queue",
     ["compliance.review_opened", "compliance.approval_granted", "compliance.approval_denied"], 1, [],
     "Per-compliance-review status + decision (review queue)."),
    ("tax.pipeline", "Tax Pipeline", "tax", "tax", "rm_tax_pipeline",
     ["tax.engagement_created", "tax.return_status_changed", "tax.filing_submitted",
      "tax.filing_acknowledged"], 1, [], "Per-tax-return production + filing status."),
    ("insurance.pipeline", "Insurance Pipeline", "insurance", "insurance", "rm_insurance_pipeline",
     ["insurance.case_created", "insurance.application_status_changed"], 1, [],
     "Per-insurance-case status (application pipeline)."),
    ("benefits.enrollment", "Benefits Enrollment", "benefits", "benefits", "rm_benefits_enrollment",
     ["benefits.enrollment_created", "benefits.enrollment_status_changed"], 1, [],
     "Per-benefits-enrollment status + coverage tier."),
    ("document.status", "Document Status", "documents", "document_platform", "rm_document_status",
     ["document.registered", "document.status_changed", "document.archived"], 1, [],
     "Per-document current lifecycle status."),
    ("exception.dashboard", "Exception Dashboard", "exceptions", "operations", "rm_exception_dashboard",
     ["exception.opened", "exception.resolved"], 1, [],
     "Per-exception code/domain/severity/status (open-exceptions dashboard)."),
    ("activity.feed", "Activity Feed", "activity", "platform", "rm_activity_feed",
     ["*"], 1, [], "Denormalized append-only feed of every domain event (query-optimized activity stream)."),
]

# The distinct projection categories (for coverage reporting).
PROJECTION_CATEGORIES = sorted({d[2] for d in PROJECTION_DEFINITIONS_SEED})
