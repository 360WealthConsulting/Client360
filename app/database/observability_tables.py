"""Declared schema for the Phase D.26 Enterprise Observability platform.

Mirrors the live schema created by migration ``x8b9c0d1e2f3``. Enterprise Observability is a new
authoritative PLATFORM-OPERATIONS domain that owns **observability metadata only** — a service
inventory + dependency graph, health checks/snapshots, diagnostic checks/results, telemetry
sources/metrics, alert rules/alerts/suppressions, runtime snapshots, environment/deployment
references, maintenance windows, and reliability incidents/findings — plus an append-only audit
ledger (``observability_events``). It **owns no business records** and is **never a source of truth**
for operational or business entities.

It **references** Automation/Integration/Security/Reporting/Analytics/Workflow/Communications/
Timeline/Audit/System-Configuration and **reuses** the existing health endpoints (``/readiness``),
the scheduler snapshot (``scheduler_status``), the ``app/observability/logging.py`` logging config
and request-id correlation, the notification ledger (alerts reference it — no delivery), and the
audit hash-chain — it **never replaces** the runtime health/logging/exception-handling logic and adds
no external monitoring stack. ``observability_events`` is the append-only audit ledger
(trigger-blocked BEFORE UPDATE OR DELETE).
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)

# Deterministic controlled vocabularies (metadata only).
SERVICE_TYPES = ("application", "database", "scheduler", "integration", "external", "queue",
                 "cache", "other")
SERVICE_STATUSES = ("operational", "degraded", "down", "maintenance", "unknown")
CRITICALITIES = ("low", "medium", "high", "critical")
DEPENDENCY_TYPES = ("hard", "soft", "runtime")
HEALTH_CHECK_TYPES = ("liveness", "readiness", "dependency", "synthetic")
HEALTH_STATUSES = ("healthy", "degraded", "unhealthy", "unknown")
DIAGNOSTIC_CATEGORIES = ("database", "scheduler", "migration", "integration", "security",
                         "configuration", "other")
DIAGNOSTIC_STATUSES = ("pass", "warn", "fail", "error")
TELEMETRY_SOURCE_TYPES = ("automation", "outbox", "integration", "scheduler", "analytics",
                          "security", "custom")
METRIC_KINDS = ("gauge", "counter", "rate", "duration")
AGGREGATIONS = ("last", "sum", "avg", "max", "min")
ALERT_SEVERITIES = ("info", "warning", "critical")
ALERT_STATUSES = ("open", "acknowledged", "resolved", "suppressed")
MAINTENANCE_STATUSES = ("scheduled", "active", "completed", "cancelled")
ENVIRONMENTS = ("production", "staging", "development", "test")
INCIDENT_SEVERITIES = ("low", "medium", "high", "critical")
INCIDENT_STATUSES = ("open", "investigating", "mitigated", "resolved", "closed")
FINDING_STATUSES = ("open", "acknowledged", "remediated", "accepted", "false_positive")
FINDING_SOURCES = ("health_scan", "diagnostic", "telemetry", "alert", "manual", "security")
FINDING_SEVERITIES = ("info", "low", "medium", "high", "critical")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_observability_tables(metadata: MetaData):
    # --- environment profiles / deployment references (registries) -----------------------------
    environment_profiles = Table(
        "observability_environment_profiles", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("environment", Text, nullable=False, server_default="production"),
        Column("region", Text),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("profile_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("environment", ENVIRONMENTS), name="ck_observability_environment"),
    )
    deployment_references = Table(
        "observability_deployment_references", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("version", Text, nullable=False),
        Column("migration_head", Text),
        Column("environment_profile_id", Integer,
               ForeignKey("observability_environment_profiles.id", ondelete="SET NULL")),
        Column("released_at", DateTime(timezone=True)),
        Column("notes", Text),
        Column("deployment_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    # --- service inventory + dependency graph --------------------------------------------------
    services = Table(
        "observability_services", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("service_type", Text, nullable=False, server_default="application"),
        Column("status", Text, nullable=False, server_default="unknown"),
        Column("criticality", Text, nullable=False, server_default="medium"),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        # Optional reference to an existing domain object (e.g. an integration connector) — never owns.
        Column("reference_type", Text),      # integration_connector | scheduler | database | ...
        Column("reference_id", Integer),
        Column("description", Text),
        Column("last_status_at", DateTime(timezone=True)),
        Column("service_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("service_type", SERVICE_TYPES), name="ck_observability_service_type"),
        CheckConstraint(_in("status", SERVICE_STATUSES), name="ck_observability_service_status"),
        CheckConstraint(_in("criticality", CRITICALITIES), name="ck_observability_service_criticality"),
    )
    service_dependencies = Table(
        "observability_service_dependencies", metadata,
        Column("id", Integer, primary_key=True),
        Column("service_id", Integer,
               ForeignKey("observability_services.id", ondelete="CASCADE"), nullable=False),
        Column("depends_on_service_id", Integer,
               ForeignKey("observability_services.id", ondelete="CASCADE"), nullable=False),
        Column("dependency_type", Text, nullable=False, server_default="hard"),
        Column("description", Text),
        Column("dependency_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("dependency_type", DEPENDENCY_TYPES), name="ck_observability_dependency_type"),
        UniqueConstraint("service_id", "depends_on_service_id", name="uq_observability_dependency"),
    )
    # --- health checks + snapshots -------------------------------------------------------------
    health_checks = Table(
        "observability_health_checks", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("service_id", Integer, ForeignKey("observability_services.id", ondelete="SET NULL")),
        Column("check_type", Text, nullable=False, server_default="liveness"),
        Column("target_reference", Text),      # e.g. "/readiness", "scheduler", "connector:5"
        Column("interval_seconds", Integer),
        Column("enabled", Boolean, nullable=False, server_default="true"),
        Column("last_status", Text, nullable=False, server_default="unknown"),
        Column("last_checked_at", DateTime(timezone=True)),
        Column("check_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("check_type", HEALTH_CHECK_TYPES), name="ck_observability_health_check_type"),
        CheckConstraint(_in("last_status", HEALTH_STATUSES), name="ck_observability_health_check_status"),
    )
    health_snapshots = Table(
        "observability_health_snapshots", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("health_check_id", Integer,
               ForeignKey("observability_health_checks.id", ondelete="SET NULL")),
        Column("service_id", Integer, ForeignKey("observability_services.id", ondelete="SET NULL")),
        Column("status", Text, nullable=False, server_default="unknown"),
        Column("latency_ms", Integer),
        Column("detail", Text),
        Column("observed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("snapshot_metadata", JSON),
        CheckConstraint(_in("status", HEALTH_STATUSES), name="ck_observability_health_snapshot_status"),
    )
    # --- diagnostics ---------------------------------------------------------------------------
    diagnostic_checks = Table(
        "observability_diagnostic_checks", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("category", Text, nullable=False, server_default="other"),
        Column("target_reference", Text),
        Column("enabled", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("check_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("category", DIAGNOSTIC_CATEGORIES), name="ck_observability_diagnostic_category"),
    )
    diagnostic_results = Table(
        "observability_diagnostic_results", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("diagnostic_check_id", Integer,
               ForeignKey("observability_diagnostic_checks.id", ondelete="SET NULL")),
        Column("status", Text, nullable=False, server_default="pass"),
        Column("summary", Text),
        Column("detail", Text),                # sensitive diagnostic detail — server-side / gated
        Column("ran_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("result_metadata", JSON),
        CheckConstraint(_in("status", DIAGNOSTIC_STATUSES), name="ck_observability_diagnostic_status"),
    )
    # --- telemetry -----------------------------------------------------------------------------
    telemetry_sources = Table(
        "observability_telemetry_sources", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("source_type", Text, nullable=False, server_default="custom"),
        Column("reference", Text),             # e.g. table/ledger name it summarizes (no data copy)
        Column("enabled", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("source_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("source_type", TELEMETRY_SOURCE_TYPES), name="ck_observability_telemetry_source_type"),
    )
    telemetry_metrics = Table(
        "observability_telemetry_metrics", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("telemetry_source_id", Integer,
               ForeignKey("observability_telemetry_sources.id", ondelete="SET NULL")),
        Column("metric_kind", Text, nullable=False, server_default="gauge"),
        Column("unit", Text),
        Column("collection_interval_seconds", Integer),
        Column("warning_threshold", Float),
        Column("critical_threshold", Float),
        Column("aggregation", Text, nullable=False, server_default="last"),
        Column("analytics_metric_key", Text),  # reference to an Analytics Metric key (never owns it)
        Column("last_value", Float),
        Column("last_collected_at", DateTime(timezone=True)),
        Column("enabled", Boolean, nullable=False, server_default="true"),
        Column("metric_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("metric_kind", METRIC_KINDS), name="ck_observability_metric_kind"),
        CheckConstraint(_in("aggregation", AGGREGATIONS), name="ck_observability_metric_aggregation"),
    )
    # --- maintenance windows -------------------------------------------------------------------
    maintenance_windows = Table(
        "observability_maintenance_windows", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("title", Text, nullable=False),
        Column("status", Text, nullable=False, server_default="scheduled"),
        Column("service_id", Integer, ForeignKey("observability_services.id", ondelete="SET NULL")),
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
        Column("suppress_alerts", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("window_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", MAINTENANCE_STATUSES), name="ck_observability_maintenance_status"),
    )
    # --- alert rules / suppressions / alerts ---------------------------------------------------
    alert_rules = Table(
        "observability_alert_rules", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("telemetry_metric_id", Integer,
               ForeignKey("observability_telemetry_metrics.id", ondelete="SET NULL")),
        Column("service_id", Integer, ForeignKey("observability_services.id", ondelete="SET NULL")),
        Column("severity", Text, nullable=False, server_default="warning"),
        Column("condition", JSON),             # {"operator": ">", "threshold": 5}
        Column("routing", JSON),               # channel/notification references (no delivery here)
        Column("enabled", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("rule_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("severity", ALERT_SEVERITIES), name="ck_observability_alert_rule_severity"),
    )
    alert_suppressions = Table(
        "observability_alert_suppressions", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("alert_rule_id", Integer,
               ForeignKey("observability_alert_rules.id", ondelete="SET NULL")),
        Column("service_id", Integer, ForeignKey("observability_services.id", ondelete="SET NULL")),
        Column("maintenance_window_id", Integer,
               ForeignKey("observability_maintenance_windows.id", ondelete="SET NULL")),
        Column("reason", Text),
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("suppression_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    alerts = Table(
        "observability_alerts", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("alert_rule_id", Integer,
               ForeignKey("observability_alert_rules.id", ondelete="SET NULL")),
        Column("service_id", Integer, ForeignKey("observability_services.id", ondelete="SET NULL")),
        Column("severity", Text, nullable=False, server_default="warning"),
        Column("status", Text, nullable=False, server_default="open"),
        Column("title", Text, nullable=False),
        Column("detail", Text),
        Column("triggered_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("acknowledged_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("acknowledged_at", DateTime(timezone=True)),
        Column("resolved_at", DateTime(timezone=True)),
        Column("suppression_id", Integer,
               ForeignKey("observability_alert_suppressions.id", ondelete="SET NULL")),
        Column("notification_ref", Text),      # reference to a notification ledger row (no delivery)
        Column("alert_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("severity", ALERT_SEVERITIES), name="ck_observability_alert_severity"),
        CheckConstraint(_in("status", ALERT_STATUSES), name="ck_observability_alert_status"),
    )
    # --- runtime snapshots ---------------------------------------------------------------------
    runtime_snapshots = Table(
        "observability_runtime_snapshots", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("captured_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("database_ok", Boolean),
        Column("scheduler_running", Boolean),
        Column("scheduler_job_count", Integer),
        Column("migration_head", Text),
        Column("migration_in_sync", Boolean),
        Column("environment_profile_id", Integer,
               ForeignKey("observability_environment_profiles.id", ondelete="SET NULL")),
        Column("deployment_reference_id", Integer,
               ForeignKey("observability_deployment_references.id", ondelete="SET NULL")),
        Column("summary", Text),
        Column("snapshot_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    )
    # --- reliability incidents / findings ------------------------------------------------------
    reliability_incidents = Table(
        "observability_reliability_incidents", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("title", Text, nullable=False),
        Column("category", Text),
        Column("severity", Text, nullable=False, server_default="medium"),
        Column("status", Text, nullable=False, server_default="open"),
        Column("summary", Text),
        Column("service_id", Integer, ForeignKey("observability_services.id", ondelete="SET NULL")),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("detected_at", DateTime(timezone=True)),
        Column("mitigated_at", DateTime(timezone=True)),
        Column("resolved_at", DateTime(timezone=True)),
        # Optional client anchor (a client-scoped reliability incident) for guarded timeline publication.
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("incident_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("severity", INCIDENT_SEVERITIES), name="ck_observability_incident_severity"),
        CheckConstraint(_in("status", INCIDENT_STATUSES), name="ck_observability_incident_status"),
    )
    reliability_findings = Table(
        "observability_reliability_findings", metadata,
        Column("id", Integer, primary_key=True),
        Column("title", Text, nullable=False),
        Column("finding_type", Text, nullable=False, server_default="manual"),
        Column("severity", Text, nullable=False, server_default="medium"),
        Column("status", Text, nullable=False, server_default="open"),
        Column("source", Text, nullable=False, server_default="manual"),
        Column("detail", Text),
        Column("incident_id", Integer,
               ForeignKey("observability_reliability_incidents.id", ondelete="SET NULL")),
        Column("service_id", Integer, ForeignKey("observability_services.id", ondelete="SET NULL")),
        Column("alert_id", Integer, ForeignKey("observability_alerts.id", ondelete="SET NULL")),
        # References (never owns): a Security finding, an Integration connector.
        Column("security_finding_id", Integer),
        Column("integration_connector_id", Integer),
        Column("resolved_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("resolved_at", DateTime(timezone=True)),
        Column("finding_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("severity", FINDING_SEVERITIES), name="ck_observability_finding_severity"),
        CheckConstraint(_in("status", FINDING_STATUSES), name="ck_observability_finding_status"),
        CheckConstraint(_in("source", FINDING_SOURCES), name="ck_observability_finding_source"),
    )
    # --- append-only audit ledger (polymorphic; no FK so parent deletes never touch it) --------
    events = Table(
        "observability_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # service | alert | incident | snapshot ...
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "observability_environment_profiles": environment_profiles,
        "observability_deployment_references": deployment_references,
        "observability_services": services,
        "observability_service_dependencies": service_dependencies,
        "observability_health_checks": health_checks,
        "observability_health_snapshots": health_snapshots,
        "observability_diagnostic_checks": diagnostic_checks,
        "observability_diagnostic_results": diagnostic_results,
        "observability_telemetry_sources": telemetry_sources,
        "observability_telemetry_metrics": telemetry_metrics,
        "observability_maintenance_windows": maintenance_windows,
        "observability_alert_rules": alert_rules,
        "observability_alert_suppressions": alert_suppressions,
        "observability_alerts": alerts,
        "observability_runtime_snapshots": runtime_snapshots,
        "observability_reliability_incidents": reliability_incidents,
        "observability_reliability_findings": reliability_findings,
        "observability_events": events,
    }
