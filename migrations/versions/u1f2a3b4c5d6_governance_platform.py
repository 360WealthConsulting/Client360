"""Data Governance platform (Phase D.23).

Data Governance is a new authoritative GOVERNANCE domain that owns governance metadata only — data
domains/elements, lineage (non-person entities), quality rules/checks/findings, duplicate
candidates, merge decisions, survivorship rules, retention assignments, legal holds,
deletion/archival reviews, remediation cases/exceptions/certifications, and an append-only audit
ledger. It **never owns canonical business records**, **reuses** the existing matching/merge
infrastructure (never performs an unsafe merge itself), **references** the Document Platform
retention policies, and **never issues a hard DELETE**.

Tables (14): governance_data_domains, governance_data_elements, governance_lineage,
governance_quality_rules, governance_quality_checks, governance_quality_findings,
governance_duplicate_candidates, governance_survivorship_rules, governance_merge_decisions,
governance_retention_assignments, governance_legal_holds, governance_deletion_requests,
governance_cases, and governance_events (APPEND-ONLY, trigger-blocked).

Also **widens the Automation JOB_TYPES CHECK constraints** to add three governance job types
(governance_quality_scan / governance_stale_scan / governance_retention_review) so Automation may
run governance checks/scans/reviews. Seeds 5 governance.* capabilities + a default data domain +
two starter quality rules. Additive and reversible. Single Alembic head (down ``t0e1f2a3b4c5``).
"""
import sqlalchemy as sa
from alembic import op

revision = "u1f2a3b4c5d6"
down_revision = "t0e1f2a3b4c5"
branch_labels = None
depends_on = None

_GOV_ENTITY_TYPES = ("person", "household", "organization", "account", "source_contact", "document")
_DATA_CLASSIFICATIONS = ("pii", "financial", "sensitive", "internal", "public")
_QUALITY_RULE_TYPES = ("required_field", "format", "referential_integrity", "date_validity",
                       "duplicate", "source_disagreement", "stale", "orphan", "missing_ownership",
                       "unresolved_matching", "retention_conflict")
_SEVERITIES = ("low", "medium", "high", "critical")
_CHECK_STATUSES = ("running", "completed", "failed")
_FINDING_STATUSES = ("open", "acknowledged", "resolved", "waived", "false_positive")
_CANDIDATE_STATUSES = ("open", "confirmed", "rejected", "merged")
_MERGE_DECISIONS = ("approved", "rejected", "deferred")
_SURVIVORSHIP_STRATEGIES = ("most_recent", "most_complete", "source_priority", "manual")
_RETENTION_STATUSES = ("active", "expired", "archived", "held")
_HOLD_STATUSES = ("active", "released")
_REQUEST_TYPES = ("deletion", "archival")
_REQUEST_STATUSES = ("draft", "submitted", "under_review", "approved", "rejected", "executed",
                     "cancelled")
_CASE_TYPES = ("remediation", "exception", "certification")
_CASE_STATUSES = ("open", "in_progress", "resolved", "closed", "waived")

# Automation job_types (D.22) widened with three governance job types.
_JOB_TYPES_OLD = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
                  "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
                  "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
                  "maintenance", "custom")
_JOB_TYPES_NEW = _JOB_TYPES_OLD + ("governance_quality_scan", "governance_stale_scan",
                                   "governance_retention_review")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _widen_job_type_check(table, constraint):
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, _in("job_type", _JOB_TYPES_NEW))


def _narrow_job_type_check(table, constraint):
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, _in("job_type", _JOB_TYPES_OLD))


