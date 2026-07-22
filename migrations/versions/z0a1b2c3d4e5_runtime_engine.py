"""Enterprise Runtime Configuration Engine (Phase D.28).

D.28 is the RUNTIME EVALUATION layer that safely consumes the Phase D.27 Enterprise Configuration
metadata. It owns runtime evaluation only — it **never edits configuration metadata** (D.27 remains
the sole owner/mutator). It reuses the existing startup lifecycle, middleware, config loaders, auth,
scheduler, observability, and analytics; it adds no new metadata domain. The only tables it owns are
its own **immutable effective-configuration snapshots** (``runtime_config_snapshots``) and an
append-only lifecycle ledger (``runtime_events``).

Tables (2). Also **widens the Automation JOB_TYPES CHECK constraints** to add a ``runtime_refresh``
job type (so Automation may trigger a safe cache/snapshot refresh). Seeds 5 runtime.* capabilities.
Additive and reversible. Single Alembic head (down ``y9c0d1e2f3a4``).
"""
import sqlalchemy as sa
from alembic import op

revision = "z0a1b2c3d4e5"
down_revision = "y9c0d1e2f3a4"
branch_labels = None
depends_on = None

_SNAPSHOT_SCOPES = ("startup", "manual", "refresh", "scheduler", "background", "request")

# Automation JOB_TYPES (current 20) widened with runtime_refresh.
_JOB_TYPES_OLD = ("run_report_schedule", "report_schedules_sweep", "capture_analytics_snapshots",
                  "launch_workflow", "workflow_sla_sweep", "dispatch_notifications", "dispatch_outbox",
                  "send_communication", "m365_mail_sync", "m365_calendar_sync", "m365_document_sync",
                  "governance_quality_scan", "governance_stale_scan", "governance_retention_review",
                  "integration_sync", "security_review", "observability_scan", "configuration_review",
                  "maintenance", "custom")
_JOB_TYPES_NEW = _JOB_TYPES_OLD + ("runtime_refresh",)


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _set_job_type_check(table, constraint, values):
    op.drop_constraint(constraint, table, type_="check")
    op.create_check_constraint(constraint, table, _in("job_type", values))


_CAPS = (
    ("runtime.view", "View the effective runtime configuration, active features, edition, snapshots, "
     "and cache state.", False, ("administrator", "operations", "compliance")),
    ("runtime.manage", "Manage runtime engine configuration (cache warm-up, snapshot scopes).", False,
     ("administrator", "operations")),
    ("runtime.execute", "Refresh the runtime engine, build snapshots, and warm/rebuild the cache.",
     False, ("administrator", "operations")),
    ("runtime.audit", "View runtime evaluation audit history and the safety-validation report.", True,
     ("administrator", "compliance")),
    ("runtime.admin", "Administer the runtime engine, including emergency configuration overrides.",
     True, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "runtime_config_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("snapshot_uid", sa.Text, nullable=False, unique=True),
        sa.Column("scope", sa.Text, nullable=False, server_default="manual"),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("config_hash", sa.Text, nullable=False),
        sa.Column("effective_config", sa.JSON),
        sa.Column("active_features", sa.JSON),
        sa.Column("edition_code", sa.Text),
        sa.Column("license_code", sa.Text),
        sa.Column("item_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("feature_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source", sa.Text),
        sa.Column("snapshot_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("scope", _SNAPSHOT_SCOPES), name="ck_runtime_snapshot_scope"),
    )
    op.create_index("ix_runtime_snapshots_version", "runtime_config_snapshots", ["version"])

    op.create_table(
        "runtime_events",
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
    op.create_index("ix_runtime_events_entity", "runtime_events", ["entity_type", "entity_id"])

    # Both runtime tables are append-only / immutable (trigger-blocked BEFORE UPDATE OR DELETE).
    for tbl in ("runtime_config_snapshots", "runtime_events"):
        fn = f"prevent_{tbl}_mutation"
        op.execute(
            f"CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{tbl} are append-only'; END; $$ LANGUAGE plpgsql")
        op.execute(
            f"CREATE TRIGGER {tbl}_immutable BEFORE UPDATE OR DELETE ON {tbl} "
            f"FOR EACH ROW EXECUTE FUNCTION {fn}()")

    # Widen the Automation JOB_TYPES CHECKs so Automation may run a safe runtime refresh (D.22 reuse).
    _set_job_type_check("automation_jobs", "ck_automation_job_type", _JOB_TYPES_NEW)
    _set_job_type_check("automation_job_templates", "ck_automation_template_job_type", _JOB_TYPES_NEW)

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

    for tbl in ("runtime_events", "runtime_config_snapshots"):
        op.execute(f"DROP TRIGGER IF EXISTS {tbl}_immutable ON {tbl}")
        op.execute(f"DROP FUNCTION IF EXISTS prevent_{tbl}_mutation()")
    op.drop_table("runtime_events")
    op.drop_table("runtime_config_snapshots")
