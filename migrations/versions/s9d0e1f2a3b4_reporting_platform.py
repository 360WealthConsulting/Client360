"""Enterprise Reporting platform (Phase D.21).

Reporting is a new **composition layer** (not a source domain). It owns only reporting metadata —
report templates/definitions, dashboards and widgets, scorecards, KPI groups, saved views,
schedules, export profiles, report-run records, and an append-only audit ledger. It **never owns
business data and is never a source of truth**. KPI values are composed from the Analytics
read-model at render time (Reporting never recalculates KPIs; point-in-time capture reuses
``analytics_snapshots`` via the analytics service). Widgets reference Analytics by ``metric_key``
(string), not FK. Schedules reference Workflow / Communications for delivery metadata.

Tables (11): ``report_templates``, ``reporting_kpi_groups``, ``reporting_scorecards``,
``report_definitions``, ``reporting_dashboards``, ``reporting_widgets``, ``reporting_saved_views``,
``reporting_export_profiles``, ``report_schedules``, ``reports``, and ``reporting_events``
(APPEND-ONLY, trigger-blocked).

Seeds 5 ``reporting.*`` capabilities, 10 audience dashboards, and 4 export profiles. Additive and
reversible. Single Alembic head (down_revision ``r8c9d0e1f2a3`` — the D.20 head).
"""
import sqlalchemy as sa
from alembic import op

revision = "s9d0e1f2a3b4"
down_revision = "r8c9d0e1f2a3"
branch_labels = None
depends_on = None

_CATEGORIES = ("executive", "operations", "compliance", "advisor", "tax", "insurance", "marketing",
               "business_development", "client_service", "technology", "general")
_REPORT_TYPES = ("dashboard", "scorecard", "operational", "executive_summary", "kpi", "custom")
_REPORT_STATUSES = ("draft", "generating", "generated", "failed", "delivered", "archived")
_DASHBOARD_STATUSES = ("draft", "published", "archived")
_WIDGET_TYPES = ("metric", "chart", "scorecard", "table", "trend", "kpi_group", "text")
_VIZ_TYPES = ("card", "table", "chart", "leaderboard", "heatmap", "trendline", "gauge", "sparkline")
_EXPORT_FORMATS = ("pdf", "excel", "csv", "powerpoint")
_EXPORT_DELIVERY = ("download", "email", "microsoft365")
_FREQUENCIES = ("manual", "daily", "weekly", "monthly", "quarterly")
_SAVED_VIEW_TARGETS = ("dashboard", "report_definition")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


# (code, name, category, executive_only) — the 10 audience dashboards.
_DASHBOARD_SEED = (
    ("executive", "Executive Dashboard", "executive", True),
    ("operations", "Operations Dashboard", "operations", False),
    ("compliance", "Compliance Dashboard", "compliance", False),
    ("advisor", "Advisor Dashboard", "advisor", False),
    ("tax", "Tax Dashboard", "tax", False),
    ("insurance", "Insurance Dashboard", "insurance", False),
    ("marketing", "Marketing Dashboard", "marketing", False),
    ("business_development", "Business Development Dashboard", "business_development", False),
    ("client_service", "Client Service Dashboard", "client_service", False),
    ("technology", "Technology Dashboard", "technology", False),
)

_EXPORT_SEED = (
    ("pdf_download", "PDF (download)", "pdf", "download"),
    ("excel_download", "Excel (download)", "excel", "download"),
    ("csv_download", "CSV (download)", "csv", "download"),
    ("pptx_email", "PowerPoint (email)", "powerpoint", "email"),
)

