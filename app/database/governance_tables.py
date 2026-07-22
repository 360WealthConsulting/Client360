"""Declared schema for the Phase D.23 Data Governance platform.

Mirrors the live schema created by migration ``u1f2a3b4c5d6``. Data Governance is a new authoritative
GOVERNANCE domain that owns **governance metadata only** — data domains/elements, lineage (for
non-person entities), quality rules/checks/findings, duplicate candidates, merge decisions,
survivorship rules, retention assignments, legal holds, deletion/archival reviews, remediation
cases/exceptions/certifications, and an append-only audit ledger. It **never owns canonical business
records** and never becomes their source of truth. Canonical People/Households/Organizations/Accounts
remain authoritative in their existing domains.

It **reuses** the existing deterministic matching/merge infrastructure (``source_contacts``,
``person_source_links``, ``match_review_decisions``, ``person_merge.merge_source_contacts``,
``promote.*``) and the Document Platform retention model (``document_retention_policies``) — it
references those, never replaces them, never performs an unsafe merge, and never issues a hard
DELETE. Entity references are polymorphic (``entity_type``/``entity_id``, no FK to canonical tables);
``person_id``/``household_id`` are optional anchors (``ON DELETE SET NULL``) for record scope and
guarded timeline publication. ``governance_events`` is the append-only audit ledger (trigger-blocked
BEFORE UPDATE OR DELETE).
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    func,
)

# Deterministic controlled vocabularies (metadata only).
GOV_ENTITY_TYPES = ("person", "household", "organization", "account", "source_contact", "document")
DATA_CLASSIFICATIONS = ("pii", "financial", "sensitive", "internal", "public")
QUALITY_RULE_TYPES = ("required_field", "format", "referential_integrity", "date_validity",
                      "duplicate", "source_disagreement", "stale", "orphan", "missing_ownership",
                      "unresolved_matching", "retention_conflict")
SEVERITIES = ("low", "medium", "high", "critical")
CHECK_STATUSES = ("running", "completed", "failed")
FINDING_STATUSES = ("open", "acknowledged", "resolved", "waived", "false_positive")
CANDIDATE_STATUSES = ("open", "confirmed", "rejected", "merged")
MERGE_DECISIONS = ("approved", "rejected", "deferred")
SURVIVORSHIP_STRATEGIES = ("most_recent", "most_complete", "source_priority", "manual")
RETENTION_STATUSES = ("active", "expired", "archived", "held")
HOLD_STATUSES = ("active", "released")
REQUEST_TYPES = ("deletion", "archival")
REQUEST_STATUSES = ("draft", "submitted", "under_review", "approved", "rejected", "executed",
                    "cancelled")
CASE_TYPES = ("remediation", "exception", "certification")
CASE_STATUSES = ("open", "in_progress", "resolved", "closed", "waived")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_governance_tables(metadata: MetaData):
    domains = Table(
        "governance_data_domains", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("steward_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("domain_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    elements = Table(
        "governance_data_elements", metadata,
        Column("id", Integer, primary_key=True),
        Column("data_domain_id", Integer,
               ForeignKey("governance_data_domains.id", ondelete="CASCADE"), nullable=False),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("entity_type", Text, nullable=False, server_default="person"),
        Column("field_name", Text),
        Column("classification", Text, nullable=False, server_default="internal"),
        Column("required", Boolean, nullable=False, server_default="false"),
        Column("description", Text),
        Column("element_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("classification", DATA_CLASSIFICATIONS), name="ck_gov_element_class"),
    )
    lineage = Table(
        "governance_lineage", metadata,
        Column("id", Integer, primary_key=True),
        Column("entity_type", Text, nullable=False),   # non-person entities (person lineage is
        Column("entity_id", Integer, nullable=False),  # read from person_source_links, not shadowed)
        Column("source_system", Text, nullable=False),
        Column("source_reference", Text),              # source_record_id
        Column("source_hash", Text),
        Column("source_contact_id", Integer, ForeignKey("source_contacts.id", ondelete="SET NULL")),
        Column("lineage_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("entity_type", GOV_ENTITY_TYPES), name="ck_gov_lineage_entity"),
    )
    quality_rules = Table(
        "governance_quality_rules", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("rule_type", Text, nullable=False),
        Column("data_element_id", Integer,
               ForeignKey("governance_data_elements.id", ondelete="SET NULL")),
        Column("entity_type", Text, nullable=False, server_default="person"),
        Column("config", JSON),
        Column("severity", Text, nullable=False, server_default="medium"),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("rule_type", QUALITY_RULE_TYPES), name="ck_gov_quality_rule_type"),
        CheckConstraint(_in("severity", SEVERITIES), name="ck_gov_quality_rule_severity"),
    )
    quality_checks = Table(
        "governance_quality_checks", metadata,
        Column("id", Integer, primary_key=True),
        Column("rule_id", Integer, ForeignKey("governance_quality_rules.id", ondelete="SET NULL")),
        Column("run_type", Text, nullable=False, server_default="manual"),
        Column("status", Text, nullable=False, server_default="running"),
        Column("records_scanned", Integer, nullable=False, server_default="0"),
        Column("findings_count", Integer, nullable=False, server_default="0"),
        Column("automation_run_id", Integer),          # plain reference to an automation run
        Column("triggered_by_user_id", Integer),
        Column("started_at", DateTime(timezone=True)),
        Column("finished_at", DateTime(timezone=True)),
        Column("check_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", CHECK_STATUSES), name="ck_gov_quality_check_status"),
    )
    findings = Table(
        "governance_quality_findings", metadata,
        Column("id", Integer, primary_key=True),
        Column("rule_id", Integer, ForeignKey("governance_quality_rules.id", ondelete="SET NULL")),
        Column("check_id", Integer, ForeignKey("governance_quality_checks.id", ondelete="SET NULL")),
        Column("data_element_id", Integer,
               ForeignKey("governance_data_elements.id", ondelete="SET NULL")),
        Column("entity_type", Text, nullable=False),
        Column("entity_id", Integer, nullable=False),
        Column("finding_type", Text, nullable=False),
        Column("severity", Text, nullable=False, server_default="medium"),
        Column("status", Text, nullable=False, server_default="open"),
        Column("detail", Text),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("remediation_case_id", Integer),        # set later (FK added conceptually via cases)
        Column("resolved_at", DateTime(timezone=True)),
        Column("resolved_by_user_id", Integer),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("severity", SEVERITIES), name="ck_gov_finding_severity"),
        CheckConstraint(_in("status", FINDING_STATUSES), name="ck_gov_finding_status"),
    )
    duplicates = Table(
        "governance_duplicate_candidates", metadata,
        Column("id", Integer, primary_key=True),
        Column("entity_type", Text, nullable=False, server_default="person"),
        Column("primary_entity_id", Integer),
        Column("duplicate_entity_id", Integer),
        Column("match_method", Text),
        Column("match_score", Numeric(5, 2)),
        Column("group_key", Text),                     # references match_review_decisions.group_key
        Column("source_contact_ids", JSON),
        Column("status", Text, nullable=False, server_default="open"),
        Column("merge_decision_id", Integer),
        Column("detected_by", Text, nullable=False, server_default="matching"),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", CANDIDATE_STATUSES), name="ck_gov_candidate_status"),
    )
    survivorship = Table(
        "governance_survivorship_rules", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("data_element_id", Integer,
               ForeignKey("governance_data_elements.id", ondelete="SET NULL")),
        Column("entity_type", Text, nullable=False, server_default="person"),
        Column("strategy", Text, nullable=False, server_default="most_recent"),
        Column("source_priority", JSON),               # ordered list of source_systems
        Column("description", Text),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("strategy", SURVIVORSHIP_STRATEGIES), name="ck_gov_survivorship_strategy"),
    )
    merge_decisions = Table(
        "governance_merge_decisions", metadata,
        Column("id", Integer, primary_key=True),
        Column("duplicate_candidate_id", Integer,
               ForeignKey("governance_duplicate_candidates.id", ondelete="SET NULL")),
        Column("survivorship_rule_id", Integer,
               ForeignKey("governance_survivorship_rules.id", ondelete="SET NULL")),
        Column("decision", Text, nullable=False, server_default="deferred"),
        # golden record reference (the surviving canonical record) — reference only, never owned.
        Column("golden_record_entity_type", Text),
        Column("golden_record_entity_id", Integer),
        Column("merged_person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("source_contact_ids", JSON),
        Column("group_key", Text),
        Column("workflow_instance_id", Integer,
               ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        Column("notes", Text),
        Column("decided_by_user_id", Integer),
        Column("decided_at", DateTime(timezone=True)),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("decision", MERGE_DECISIONS), name="ck_gov_merge_decision"),
    )
    retention = Table(
        "governance_retention_assignments", metadata,
        Column("id", Integer, primary_key=True),
        Column("entity_type", Text, nullable=False),
        Column("entity_id", Integer, nullable=False),
        # References the Document Platform retention policy vocabulary (never a parallel policy).
        Column("retention_policy_id", Integer,
               ForeignKey("document_retention_policies.id", ondelete="SET NULL")),
        Column("classification", Text),
        Column("retention_start_event", Text),
        Column("effective_date", Date),
        Column("expiration_date", Date),               # deterministically derived
        Column("status", Text, nullable=False, server_default="active"),
        Column("archival_eligible", Boolean, nullable=False, server_default="false"),
        Column("deletion_eligible", Boolean, nullable=False, server_default="false"),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", RETENTION_STATUSES), name="ck_gov_retention_status"),
        CheckConstraint(_in("entity_type", GOV_ENTITY_TYPES), name="ck_gov_retention_entity"),
        UniqueConstraint("entity_type", "entity_id", "retention_policy_id",
                         name="uq_gov_retention_assignment"),
    )
    legal_holds = Table(
        "governance_legal_holds", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("entity_type", Text, nullable=False),
        Column("entity_id", Integer, nullable=False),
        Column("reason", Text),
        Column("status", Text, nullable=False, server_default="active"),
        Column("approving_compliance_review_id", BigInteger),   # plain ref (compliance owns it)
        Column("workflow_instance_id", Integer,
               ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("placed_by_user_id", Integer),
        Column("placed_at", DateTime(timezone=True)),
        Column("released_by_user_id", Integer),
        Column("released_at", DateTime(timezone=True)),
        Column("hold_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", HOLD_STATUSES), name="ck_gov_legal_hold_status"),
        CheckConstraint(_in("entity_type", GOV_ENTITY_TYPES), name="ck_gov_legal_hold_entity"),
    )
    deletion_requests = Table(
        "governance_deletion_requests", metadata,
        Column("id", Integer, primary_key=True),
        Column("request_type", Text, nullable=False, server_default="deletion"),
        Column("entity_type", Text, nullable=False),
        Column("entity_id", Integer, nullable=False),
        Column("reason", Text),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("legal_hold_blocked", Boolean, nullable=False, server_default="false"),
        Column("compliance_review_id", BigInteger),    # plain ref to the approval review
        Column("workflow_instance_id", Integer,
               ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        Column("evidence_reference", Text),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("reviewed_by_user_id", Integer),
        Column("reviewed_at", DateTime(timezone=True)),
        Column("approved_by_user_id", Integer),
        Column("approved_at", DateTime(timezone=True)),
        Column("executed_at", DateTime(timezone=True)),
        Column("request_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("request_type", REQUEST_TYPES), name="ck_gov_deletion_request_type"),
        CheckConstraint(_in("status", REQUEST_STATUSES), name="ck_gov_deletion_request_status"),
        CheckConstraint(_in("entity_type", GOV_ENTITY_TYPES), name="ck_gov_deletion_request_entity"),
    )
    cases = Table(
        "governance_cases", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("title", Text, nullable=False),
        Column("case_type", Text, nullable=False, server_default="remediation"),
        Column("finding_id", Integer, ForeignKey("governance_quality_findings.id", ondelete="SET NULL")),
        Column("entity_type", Text),
        Column("entity_id", Integer),
        Column("status", Text, nullable=False, server_default="open"),
        Column("priority", Text, nullable=False, server_default="normal"),
        Column("assigned_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("workflow_instance_id", Integer,
               ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        Column("description", Text),
        Column("expires_at", DateTime(timezone=True)),   # certification expiry / exception expiry
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("resolved_at", DateTime(timezone=True)),
        Column("case_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("case_type", CASE_TYPES), name="ck_gov_case_type"),
        CheckConstraint(_in("status", CASE_STATUSES), name="ck_gov_case_status"),
    )
    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    events = Table(
        "governance_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # finding | candidate | hold | deletion | case ...
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "governance_data_domains": domains,
        "governance_data_elements": elements,
        "governance_lineage": lineage,
        "governance_quality_rules": quality_rules,
        "governance_quality_checks": quality_checks,
        "governance_quality_findings": findings,
        "governance_duplicate_candidates": duplicates,
        "governance_survivorship_rules": survivorship,
        "governance_merge_decisions": merge_decisions,
        "governance_retention_assignments": retention,
        "governance_legal_holds": legal_holds,
        "governance_deletion_requests": deletion_requests,
        "governance_cases": cases,
        "governance_events": events,
    }
