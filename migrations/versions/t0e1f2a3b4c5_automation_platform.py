"""Enterprise Automation platform (Phase D.22).

Automation is a new authoritative ORCHESTRATION domain that owns execution metadata only — jobs,
job templates, schedules, runs (execution history), queues, retry/failure policies,
execution/maintenance windows, workers, heartbeats, and execution locks. It **owns no business
records** and never duplicates business logic: a job dispatches to an EXISTING service
(reporting/workflow/analytics/communications/Microsoft 365) via the ``job_type`` map. It wraps the
existing in-process APScheduler (one new gated tick job) and mirrors the transactional outbox's
retry/backoff/dead-letter model — no distributed execution, no external queue broker, no Kubernetes.

Tables (12): ``automation_retry_policies``, ``automation_failure_policies``, ``automation_queues``,
``automation_windows``, ``automation_job_templates``, ``automation_jobs``, ``automation_schedules``,
``automation_workers``, ``automation_worker_heartbeats``, ``automation_execution_locks``,
``automation_runs``, and ``automation_events`` (APPEND-ONLY, trigger-blocked).

Seeds 5 ``automation.*`` capabilities, 1 default retry policy, 1 default failure policy, and 1
default queue. Additive and reversible. Single Alembic head (down_revision ``s9d0e1f2a3b4``).
"""
import sqlalchemy as sa
from alembic import op

revision = "t0e1f2a3b4c5"
down_revision = "s9d0e1f2a3b4"
branch_labels = None
depends_on = None

_JOB_TYPES = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
              "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
              "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
              "maintenance", "custom")
_JOB_CATEGORIES = ("reporting", "analytics", "workflow", "communications", "operations",
                   "microsoft365", "maintenance", "general")
_JOB_STATUSES = ("enabled", "disabled", "paused")
_SCHEDULE_TYPES = ("interval", "cron", "once", "manual")
_SCHEDULE_FREQUENCIES = ("manual", "hourly", "daily", "weekly", "monthly", "quarterly")
_RUN_STATUSES = ("pending", "queued", "running", "succeeded", "failed", "dead", "cancelled")
_RUN_TRIGGERS = ("schedule", "manual", "workflow", "api", "system")
_WORKER_TYPES = ("scheduler", "manual", "system")
_WORKER_STATUSES = ("active", "idle", "stopped")
_WINDOW_TYPES = ("execution", "maintenance")
_FAILURE_ACTIONS = ("retry", "dead_letter", "alert", "ignore")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


