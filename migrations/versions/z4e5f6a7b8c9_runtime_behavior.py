"""Runtime Behavior registry (Phase D.30).

D.30 migrates application BEHAVIOR to consume the D.28 Runtime Configuration Engine (via
``RuntimeContext`` / the consumption API). The runtime engine remains the sole evaluator; the D.29
coordination layer remains the sole synchronization mechanism; Configuration remains the sole metadata
owner. The only persistence D.30 adds is a durable **behavioral-migration registry**
(``runtime_behaviors``) that catalogs which application behaviors have been migrated to the runtime
engine (for adoption tracking / analytics / the migration inventory).

Tables (1). Seeds the behavioral-migration catalog. Reuses the existing D.28 ``runtime.*``
capabilities (no new capabilities) and adds no automation job types. Additive and reversible. Single
Alembic head (down ``z2c3d4e5f6a7``).
"""
import sqlalchemy as sa
from alembic import op

revision = "z4e5f6a7b8c9"
down_revision = "z2c3d4e5f6a7"
branch_labels = None
depends_on = None

_BEHAVIOR_STATUSES = ("legacy", "migrated", "retired", "deterministic")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"

# (code, module, name, status, runtime_key, consumes_config, description)
_SEED = (
    ("automation.job_dispatch", "automation", "Automation job dispatch gating", "migrated",
     "automation.job", False, "Per-job-type runtime enablement in execute_dispatch."),
    ("analytics.executive_metrics", "analytics", "Executive metric gating", "migrated",
     "analytics.executive_metrics", False, "Runtime gate for executive analytics metrics (capability still required)."),
    ("microsoft365.sync", "microsoft365", "Microsoft 365 sync enablement", "migrated",
     "microsoft365.sync", False, "Runtime on/off for M365 mail/calendar/document sync behavior."),
    ("microsoft365.sharepoint_scope", "microsoft365", "SharePoint site scope", "migrated",
     "microsoft365.sharepoint_site_ids", True, "SharePoint site-id scope for document sync (config)."),
    ("benefits.detector_windows", "benefits", "Benefits detector day-windows", "migrated",
     "benefits.detector_windows", True, "New-hire/OE/renewal/grace day windows for benefits detectors (config)."),
    ("reporting.optional_modules", "reporting", "Reporting optional modules", "migrated",
     "reporting.module", False, "Runtime enablement of optional report definitions/modules."),
    ("notifications.channel_dispatch", "notifications", "Notification channel dispatch", "deterministic",
     None, False, "Channel enablement is data-driven via the F5.2 provider registry; the F5.5 dispatch "
     "implementation is a certified frozen module (no behavioral switch to migrate)."),
    ("advisor_workspace.sections", "advisor_workspace", "Advisor workspace sections", "legacy",
     "advisor_workspace.section", False, "Workspace sections are capability-gated; a runtime section gate is a future migration."),
    ("operations.workspace", "operations", "Operations workspace", "deterministic",
     None, False, "Deterministic/data-driven; no behavioral switch to migrate."),
    ("compliance.workflow", "compliance", "Compliance workflow", "deterministic",
     None, False, "Deterministic/data-driven (no workflow/automation); no behavioral switch."),
    ("document_platform.behavior", "document_platform", "Document platform behavior", "deterministic",
     None, False, "Deterministic/data-driven CRUD; no behavioral switch."),
)


def upgrade():
    bind = op.get_bind()
    op.create_table(
        "runtime_behaviors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("module", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="legacy"),
        sa.Column("runtime_key", sa.Text),
        sa.Column("consumes_config", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("default_behavior", sa.JSON),
        sa.Column("description", sa.Text),
        sa.Column("migrated_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("behavior_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _BEHAVIOR_STATUSES), name="ck_runtime_behavior_status"),
    )

    for code, module, name, status, runtime_key, consumes_config, desc in _SEED:
        if bind.execute(sa.text("SELECT id FROM runtime_behaviors WHERE code=:c"),
                        {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO runtime_behaviors "
                "(code, module, name, status, runtime_key, consumes_config, description, migrated_at) "
                "VALUES (:c, :m, :n, :s, :rk, :cc, :d, "
                + ("now()" if status == "migrated" else "NULL") + ")"),
                {"c": code, "m": module, "n": name, "s": status, "rk": runtime_key,
                 "cc": consumes_config, "d": desc})


def downgrade():
    op.drop_table("runtime_behaviors")
