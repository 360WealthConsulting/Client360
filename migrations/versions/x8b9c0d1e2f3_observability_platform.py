"""Enterprise Observability platform (Phase D.26).

Enterprise Observability is a new authoritative PLATFORM-OPERATIONS domain that owns observability
metadata only — a service inventory + dependency graph, health checks/snapshots, diagnostic checks/
results, telemetry sources/metrics, alert rules/alerts/suppressions, runtime snapshots, environment/
deployment references, maintenance windows, and reliability incidents/findings — plus an append-only
audit ledger. It **owns no business records**, **references** Automation/Integration/Security/
Analytics/Timeline/Audit, and **reuses** the existing health endpoints (``/readiness``), the scheduler
snapshot (``scheduler_status``), the logging config + request-id correlation, and the notification
ledger (alerts reference it — no delivery). It **never replaces** the runtime health/logging/
exception-handling logic and adds no external monitoring stack.

Tables (18). Also **widens the Automation JOB_TYPES CHECK constraints** to add an ``observability_scan``
job type (so Automation may run health/diagnostic scans, telemetry collection, and alert evaluations).
Seeds 5 observability.* capabilities and a small telemetry-source registry. Additive and reversible.
Single Alembic head (down ``w7a8b9c0d1e2``).
"""
import sqlalchemy as sa
from alembic import op

revision = "x8b9c0d1e2f3"
down_revision = "w7a8b9c0d1e2"
branch_labels = None
depends_on = None

_SERVICE_TYPES = ("application", "database", "scheduler", "integration", "external", "queue",
                  "cache", "other")
_SERVICE_STATUSES = ("operational", "degraded", "down", "maintenance", "unknown")
_CRITICALITIES = ("low", "medium", "high", "critical")
_DEPENDENCY_TYPES = ("hard", "soft", "runtime")
_HEALTH_CHECK_TYPES = ("liveness", "readiness", "dependency", "synthetic")
_HEALTH_STATUSES = ("healthy", "degraded", "unhealthy", "unknown")
_DIAGNOSTIC_CATEGORIES = ("database", "scheduler", "migration", "integration", "security",
                          "configuration", "other")
_DIAGNOSTIC_STATUSES = ("pass", "warn", "fail", "error")
_TELEMETRY_SOURCE_TYPES = ("automation", "outbox", "integration", "scheduler", "analytics",
                           "security", "custom")
_METRIC_KINDS = ("gauge", "counter", "rate", "duration")
_AGGREGATIONS = ("last", "sum", "avg", "max", "min")
_ALERT_SEVERITIES = ("info", "warning", "critical")
_ALERT_STATUSES = ("open", "acknowledged", "resolved", "suppressed")
_MAINTENANCE_STATUSES = ("scheduled", "active", "completed", "cancelled")
_ENVIRONMENTS = ("production", "staging", "development", "test")
_INCIDENT_SEVERITIES = ("low", "medium", "high", "critical")
_INCIDENT_STATUSES = ("open", "investigating", "mitigated", "resolved", "closed")
_FINDING_STATUSES = ("open", "acknowledged", "remediated", "accepted", "false_positive")
_FINDING_SOURCES = ("health_scan", "diagnostic", "telemetry", "alert", "manual", "security")
_FINDING_SEVERITIES = ("info", "low", "medium", "high", "critical")

# Automation JOB_TYPES (current 18 = base + governance + integration_sync + security_review) widened.
_JOB_TYPES_OLD = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
                  "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
                  "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
                  "governance_quality_scan", "governance_stale_scan", "governance_retention_review",
                  "integration_sync", "security_review", "maintenance", "custom")
_JOB_TYPES_NEW = _JOB_TYPES_OLD + ("observability_scan",)


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _set_job_type_check(table, constraint, values):
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, _in("job_type", values))