_CAPS = (
    ("automation.view", "View jobs, schedules, runs, and execution history.", False,
     ("administrator", "operations", "advisor", "compliance")),
    ("automation.manage", "Create and update jobs, schedules, policies, and queues.", False,
     ("administrator", "operations")),
    ("automation.execute", "Run/enqueue jobs and drive the automation runner.", False,
     ("administrator", "operations")),
    ("automation.audit", "View automation audit history.", True, ("administrator", "compliance")),
    ("automation.admin", "Administer the automation platform.", True, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "automation_retry_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("retry_delays", sa.JSON),
        sa.Column("backoff_base_seconds", sa.Integer, nullable=False, server_default="30"),
        sa.Column("description", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "automation_failure_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("on_failure", sa.Text, nullable=False, server_default="retry"),
        sa.Column("max_failures", sa.Integer, nullable=False, server_default="5"),
        sa.Column("alert_channel", sa.Text),
        sa.Column("config", sa.JSON),
        sa.Column("description", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("on_failure", _FAILURE_ACTIONS), name="ck_automation_failure_action"),
    )
    op.create_table(
        "automation_queues",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("max_concurrency", sa.Integer, nullable=False, server_default="1"),
        sa.Column("paused", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("queue_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "automation_windows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("window_type", sa.Text, nullable=False, server_default="execution"),
        sa.Column("days_of_week", sa.JSON),
        sa.Column("start_time", sa.Text),
        sa.Column("end_time", sa.Text),
        sa.Column("timezone", sa.Text, nullable=False, server_default="America/Chicago"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("window_type", _WINDOW_TYPES), name="ck_automation_window_type"),
    )
    op.create_table(
        "automation_job_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("job_type", sa.Text, nullable=False, server_default="maintenance"),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("description", sa.Text),
        sa.Column("default_config", sa.JSON),
        sa.Column("retry_policy_id", sa.Integer,
                  sa.ForeignKey("automation_retry_policies.id", ondelete="SET NULL")),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("tags", sa.JSON),
        sa.Column("template_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("job_type", _JOB_TYPES), name="ck_automation_template_job_type"),
        sa.CheckConstraint(_in("category", _JOB_CATEGORIES), name="ck_automation_template_category"),
    )
    op.create_table(
        "automation_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("job_type", sa.Text, nullable=False, server_default="maintenance"),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("description", sa.Text),
        sa.Column("config", sa.JSON),
        sa.Column("status", sa.Text, nullable=False, server_default="enabled"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("template_id", sa.Integer, sa.ForeignKey("automation_job_templates.id", ondelete="SET NULL")),
        sa.Column("retry_policy_id", sa.Integer,
                  sa.ForeignKey("automation_retry_policies.id", ondelete="SET NULL")),
        sa.Column("failure_policy_id", sa.Integer,
                  sa.ForeignKey("automation_failure_policies.id", ondelete="SET NULL")),
        sa.Column("queue_id", sa.Integer, sa.ForeignKey("automation_queues.id", ondelete="SET NULL")),
        sa.Column("window_id", sa.Integer, sa.ForeignKey("automation_windows.id", ondelete="SET NULL")),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("tags", sa.JSON),
        sa.Column("job_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("job_type", _JOB_TYPES), name="ck_automation_job_type"),
        sa.CheckConstraint(_in("category", _JOB_CATEGORIES), name="ck_automation_job_category"),
        sa.CheckConstraint(_in("status", _JOB_STATUSES), name="ck_automation_job_status"),
    )
    op.create_index("ix_automation_jobs_status", "automation_jobs", ["status"])

    op.create_table(
        "automation_schedules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("automation_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("schedule_type", sa.Text, nullable=False, server_default="interval"),
        sa.Column("frequency", sa.Text, nullable=False, server_default="manual"),
        sa.Column("cron_expression", sa.Text),
        sa.Column("interval_seconds", sa.Integer),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("timezone", sa.Text, nullable=False, server_default="America/Chicago"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("schedule_type", _SCHEDULE_TYPES), name="ck_automation_schedule_type"),
        sa.CheckConstraint(_in("frequency", _SCHEDULE_FREQUENCIES), name="ck_automation_schedule_freq"),
    )
    op.create_index("ix_automation_schedules_due", "automation_schedules", ["active", "next_run_at"])

    op.create_table(
        "automation_workers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("worker_type", sa.Text, nullable=False, server_default="scheduler"),
        sa.Column("status", sa.Text, nullable=False, server_default="idle"),
        sa.Column("hostname", sa.Text),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("worker_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("worker_type", _WORKER_TYPES), name="ck_automation_worker_type"),
        sa.CheckConstraint(_in("status", _WORKER_STATUSES), name="ck_automation_worker_status"),
    )
    op.create_table(
        "automation_worker_heartbeats",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("worker_id", sa.Integer, sa.ForeignKey("automation_workers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("active_runs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("detail", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_automation_heartbeats_worker", "automation_worker_heartbeats", ["worker_id"])

    op.create_table(
        "automation_execution_locks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("lock_key", sa.Text, nullable=False, unique=True),
        sa.Column("owner", sa.Text),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("run_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("automation_jobs.id", ondelete="SET NULL")),
        sa.Column("schedule_id", sa.Integer, sa.ForeignKey("automation_schedules.id", ondelete="SET NULL")),
        sa.Column("queue_id", sa.Integer, sa.ForeignKey("automation_queues.id", ondelete="SET NULL")),
        sa.Column("worker_id", sa.Integer, sa.ForeignKey("automation_workers.id", ondelete="SET NULL")),
        sa.Column("job_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("trigger_source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("triggered_by_user_id", sa.Integer),
        sa.Column("idempotency_key", sa.Text, unique=True),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("result", sa.JSON),
        sa.Column("last_error", sa.Text),
        sa.Column("run_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _RUN_STATUSES), name="ck_automation_run_status"),
        sa.CheckConstraint(_in("trigger_source", _RUN_TRIGGERS), name="ck_automation_run_trigger"),
    )
    op.create_index("ix_automation_runs_status", "automation_runs", ["status", "available_at"])
    op.create_index("ix_automation_runs_job", "automation_runs", ["job_id"])

    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    op.create_table(
        "automation_events",
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
    op.create_index("ix_automation_events_entity", "automation_events", ["entity_type", "entity_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_automation_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'automation_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER automation_events_immutable BEFORE UPDATE OR DELETE ON automation_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_automation_event_mutation()"
    )

    # Seed a default retry policy, failure policy, and queue (idempotent by code).
    if bind.execute(sa.text("SELECT id FROM automation_retry_policies WHERE code='default'")).scalar() is None:
        bind.execute(sa.text(
            "INSERT INTO automation_retry_policies (code, name, max_attempts, retry_delays, "
            "backoff_base_seconds) VALUES ('default', 'Default retry', 3, '[30,120,600]'::json, 30)"))
    if bind.execute(sa.text("SELECT id FROM automation_failure_policies WHERE code='default'")).scalar() is None:
        bind.execute(sa.text(
            "INSERT INTO automation_failure_policies (code, name, on_failure, max_failures) "
            "VALUES ('default', 'Default failure', 'dead_letter', 5)"))
    if bind.execute(sa.text("SELECT id FROM automation_queues WHERE code='default'")).scalar() is None:
        bind.execute(sa.text(
            "INSERT INTO automation_queues (code, name, max_concurrency) "
            "VALUES ('default', 'Default queue', 1)"))

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

    op.execute("DROP TRIGGER IF EXISTS automation_events_immutable ON automation_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_automation_event_mutation()")
    op.drop_table("automation_events")
    op.drop_table("automation_runs")
    op.drop_table("automation_execution_locks")
    op.drop_table("automation_worker_heartbeats")
    op.drop_table("automation_workers")
    op.drop_table("automation_schedules")
    op.drop_table("automation_jobs")
    op.drop_table("automation_job_templates")
    op.drop_table("automation_windows")
    op.drop_table("automation_queues")
    op.drop_table("automation_failure_policies")
    op.drop_table("automation_retry_policies")
