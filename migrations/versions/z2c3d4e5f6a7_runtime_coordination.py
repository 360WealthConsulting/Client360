"""Distributed Runtime Coordination (Phase D.29).

Makes the D.28 Runtime Configuration Engine cluster-safe. Reuses the transactional outbox as the sole
coordination bus (no second messaging system) and adds only its own coordination metadata: a worker
registry (``runtime_workers``) + heartbeat log (``runtime_worker_heartbeats``), a runtime version/
generation history with convergence tracking (``runtime_generations``), and an append-only
coordination lifecycle ledger (``runtime_coordination_events``). The runtime engine remains the sole
evaluator; Configuration remains the sole metadata owner; the outbox remains the sole coordination
mechanism.

Tables (4). Also **widens the Automation JOB_TYPES CHECK constraints** to add a
``runtime_coordination`` job type (so Automation may run a coordination sweep: expire stale workers +
converge). Reuses the existing D.28 ``runtime.*`` capabilities (no new capabilities). Additive and
reversible. Single Alembic head (down ``z0a1b2c3d4e5``).
"""
import sqlalchemy as sa
from alembic import op

revision = "z2c3d4e5f6a7"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None

_WORKER_STATUSES = ("active", "draining", "stale", "stopped")
_HEALTH_STATUSES = ("healthy", "degraded", "unreachable")
_GENERATION_TRIGGERS = ("manual", "scheduled", "startup", "metadata_change", "emergency")
_GENERATION_STATUSES = ("active", "superseded")
_PROPAGATION_STATUSES = ("pending", "converging", "converged")

# Automation JOB_TYPES (current 21) widened with runtime_coordination.
_JOB_TYPES_OLD = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
                  "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
                  "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
                  "governance_quality_scan", "governance_stale_scan", "governance_retention_review",
                  "integration_sync", "security_review", "observability_scan", "configuration_review",
                  "runtime_refresh", "maintenance", "custom")
_JOB_TYPES_NEW = _JOB_TYPES_OLD + ("runtime_coordination",)


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _set_job_type_check(table, constraint, values):
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, _in("job_type", values))


def upgrade():
    op.create_table(
        "runtime_workers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("worker_uid", sa.Text, nullable=False, unique=True),
        sa.Column("hostname", sa.Text),
        sa.Column("pid", sa.Integer),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("health_status", sa.Text, nullable=False, server_default="healthy"),
        sa.Column("runtime_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("snapshot_version", sa.Integer),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("worker_metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _WORKER_STATUSES), name="ck_runtime_worker_status"),
        sa.CheckConstraint(_in("health_status", _HEALTH_STATUSES), name="ck_runtime_worker_health"),
    )
    op.create_index("ix_runtime_workers_status", "runtime_workers", ["status"])

    op.create_table(
        "runtime_worker_heartbeats",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("worker_id", sa.Integer, sa.ForeignKey("runtime_workers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("runtime_version", sa.Integer),
        sa.Column("cache_version", sa.Integer),
        sa.Column("snapshot_version", sa.Integer),
        sa.Column("health_status", sa.Text),
        sa.Column("detail", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_runtime_heartbeats_worker", "runtime_worker_heartbeats", ["worker_id"])

    op.create_table(
        "runtime_generations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("generation_uid", sa.Text, nullable=False, unique=True),
        sa.Column("version", sa.Integer, nullable=False, unique=True),
        sa.Column("snapshot_uid", sa.Text),
        sa.Column("config_hash", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False, server_default="manual"),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("propagation_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("event_id", sa.Text),
        sa.Column("worker_count_at_activation", sa.Integer, nullable=False, server_default="0"),
        sa.Column("converged_worker_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("converged_at", sa.DateTime(timezone=True)),
        sa.Column("generation_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("trigger", _GENERATION_TRIGGERS), name="ck_runtime_generation_trigger"),
        sa.CheckConstraint(_in("status", _GENERATION_STATUSES), name="ck_runtime_generation_status"),
        sa.CheckConstraint(_in("propagation_status", _PROPAGATION_STATUSES),
                           name="ck_runtime_generation_propagation"),
    )
    op.create_index("ix_runtime_generations_version", "runtime_generations", ["version"])

    op.create_table(
        "runtime_coordination_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text),
        sa.Column("worker_uid", sa.Text),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("payload", sa.JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_runtime_coordination_events_entity", "runtime_coordination_events",
                    ["entity_type", "entity_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_runtime_coordination_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'runtime_coordination_events are append-only'; END; $$ LANGUAGE plpgsql")
    op.execute(
        "CREATE TRIGGER runtime_coordination_events_immutable BEFORE UPDATE OR DELETE "
        "ON runtime_coordination_events FOR EACH ROW EXECUTE FUNCTION "
        "prevent_runtime_coordination_event_mutation()")

    # Widen the Automation JOB_TYPES CHECKs so Automation may run a coordination sweep (D.22 reuse).
    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_NEW)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_NEW)


def downgrade():
    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_OLD)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_OLD)

    op.execute("DROP TRIGGER IF EXISTS runtime_coordination_events_immutable ON runtime_coordination_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_runtime_coordination_event_mutation()")
    op.drop_table("runtime_coordination_events")
    op.drop_table("runtime_generations")
    op.drop_table("runtime_worker_heartbeats")
    op.drop_table("runtime_workers")