_CAPS = (
    ("observability.view", "View services, health, diagnostics, telemetry, alerts, and reliability "
     "incidents.", False, ("administrator", "operations", "compliance")),
    ("observability.manage", "Create and configure services, health/diagnostic checks, telemetry, "
     "alert rules, suppressions, and maintenance windows.", False, ("administrator", "operations")),
    ("observability.execute", "Run health/diagnostic scans and telemetry collection, acknowledge/"
     "resolve alerts, and manage reliability incidents.", False, ("administrator", "operations")),
    ("observability.audit", "View observability audit history and sensitive diagnostic detail.", True,
     ("administrator", "compliance")),
    ("observability.admin", "Administer the observability platform.", True, ("administrator",)),
)

# (code, name, source_type, reference) — telemetry sources reference existing run-ledgers (no copy).
_SOURCE_SEED = (
    ("automation_runs", "Automation runs", "automation", "automation_runs"),
    ("outbox", "Transactional outbox", "outbox", "outbox_events"),
    ("integration_sync", "Integration sync runs", "integration", "integration_sync_runs"),
    ("scheduler", "Background scheduler", "scheduler", "scheduler_status"),
    ("security_findings", "Security findings", "security", "security_findings"),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "observability_environment_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("environment", sa.Text, nullable=False, server_default="production"),
        sa.Column("region", sa.Text),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("profile_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("environment", _ENVIRONMENTS), name="ck_observability_environment"),
    )
    op.create_table(
        "observability_deployment_references",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("version", sa.Text, nullable=False),
        sa.Column("migration_head", sa.Text),
        sa.Column("environment_profile_id", sa.Integer,
                  sa.ForeignKey("observability_environment_profiles.id", ondelete="SET NULL")),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text),
        sa.Column("deployment_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "observability_services",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("service_type", sa.Text, nullable=False, server_default="application"),
        sa.Column("status", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("criticality", sa.Text, nullable=False, server_default="medium"),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reference_type", sa.Text),
        sa.Column("reference_id", sa.Integer),
        sa.Column("description", sa.Text),
        sa.Column("last_status_at", sa.DateTime(timezone=True)),
        sa.Column("service_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("service_type", _SERVICE_TYPES), name="ck_observability_service_type"),
        sa.CheckConstraint(_in("status", _SERVICE_STATUSES), name="ck_observability_service_status"),
        sa.CheckConstraint(_in("criticality", _CRITICALITIES), name="ck_observability_service_criticality"),
    )
    op.create_table(
        "observability_service_dependencies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("service_id", sa.Integer,
                  sa.ForeignKey("observability_services.id", ondelete="CASCADE"), nullable=False),
        sa.Column("depends_on_service_id", sa.Integer,
                  sa.ForeignKey("observability_services.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dependency_type", sa.Text, nullable=False, server_default="hard"),
        sa.Column("description", sa.Text),
        sa.Column("dependency_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("dependency_type", _DEPENDENCY_TYPES), name="ck_observability_dependency_type"),
        sa.UniqueConstraint("service_id", "depends_on_service_id", name="uq_observability_dependency"),
    )
    op.create_index("ix_observability_dependencies_service", "observability_service_dependencies", ["service_id"])

    op.create_table(
        "observability_health_checks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("observability_services.id", ondelete="SET NULL")),
        sa.Column("check_type", sa.Text, nullable=False, server_default="liveness"),
        sa.Column("target_reference", sa.Text),
        sa.Column("interval_seconds", sa.Integer),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_status", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("check_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("check_type", _HEALTH_CHECK_TYPES), name="ck_observability_health_check_type"),
        sa.CheckConstraint(_in("last_status", _HEALTH_STATUSES), name="ck_observability_health_check_status"),
    )
    op.create_table(
        "observability_health_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("health_check_id", sa.Integer,
                  sa.ForeignKey("observability_health_checks.id", ondelete="SET NULL")),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("observability_services.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("detail", sa.Text),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("snapshot_metadata", sa.JSON),
        sa.CheckConstraint(_in("status", _HEALTH_STATUSES), name="ck_observability_health_snapshot_status"),
    )
    op.create_index("ix_observability_health_snapshots_check", "observability_health_snapshots", ["health_check_id"])

    op.create_table(
        "observability_diagnostic_checks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="other"),
        sa.Column("target_reference", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("check_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("category", _DIAGNOSTIC_CATEGORIES), name="ck_observability_diagnostic_category"),
    )
    op.create_table(
        "observability_diagnostic_results",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("diagnostic_check_id", sa.Integer,
                  sa.ForeignKey("observability_diagnostic_checks.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text, nullable=False, server_default="pass"),
        sa.Column("summary", sa.Text),
        sa.Column("detail", sa.Text),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("result_metadata", sa.JSON),
        sa.CheckConstraint(_in("status", _DIAGNOSTIC_STATUSES), name="ck_observability_diagnostic_status"),
    )
    op.create_index("ix_observability_diagnostic_results_check", "observability_diagnostic_results", ["diagnostic_check_id"])

    op.create_table(
        "observability_telemetry_sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("source_type", sa.Text, nullable=False, server_default="custom"),
        sa.Column("reference", sa.Text),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("source_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("source_type", _TELEMETRY_SOURCE_TYPES), name="ck_observability_telemetry_source_type"),
    )
    op.create_table(
        "observability_telemetry_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("telemetry_source_id", sa.Integer,
                  sa.ForeignKey("observability_telemetry_sources.id", ondelete="SET NULL")),
        sa.Column("metric_kind", sa.Text, nullable=False, server_default="gauge"),
        sa.Column("unit", sa.Text),
        sa.Column("collection_interval_seconds", sa.Integer),
        sa.Column("warning_threshold", sa.Float),
        sa.Column("critical_threshold", sa.Float),
        sa.Column("aggregation", sa.Text, nullable=False, server_default="last"),
        sa.Column("analytics_metric_key", sa.Text),
        sa.Column("last_value", sa.Float),
        sa.Column("last_collected_at", sa.DateTime(timezone=True)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("metric_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("metric_kind", _METRIC_KINDS), name="ck_observability_metric_kind"),
        sa.CheckConstraint(_in("aggregation", _AGGREGATIONS), name="ck_observability_metric_aggregation"),
    )
    op.create_table(
        "observability_maintenance_windows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="scheduled"),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("observability_services.id", ondelete="SET NULL")),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("suppress_alerts", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("window_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _MAINTENANCE_STATUSES), name="ck_observability_maintenance_status"),
    )
    op.create_table(
        "observability_alert_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("telemetry_metric_id", sa.Integer,
                  sa.ForeignKey("observability_telemetry_metrics.id", ondelete="SET NULL")),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("observability_services.id", ondelete="SET NULL")),
        sa.Column("severity", sa.Text, nullable=False, server_default="warning"),
        sa.Column("condition", sa.JSON),
        sa.Column("routing", sa.JSON),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("rule_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("severity", _ALERT_SEVERITIES), name="ck_observability_alert_rule_severity"),
    )
    op.create_table(
        "observability_alert_suppressions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("alert_rule_id", sa.Integer,
                  sa.ForeignKey("observability_alert_rules.id", ondelete="SET NULL")),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("observability_services.id", ondelete="SET NULL")),
        sa.Column("maintenance_window_id", sa.Integer,
                  sa.ForeignKey("observability_maintenance_windows.id", ondelete="SET NULL")),
        sa.Column("reason", sa.Text),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("suppression_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "observability_alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("alert_rule_id", sa.Integer,
                  sa.ForeignKey("observability_alert_rules.id", ondelete="SET NULL")),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("observability_services.id", ondelete="SET NULL")),
        sa.Column("severity", sa.Text, nullable=False, server_default="warning"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("suppression_id", sa.Integer,
                  sa.ForeignKey("observability_alert_suppressions.id", ondelete="SET NULL")),
        sa.Column("notification_ref", sa.Text),
        sa.Column("alert_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("severity", _ALERT_SEVERITIES), name="ck_observability_alert_severity"),
        sa.CheckConstraint(_in("status", _ALERT_STATUSES), name="ck_observability_alert_status"),
    )
    op.create_index("ix_observability_alerts_status", "observability_alerts", ["status"])

    op.create_table(
        "observability_runtime_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("database_ok", sa.Boolean),
        sa.Column("scheduler_running", sa.Boolean),
        sa.Column("scheduler_job_count", sa.Integer),
        sa.Column("migration_head", sa.Text),
        sa.Column("migration_in_sync", sa.Boolean),
        sa.Column("environment_profile_id", sa.Integer,
                  sa.ForeignKey("observability_environment_profiles.id", ondelete="SET NULL")),
        sa.Column("deployment_reference_id", sa.Integer,
                  sa.ForeignKey("observability_deployment_references.id", ondelete="SET NULL")),
        sa.Column("summary", sa.Text),
        sa.Column("snapshot_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_table(
        "observability_reliability_incidents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("category", sa.Text),
        sa.Column("severity", sa.Text, nullable=False, server_default="medium"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("summary", sa.Text),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("observability_services.id", ondelete="SET NULL")),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("detected_at", sa.DateTime(timezone=True)),
        sa.Column("mitigated_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("incident_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("severity", _INCIDENT_SEVERITIES), name="ck_observability_incident_severity"),
        sa.CheckConstraint(_in("status", _INCIDENT_STATUSES), name="ck_observability_incident_status"),
    )
    op.create_index("ix_observability_incidents_status", "observability_reliability_incidents", ["status"])

    op.create_table(
        "observability_reliability_findings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("finding_type", sa.Text, nullable=False, server_default="manual"),
        sa.Column("severity", sa.Text, nullable=False, server_default="medium"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("detail", sa.Text),
        sa.Column("incident_id", sa.Integer,
                  sa.ForeignKey("observability_reliability_incidents.id", ondelete="SET NULL")),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("observability_services.id", ondelete="SET NULL")),
        sa.Column("alert_id", sa.Integer, sa.ForeignKey("observability_alerts.id", ondelete="SET NULL")),
        sa.Column("security_finding_id", sa.Integer),
        sa.Column("integration_connector_id", sa.Integer),
        sa.Column("resolved_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("finding_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("severity", _FINDING_SEVERITIES), name="ck_observability_finding_severity"),
        sa.CheckConstraint(_in("status", _FINDING_STATUSES), name="ck_observability_finding_status"),
        sa.CheckConstraint(_in("source", _FINDING_SOURCES), name="ck_observability_finding_source"),
    )
    op.create_index("ix_observability_findings_status", "observability_reliability_findings", ["status"])

    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    op.create_table(
        "observability_events",
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
    op.create_index("ix_observability_events_entity", "observability_events", ["entity_type", "entity_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_observability_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'observability_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER observability_events_immutable BEFORE UPDATE OR DELETE ON observability_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_observability_event_mutation()"
    )

    # Widen the Automation JOB_TYPES CHECKs so Automation may run observability scans (D.22 reuse).
    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_NEW)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_NEW)

    # Seed the telemetry-source registry (references existing run-ledgers; copies no data).
    for code, name, stype, ref in _SOURCE_SEED:
        if bind.execute(sa.text("SELECT id FROM observability_telemetry_sources WHERE code=:c"),
                        {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO observability_telemetry_sources (code, name, source_type, reference, enabled) "
                "VALUES (:c, :n, :t, :r, true)"), {"c": code, "n": name, "t": stype, "r": ref})

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

    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_OLD)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_OLD)

    op.execute("DROP TRIGGER IF EXISTS observability_events_immutable ON observability_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_observability_event_mutation()")
    op.drop_table("observability_events")
    op.drop_table("observability_reliability_findings")
    op.drop_table("observability_reliability_incidents")
    op.drop_table("observability_runtime_snapshots")
    op.drop_table("observability_alerts")
    op.drop_table("observability_alert_suppressions")
    op.drop_table("observability_alert_rules")
    op.drop_table("observability_maintenance_windows")
    op.drop_table("observability_telemetry_metrics")
    op.drop_table("observability_telemetry_sources")
    op.drop_table("observability_diagnostic_results")
    op.drop_table("observability_diagnostic_checks")
    op.drop_table("observability_health_snapshots")
    op.drop_table("observability_health_checks")
    op.drop_table("observability_service_dependencies")
    op.drop_table("observability_services")
    op.drop_table("observability_deployment_references")
    op.drop_table("observability_environment_profiles")