_CAPS = (
    ("governance.view", "View governance domains, findings, lineage, retention, and holds.", False,
     ("administrator", "operations", "advisor", "compliance")),
    ("governance.manage", "Manage governance rules, findings, duplicates, retention, and cases.", False,
     ("administrator", "operations", "compliance")),
    ("governance.review", "Approve merges, deletions, archival, and legal holds.", True,
     ("administrator", "compliance")),
    ("governance.audit", "View governance audit history and lineage provenance.", True,
     ("administrator", "compliance")),
    ("governance.admin", "Administer the data governance platform.", True, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "governance_data_domains",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("steward_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("domain_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "governance_data_elements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("data_domain_id", sa.Integer,
                  sa.ForeignKey("governance_data_domains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False, server_default="person"),
        sa.Column("field_name", sa.Text),
        sa.Column("classification", sa.Text, nullable=False, server_default="internal"),
        sa.Column("required", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("description", sa.Text),
        sa.Column("element_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("classification", _DATA_CLASSIFICATIONS), name="ck_gov_element_class"),
    )
    op.create_table(
        "governance_lineage",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("source_system", sa.Text, nullable=False),
        sa.Column("source_reference", sa.Text),
        sa.Column("source_hash", sa.Text),
        sa.Column("source_contact_id", sa.Integer, sa.ForeignKey("source_contacts.id", ondelete="SET NULL")),
        sa.Column("lineage_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("entity_type", _GOV_ENTITY_TYPES), name="ck_gov_lineage_entity"),
    )
    op.create_index("ix_gov_lineage_entity", "governance_lineage", ["entity_type", "entity_id"])

    op.create_table(
        "governance_quality_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("rule_type", sa.Text, nullable=False),
        sa.Column("data_element_id", sa.Integer,
                  sa.ForeignKey("governance_data_elements.id", ondelete="SET NULL")),
        sa.Column("entity_type", sa.Text, nullable=False, server_default="person"),
        sa.Column("config", sa.JSON),
        sa.Column("severity", sa.Text, nullable=False, server_default="medium"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("rule_type", _QUALITY_RULE_TYPES), name="ck_gov_quality_rule_type"),
        sa.CheckConstraint(_in("severity", _SEVERITIES), name="ck_gov_quality_rule_severity"),
    )
    op.create_table(
        "governance_quality_checks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("governance_quality_rules.id", ondelete="SET NULL")),
        sa.Column("run_type", sa.Text, nullable=False, server_default="manual"),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("records_scanned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("findings_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("automation_run_id", sa.Integer),
        sa.Column("triggered_by_user_id", sa.Integer),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("check_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _CHECK_STATUSES), name="ck_gov_quality_check_status"),
    )
    op.create_table(
        "governance_quality_findings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("governance_quality_rules.id", ondelete="SET NULL")),
        sa.Column("check_id", sa.Integer, sa.ForeignKey("governance_quality_checks.id", ondelete="SET NULL")),
        sa.Column("data_element_id", sa.Integer,
                  sa.ForeignKey("governance_data_elements.id", ondelete="SET NULL")),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("finding_type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False, server_default="medium"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("detail", sa.Text),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("remediation_case_id", sa.Integer),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_user_id", sa.Integer),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("severity", _SEVERITIES), name="ck_gov_finding_severity"),
        sa.CheckConstraint(_in("status", _FINDING_STATUSES), name="ck_gov_finding_status"),
    )
    op.create_index("ix_gov_findings_status", "governance_quality_findings", ["status"])
    op.create_index("ix_gov_findings_entity", "governance_quality_findings", ["entity_type", "entity_id"])

    op.create_table(
        "governance_duplicate_candidates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False, server_default="person"),
        sa.Column("primary_entity_id", sa.Integer),
        sa.Column("duplicate_entity_id", sa.Integer),
        sa.Column("match_method", sa.Text),
        sa.Column("match_score", sa.Numeric(5, 2)),
        sa.Column("group_key", sa.Text),
        sa.Column("source_contact_ids", sa.JSON),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("merge_decision_id", sa.Integer),
        sa.Column("detected_by", sa.Text, nullable=False, server_default="matching"),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _CANDIDATE_STATUSES), name="ck_gov_candidate_status"),
    )
    op.create_index("ix_gov_candidates_status", "governance_duplicate_candidates", ["status"])

    op.create_table(
        "governance_survivorship_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("data_element_id", sa.Integer,
                  sa.ForeignKey("governance_data_elements.id", ondelete="SET NULL")),
        sa.Column("entity_type", sa.Text, nullable=False, server_default="person"),
        sa.Column("strategy", sa.Text, nullable=False, server_default="most_recent"),
        sa.Column("source_priority", sa.JSON),
        sa.Column("description", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("strategy", _SURVIVORSHIP_STRATEGIES), name="ck_gov_survivorship_strategy"),
    )
    op.create_table(
        "governance_merge_decisions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("duplicate_candidate_id", sa.Integer,
                  sa.ForeignKey("governance_duplicate_candidates.id", ondelete="SET NULL")),
        sa.Column("survivorship_rule_id", sa.Integer,
                  sa.ForeignKey("governance_survivorship_rules.id", ondelete="SET NULL")),
        sa.Column("decision", sa.Text, nullable=False, server_default="deferred"),
        sa.Column("golden_record_entity_type", sa.Text),
        sa.Column("golden_record_entity_id", sa.Integer),
        sa.Column("merged_person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("source_contact_ids", sa.JSON),
        sa.Column("group_key", sa.Text),
        sa.Column("workflow_instance_id", sa.Integer,
                  sa.ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        sa.Column("notes", sa.Text),
        sa.Column("decided_by_user_id", sa.Integer),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("decision", _MERGE_DECISIONS), name="ck_gov_merge_decision"),
    )
    op.create_table(
        "governance_retention_assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("retention_policy_id", sa.Integer,
                  sa.ForeignKey("document_retention_policies.id", ondelete="SET NULL")),
        sa.Column("classification", sa.Text),
        sa.Column("retention_start_event", sa.Text),
        sa.Column("effective_date", sa.Date),
        sa.Column("expiration_date", sa.Date),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("archival_eligible", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("deletion_eligible", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _RETENTION_STATUSES), name="ck_gov_retention_status"),
        sa.CheckConstraint(_in("entity_type", _GOV_ENTITY_TYPES), name="ck_gov_retention_entity"),
        sa.UniqueConstraint("entity_type", "entity_id", "retention_policy_id",
                            name="uq_gov_retention_assignment"),
    )
    op.create_table(
        "governance_legal_holds",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("approving_compliance_review_id", sa.BigInteger),
        sa.Column("workflow_instance_id", sa.Integer,
                  sa.ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("placed_by_user_id", sa.Integer),
        sa.Column("placed_at", sa.DateTime(timezone=True)),
        sa.Column("released_by_user_id", sa.Integer),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("hold_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _HOLD_STATUSES), name="ck_gov_legal_hold_status"),
        sa.CheckConstraint(_in("entity_type", _GOV_ENTITY_TYPES), name="ck_gov_legal_hold_entity"),
    )
    op.create_index("ix_gov_legal_holds_entity", "governance_legal_holds", ["entity_type", "entity_id"])

    op.create_table(
        "governance_deletion_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("request_type", sa.Text, nullable=False, server_default="deletion"),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("legal_hold_blocked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("compliance_review_id", sa.BigInteger),
        sa.Column("workflow_instance_id", sa.Integer,
                  sa.ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        sa.Column("evidence_reference", sa.Text),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("reviewed_by_user_id", sa.Integer),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_user_id", sa.Integer),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("request_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("request_type", _REQUEST_TYPES), name="ck_gov_deletion_request_type"),
        sa.CheckConstraint(_in("status", _REQUEST_STATUSES), name="ck_gov_deletion_request_status"),
        sa.CheckConstraint(_in("entity_type", _GOV_ENTITY_TYPES), name="ck_gov_deletion_request_entity"),
    )
    op.create_index("ix_gov_deletion_requests_status", "governance_deletion_requests", ["status"])

    op.create_table(
        "governance_cases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("case_type", sa.Text, nullable=False, server_default="remediation"),
        sa.Column("finding_id", sa.Integer, sa.ForeignKey("governance_quality_findings.id", ondelete="SET NULL")),
        sa.Column("entity_type", sa.Text),
        sa.Column("entity_id", sa.Integer),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("priority", sa.Text, nullable=False, server_default="normal"),
        sa.Column("assigned_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("workflow_instance_id", sa.Integer,
                  sa.ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        sa.Column("description", sa.Text),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("case_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("case_type", _CASE_TYPES), name="ck_gov_case_type"),
        sa.CheckConstraint(_in("status", _CASE_STATUSES), name="ck_gov_case_status"),
    )

    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    op.create_table(
        "governance_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("payload", sa.JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_gov_events_entity", "governance_events", ["entity_type", "entity_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_governance_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'governance_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER governance_events_immutable BEFORE UPDATE OR DELETE ON governance_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_governance_event_mutation()"
    )

    # Widen the Automation JOB_TYPES CHECKs so Automation may run governance jobs (D.22 reuse).
    _widen_job_type_check("automation_jobs", "ck_automation_job_type")
    _widen_job_type_check("automation_job_templates", "ck_automation_template_job_type")

    # Seed a default data domain + two starter quality rules (idempotent by code).
    if bind.execute(sa.text("SELECT id FROM governance_data_domains WHERE code='client_identity'")).scalar() is None:
        bind.execute(sa.text(
            "INSERT INTO governance_data_domains (code, name, description) "
            "VALUES ('client_identity', 'Client Identity', 'People, households, and identity data')"))
    for code, name, rtype, sev in (
        ("person_required_email", "Person requires email", "required_field", "medium"),
        ("account_orphan", "Orphan accounts (no person or household)", "orphan", "high"),
    ):
        if bind.execute(sa.text("SELECT id FROM governance_quality_rules WHERE code=:c"), {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO governance_quality_rules (code, name, rule_type, entity_type, severity) "
                "VALUES (:c, :n, :t, 'person', :s)"), {"c": code, "n": name, "t": rtype, "s": sev})

    # Seed capabilities (idempotent).
    for code, description, sensitive, roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, :s) RETURNING id"),
                {"c": code, "d": description, "s": sensitive}).scalar()
        for role_code in roles:
            role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
            if role_id is None:
                continue
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(sa.text("INSERT INTO role_capabilities (role_id, capability_id) "
                                     "VALUES (:r, :c)"), {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    for code, _d, _s, _roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})

    _narrow_job_type_check("automation_jobs", "ck_automation_job_type")
    _narrow_job_type_check("automation_job_templates", "ck_automation_template_job_type")

    op.execute("DROP TRIGGER IF EXISTS governance_events_immutable ON governance_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_governance_event_mutation()")
    op.drop_table("governance_events")
    op.drop_table("governance_cases")
    op.drop_table("governance_deletion_requests")
    op.drop_table("governance_legal_holds")
    op.drop_table("governance_retention_assignments")
    op.drop_table("governance_merge_decisions")
    op.drop_table("governance_survivorship_rules")
    op.drop_table("governance_duplicate_candidates")
    op.drop_table("governance_quality_findings")
    op.drop_table("governance_quality_checks")
    op.drop_table("governance_quality_rules")
    op.drop_table("governance_lineage")
    op.drop_table("governance_data_elements")
    op.drop_table("governance_data_domains")