_CAPS = (
    ("reporting.view", "View reports, dashboards, scorecards, and saved views.", False,
     ("administrator", "operations", "advisor", "compliance")),
    ("reporting.manage", "Create, update, generate, and schedule reports and dashboards.", False,
     ("administrator", "operations")),
    ("reporting.templates", "Manage report templates and export profiles.", False,
     ("administrator", "operations")),
    ("reporting.audit", "View reporting audit history.", True, ("administrator", "compliance")),
    ("reporting.admin", "Administer the reporting platform.", True, ("administrator",)),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "report_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("report_type", sa.Text, nullable=False, server_default="dashboard"),
        sa.Column("description", sa.Text),
        sa.Column("definition", sa.JSON),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("tags", sa.JSON),
        sa.Column("template_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_report_template_category"),
        sa.CheckConstraint(_in("report_type", _REPORT_TYPES), name="ck_report_template_type"),
    )

    op.create_table(
        "reporting_kpi_groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("metric_keys", sa.JSON),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "reporting_scorecards",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("executive_only", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("metric_keys", sa.JSON),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_reporting_scorecard_category"),
    )

    op.create_table(
        "report_definitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("report_type", sa.Text, nullable=False, server_default="dashboard"),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("template_id", sa.Integer, sa.ForeignKey("report_templates.id", ondelete="SET NULL")),
        sa.Column("kpi_group_id", sa.Integer, sa.ForeignKey("reporting_kpi_groups.id", ondelete="SET NULL")),
        sa.Column("definition", sa.JSON),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("executive_only", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("tags", sa.JSON),
        sa.Column("definition_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("report_type", _REPORT_TYPES), name="ck_report_definition_type"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_report_definition_category"),
    )
    op.create_index("ix_report_definitions_type", "report_definitions", ["report_type"])

    op.create_table(
        "reporting_dashboards",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("layout", sa.JSON),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("executive_only", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("tags", sa.JSON),
        sa.Column("dashboard_metadata", sa.JSON),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_reporting_dashboard_category"),
        sa.CheckConstraint(_in("status", _DASHBOARD_STATUSES), name="ck_reporting_dashboard_status"),
    )

    op.create_table(
        "reporting_widgets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dashboard_id", sa.Integer,
                  sa.ForeignKey("reporting_dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("widget_type", sa.Text, nullable=False, server_default="metric"),
        sa.Column("metric_key", sa.Text),
        sa.Column("kpi_group_id", sa.Integer, sa.ForeignKey("reporting_kpi_groups.id", ondelete="SET NULL")),
        sa.Column("viz_type", sa.Text, nullable=False, server_default="card"),
        sa.Column("dimension_key", sa.Text),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("config", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("widget_type", _WIDGET_TYPES), name="ck_reporting_widget_type"),
        sa.CheckConstraint(_in("viz_type", _VIZ_TYPES), name="ck_reporting_widget_viz"),
    )
    op.create_index("ix_reporting_widgets_dashboard", "reporting_widgets", ["dashboard_id"])

    op.create_table(
        "reporting_saved_views",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=False, server_default="dashboard"),
        sa.Column("target_id", sa.Integer, nullable=False),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("filters", sa.JSON),
        sa.Column("shared", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("target_type", _SAVED_VIEW_TARGETS), name="ck_reporting_saved_view_target"),
    )
    op.create_index("ix_reporting_saved_views_owner", "reporting_saved_views", ["owner_user_id"])

    op.create_table(
        "reporting_export_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("export_format", sa.Text, nullable=False, server_default="pdf"),
        sa.Column("delivery", sa.Text, nullable=False, server_default="download"),
        sa.Column("config", sa.JSON),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("export_format", _EXPORT_FORMATS), name="ck_reporting_export_format"),
        sa.CheckConstraint(_in("delivery", _EXPORT_DELIVERY), name="ck_reporting_export_delivery"),
    )

    op.create_table(
        "report_schedules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("report_definition_id", sa.Integer,
                  sa.ForeignKey("report_definitions.id", ondelete="SET NULL")),
        sa.Column("dashboard_id", sa.Integer, sa.ForeignKey("reporting_dashboards.id", ondelete="SET NULL")),
        sa.Column("export_profile_id", sa.Integer,
                  sa.ForeignKey("reporting_export_profiles.id", ondelete="SET NULL")),
        sa.Column("frequency", sa.Text, nullable=False, server_default="manual"),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("recipients", sa.JSON),
        sa.Column("conversation_id", sa.Integer,
                  sa.ForeignKey("communication_conversations.id", ondelete="SET NULL")),
        sa.Column("workflow_instance_id", sa.Integer,
                  sa.ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("frequency", _FREQUENCIES), name="ck_report_schedule_frequency"),
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("report_definition_id", sa.Integer,
                  sa.ForeignKey("report_definitions.id", ondelete="SET NULL")),
        sa.Column("dashboard_id", sa.Integer, sa.ForeignKey("reporting_dashboards.id", ondelete="SET NULL")),
        sa.Column("schedule_id", sa.Integer, sa.ForeignKey("report_schedules.id", ondelete="SET NULL")),
        sa.Column("export_profile_id", sa.Integer,
                  sa.ForeignKey("reporting_export_profiles.id", ondelete="SET NULL")),
        sa.Column("report_type", sa.Text, nullable=False, server_default="dashboard"),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("category", sa.Text, nullable=False, server_default="general"),
        sa.Column("period_key", sa.Text),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("generated_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("result_metadata", sa.JSON),
        sa.Column("snapshot_captured", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("tags", sa.JSON),
        sa.Column("report_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("report_type", _REPORT_TYPES), name="ck_report_type"),
        sa.CheckConstraint(_in("status", _REPORT_STATUSES), name="ck_report_status"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="ck_report_category"),
    )
    op.create_index("ix_reports_status", "reports", ["status"])
    op.create_index("ix_reports_definition", "reports", ["report_definition_id"])

    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    op.create_table(
        "reporting_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("payload", sa.JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_reporting_events_entity", "reporting_events", ["entity_type", "entity_id"])
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_reporting_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'reporting_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER reporting_events_immutable BEFORE UPDATE OR DELETE ON reporting_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_reporting_event_mutation()"
    )

    # Seed the 10 audience dashboards (idempotent by code).
    for code, name, category, exec_only in _DASHBOARD_SEED:
        exists = bind.execute(sa.text("SELECT id FROM reporting_dashboards WHERE code = :c"),
                              {"c": code}).scalar()
        if exists is None:
            bind.execute(sa.text(
                "INSERT INTO reporting_dashboards (code, name, category, status, is_system, "
                "executive_only, active) VALUES (:c, :n, :cat, 'published', true, :e, true)"),
                {"c": code, "n": name, "cat": category, "e": exec_only})

    # Seed export profiles (idempotent by code).
    for code, name, fmt, delivery in _EXPORT_SEED:
        exists = bind.execute(sa.text("SELECT id FROM reporting_export_profiles WHERE code = :c"),
                              {"c": code}).scalar()
        if exists is None:
            bind.execute(sa.text(
                "INSERT INTO reporting_export_profiles (code, name, export_format, delivery, active) "
                "VALUES (:c, :n, :f, :d, true)"), {"c": code, "n": name, "f": fmt, "d": delivery})

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

    op.execute("DROP TRIGGER IF EXISTS reporting_events_immutable ON reporting_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_reporting_event_mutation()")
    op.drop_table("reporting_events")
    op.drop_table("reports")
    op.drop_table("report_schedules")
    op.drop_table("reporting_export_profiles")
    op.drop_table("reporting_saved_views")
    op.drop_table("reporting_widgets")
    op.drop_table("reporting_dashboards")
    op.drop_table("report_definitions")
    op.drop_table("reporting_scorecards")
    op.drop_table("reporting_kpi_groups")
    op.drop_table("report_templates")
