"""Declared schema for the Phase D.22 Enterprise Automation platform.

Mirrors the live schema created by migration ``t0e1f2a3b4c5``. Automation is a new authoritative
ORCHESTRATION domain that owns **execution metadata only** — jobs, job templates, schedules, runs
(execution history), queues, retry/failure policies, execution/maintenance windows, workers,
heartbeats, and execution locks. It **owns no business records** and never duplicates business
logic: a job dispatches to an EXISTING service (reporting/workflow/analytics/communications/M365)
via the ``job_type`` → handler map. It wraps the existing in-process APScheduler and mirrors the
transactional outbox's retry/backoff/dead-letter model — **no distributed execution, no external
queue broker, no Kubernetes**.

A run may carry an optional client anchor (``person_id``/``household_id``, ``ON DELETE SET NULL``)
so its lifecycle event can reach the client timeline; firm-level jobs carry none and record only to
the append-only ``automation_events`` ledger (trigger-blocked BEFORE UPDATE OR DELETE).
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    func,
)

# Deterministic controlled vocabularies (metadata only).
JOB_TYPES = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
             "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
             "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
             "maintenance", "custom",
             # Phase D.23 — Data Governance jobs (dispatch to the governance services).
             "governance_quality_scan", "governance_stale_scan", "governance_retention_review",
             # Phase D.24 — Enterprise Integration (Automation executes scheduled synchronization).
             "integration_sync",
             # Phase D.25 — Enterprise Security (Automation runs rotation/certificate/policy reviews).
             "security_review",
             # Phase D.26 — Enterprise Observability (health/diagnostic scans, telemetry, alert eval).
             "observability_scan")
JOB_CATEGORIES = ("reporting", "analytics", "workflow", "communications", "operations",
                  "microsoft365", "maintenance", "governance", "general")
JOB_STATUSES = ("enabled", "disabled", "paused")
SCHEDULE_TYPES = ("interval", "cron", "once", "manual")
SCHEDULE_FREQUENCIES = ("manual", "hourly", "daily", "weekly", "monthly", "quarterly")
RUN_STATUSES = ("pending", "queued", "running", "succeeded", "failed", "dead", "cancelled")
RUN_TRIGGERS = ("schedule", "manual", "workflow", "api", "system")
WORKER_TYPES = ("scheduler", "manual", "system")
WORKER_STATUSES = ("active", "idle", "stopped")
WINDOW_TYPES = ("execution", "maintenance")
FAILURE_ACTIONS = ("retry", "dead_letter", "alert", "ignore")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_automation_tables(metadata: MetaData):
    retry_policies = Table(
        "automation_retry_policies", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("max_attempts", Integer, nullable=False, server_default="3"),
        Column("retry_delays", JSON),           # list of seconds, mirrors notification RetryPolicy
        Column("backoff_base_seconds", Integer, nullable=False, server_default="30"),
        Column("description", Text),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    failure_policies = Table(
        "automation_failure_policies", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("on_failure", Text, nullable=False, server_default="retry"),
        Column("max_failures", Integer, nullable=False, server_default="5"),
        Column("alert_channel", Text),
        Column("config", JSON),
        Column("description", Text),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("on_failure", FAILURE_ACTIONS), name="ck_automation_failure_action"),
    )
    queues = Table(
        "automation_queues", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("max_concurrency", Integer, nullable=False, server_default="1"),
        Column("paused", Boolean, nullable=False, server_default="false"),
        Column("queue_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    windows = Table(
        "automation_windows", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("window_type", Text, nullable=False, server_default="execution"),
        Column("days_of_week", JSON),           # e.g. [0..6]
        Column("start_time", Text),             # "HH:MM"
        Column("end_time", Text),
        Column("timezone", Text, nullable=False, server_default="America/Chicago"),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("description", Text),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("window_type", WINDOW_TYPES), name="ck_automation_window_type"),
    )
    templates = Table(
        "automation_job_templates", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("job_type", Text, nullable=False, server_default="maintenance"),
        Column("category", Text, nullable=False, server_default="general"),
        Column("description", Text),
        Column("default_config", JSON),
        Column("retry_policy_id", Integer,
               ForeignKey("automation_retry_policies.id", ondelete="SET NULL")),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("tags", JSON),
        Column("template_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("job_type", JOB_TYPES), name="ck_automation_template_job_type"),
        CheckConstraint(_in("category", JOB_CATEGORIES), name="ck_automation_template_category"),
    )
    jobs = Table(
        "automation_jobs", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("job_type", Text, nullable=False, server_default="maintenance"),
        Column("category", Text, nullable=False, server_default="general"),
        Column("description", Text),
        Column("config", JSON),                 # dispatch parameters
        Column("status", Text, nullable=False, server_default="enabled"),
        Column("priority", Integer, nullable=False, server_default="100"),
        Column("template_id", Integer, ForeignKey("automation_job_templates.id", ondelete="SET NULL")),
        Column("retry_policy_id", Integer,
               ForeignKey("automation_retry_policies.id", ondelete="SET NULL")),
        Column("failure_policy_id", Integer,
               ForeignKey("automation_failure_policies.id", ondelete="SET NULL")),
        Column("queue_id", Integer, ForeignKey("automation_queues.id", ondelete="SET NULL")),
        Column("window_id", Integer, ForeignKey("automation_windows.id", ondelete="SET NULL")),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("tags", JSON),
        Column("job_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("job_type", JOB_TYPES), name="ck_automation_job_type"),
        CheckConstraint(_in("category", JOB_CATEGORIES), name="ck_automation_job_category"),
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_automation_job_status"),
    )
    schedules = Table(
        "automation_schedules", metadata,
        Column("id", Integer, primary_key=True),
        Column("job_id", Integer, ForeignKey("automation_jobs.id", ondelete="CASCADE"),
               nullable=False),
        Column("name", Text, nullable=False),
        Column("schedule_type", Text, nullable=False, server_default="interval"),
        Column("frequency", Text, nullable=False, server_default="manual"),
        Column("cron_expression", Text),
        Column("interval_seconds", Integer),
        Column("next_run_at", DateTime(timezone=True)),
        Column("last_run_at", DateTime(timezone=True)),
        Column("timezone", Text, nullable=False, server_default="America/Chicago"),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("schedule_type", SCHEDULE_TYPES), name="ck_automation_schedule_type"),
        CheckConstraint(_in("frequency", SCHEDULE_FREQUENCIES), name="ck_automation_schedule_freq"),
    )
    workers = Table(
        "automation_workers", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("worker_type", Text, nullable=False, server_default="scheduler"),
        Column("status", Text, nullable=False, server_default="idle"),
        Column("hostname", Text),
        Column("last_heartbeat_at", DateTime(timezone=True)),
        Column("started_at", DateTime(timezone=True)),
        Column("worker_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("worker_type", WORKER_TYPES), name="ck_automation_worker_type"),
        CheckConstraint(_in("status", WORKER_STATUSES), name="ck_automation_worker_status"),
    )
    heartbeats = Table(
        "automation_worker_heartbeats", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("worker_id", Integer, ForeignKey("automation_workers.id", ondelete="CASCADE"),
               nullable=False),
        Column("heartbeat_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("active_runs", Integer, nullable=False, server_default="0"),
        Column("detail", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    locks = Table(
        "automation_execution_locks", metadata,
        Column("id", Integer, primary_key=True),
        Column("lock_key", Text, nullable=False, unique=True),
        Column("owner", Text),                  # worker code
        Column("acquired_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("expires_at", DateTime(timezone=True)),
        Column("run_id", Integer),              # plain reference (a run may not yet exist)
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    runs = Table(
        "automation_runs", metadata,
        Column("id", Integer, primary_key=True),
        Column("job_id", Integer, ForeignKey("automation_jobs.id", ondelete="SET NULL")),
        Column("schedule_id", Integer, ForeignKey("automation_schedules.id", ondelete="SET NULL")),
        Column("queue_id", Integer, ForeignKey("automation_queues.id", ondelete="SET NULL")),
        Column("worker_id", Integer, ForeignKey("automation_workers.id", ondelete="SET NULL")),
        Column("job_type", Text, nullable=False),
        Column("status", Text, nullable=False, server_default="pending"),
        Column("attempts", Integer, nullable=False, server_default="0"),
        Column("max_attempts", Integer, nullable=False, server_default="1"),
        Column("available_at", DateTime(timezone=True)),   # retry backoff gate
        Column("trigger_source", Text, nullable=False, server_default="manual"),
        Column("triggered_by_user_id", Integer),           # plain (may be a system run)
        Column("idempotency_key", Text, unique=True),
        # Optional client anchor (a client-specific job run); firm jobs carry none.
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("started_at", DateTime(timezone=True)),
        Column("finished_at", DateTime(timezone=True)),
        Column("duration_ms", Integer),
        Column("result", JSON),                 # dispatch summary — NOT business truth
        Column("last_error", Text),
        Column("run_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", RUN_STATUSES), name="ck_automation_run_status"),
        CheckConstraint(_in("trigger_source", RUN_TRIGGERS), name="ck_automation_run_trigger"),
    )
    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    events = Table(
        "automation_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # job | schedule | run | worker
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "automation_retry_policies": retry_policies,
        "automation_failure_policies": failure_policies,
        "automation_queues": queues,
        "automation_windows": windows,
        "automation_job_templates": templates,
        "automation_jobs": jobs,
        "automation_schedules": schedules,
        "automation_workers": workers,
        "automation_worker_heartbeats": heartbeats,
        "automation_execution_locks": locks,
        "automation_runs": runs,
        "automation_events": events,
    }
