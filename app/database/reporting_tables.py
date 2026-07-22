"""Declared schema for the Phase D.21 Enterprise Reporting platform.

Mirrors the live schema created by migration ``s9d0e1f2a3b4``. Reporting is a **composition layer**,
not a source domain: it owns only reporting **metadata** (report templates/definitions, dashboards
and widgets, scorecards, KPI groups, saved views, schedules, export profiles, report-run records,
and an append-only audit ledger). It **never owns business data and is never a source of truth**.
KPI **values** are composed from the Analytics read-model at render time — Reporting never
recalculates KPIs and never persists KPI values as truth (point-in-time capture reuses
``analytics_snapshots`` via the analytics service).

Widgets reference Analytics by ``metric_key`` (a string into the analytics ``METRICS`` registry),
not by FK — Analytics owns the metrics. Schedules reference Workflow / Communications (delivery
metadata). A report run may optionally carry a client anchor (``person_id``/``household_id``,
``ON DELETE SET NULL``) so its lifecycle event can reach the client timeline; firm-level reports
carry no anchor. ``reporting_events`` is the append-only audit ledger (polymorphic, trigger-blocked
BEFORE UPDATE OR DELETE).
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
REPORT_CATEGORIES = ("executive", "operations", "compliance", "advisor", "tax", "insurance",
                     "marketing", "business_development", "client_service", "technology", "general")
REPORT_TYPES = ("dashboard", "scorecard", "operational", "executive_summary", "kpi", "custom")
REPORT_STATUSES = ("draft", "generating", "generated", "failed", "delivered", "archived")
DASHBOARD_STATUSES = ("draft", "published", "archived")
WIDGET_TYPES = ("metric", "chart", "scorecard", "table", "trend", "kpi_group", "text")
# Aligns with analytics WIDGET_VIZ_TYPES.
VIZ_TYPES = ("card", "table", "chart", "leaderboard", "heatmap", "trendline", "gauge", "sparkline")
EXPORT_FORMATS = ("pdf", "excel", "csv", "powerpoint")
EXPORT_DELIVERY = ("download", "email", "microsoft365")
SCHEDULE_FREQUENCIES = ("manual", "daily", "weekly", "monthly", "quarterly")
SAVED_VIEW_TARGETS = ("dashboard", "report_definition")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_reporting_tables(metadata: MetaData):
    templates = Table(
        "report_templates", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("category", Text, nullable=False, server_default="general"),
        Column("report_type", Text, nullable=False, server_default="dashboard"),
        Column("description", Text),
        Column("definition", JSON),             # default metric keys / sections / layout
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("tags", JSON),
        Column("template_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("category", REPORT_CATEGORIES), name="ck_report_template_category"),
        CheckConstraint(_in("report_type", REPORT_TYPES), name="ck_report_template_type"),
    )
    kpi_groups = Table(
        "reporting_kpi_groups", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("metric_keys", JSON),            # ordered list of analytics metric keys
        Column("sort_order", Integer, nullable=False, server_default="0"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    scorecards = Table(
        "reporting_scorecards", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("category", Text, nullable=False, server_default="general"),
        Column("executive_only", Boolean, nullable=False, server_default="false"),
        Column("metric_keys", JSON),            # ordered list of analytics metric keys
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("category", REPORT_CATEGORIES), name="ck_reporting_scorecard_category"),
    )
    definitions = Table(
        "report_definitions", metadata,
        Column("id", Integer, primary_key=True),
        Column("name", Text, nullable=False),
        Column("report_type", Text, nullable=False, server_default="dashboard"),
        Column("category", Text, nullable=False, server_default="general"),
        Column("template_id", Integer, ForeignKey("report_templates.id", ondelete="SET NULL")),
        Column("kpi_group_id", Integer, ForeignKey("reporting_kpi_groups.id", ondelete="SET NULL")),
        Column("definition", JSON),             # metric keys / sections / filters (the "what")
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("is_system", Boolean, nullable=False, server_default="false"),
        Column("executive_only", Boolean, nullable=False, server_default="false"),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("tags", JSON),
        Column("definition_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("report_type", REPORT_TYPES), name="ck_report_definition_type"),
        CheckConstraint(_in("category", REPORT_CATEGORIES), name="ck_report_definition_category"),
    )
    dashboards = Table(
        "reporting_dashboards", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("category", Text, nullable=False, server_default="general"),
        Column("description", Text),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("layout", JSON),
        Column("is_system", Boolean, nullable=False, server_default="false"),
        Column("executive_only", Boolean, nullable=False, server_default="false"),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("tags", JSON),
        Column("dashboard_metadata", JSON),
        Column("published_at", DateTime(timezone=True)),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("category", REPORT_CATEGORIES), name="ck_reporting_dashboard_category"),
        CheckConstraint(_in("status", DASHBOARD_STATUSES), name="ck_reporting_dashboard_status"),
    )
    widgets = Table(
        "reporting_widgets", metadata,
        Column("id", Integer, primary_key=True),
        Column("dashboard_id", Integer,
               ForeignKey("reporting_dashboards.id", ondelete="CASCADE"), nullable=False),
        Column("title", Text, nullable=False),
        Column("widget_type", Text, nullable=False, server_default="metric"),
        Column("metric_key", Text),             # into the analytics METRICS registry
        Column("kpi_group_id", Integer, ForeignKey("reporting_kpi_groups.id", ondelete="SET NULL")),
        Column("viz_type", Text, nullable=False, server_default="card"),
        Column("dimension_key", Text),
        Column("sort_order", Integer, nullable=False, server_default="0"),
        Column("config", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("widget_type", WIDGET_TYPES), name="ck_reporting_widget_type"),
        CheckConstraint(_in("viz_type", VIZ_TYPES), name="ck_reporting_widget_viz"),
    )
    saved_views = Table(
        "reporting_saved_views", metadata,
        Column("id", Integer, primary_key=True),
        Column("name", Text, nullable=False),
        Column("target_type", Text, nullable=False, server_default="dashboard"),
        Column("target_id", Integer, nullable=False),   # polymorphic (dashboard / definition)
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("filters", JSON),                # the Report Filter set
        Column("shared", Boolean, nullable=False, server_default="false"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("target_type", SAVED_VIEW_TARGETS), name="ck_reporting_saved_view_target"),
    )
    export_profiles = Table(
        "reporting_export_profiles", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("export_format", Text, nullable=False, server_default="pdf"),
        Column("delivery", Text, nullable=False, server_default="download"),
        Column("config", JSON),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("export_format", EXPORT_FORMATS), name="ck_reporting_export_format"),
        CheckConstraint(_in("delivery", EXPORT_DELIVERY), name="ck_reporting_export_delivery"),
    )
    schedules = Table(
        "report_schedules", metadata,
        Column("id", Integer, primary_key=True),
        Column("name", Text, nullable=False),
        Column("report_definition_id", Integer,
               ForeignKey("report_definitions.id", ondelete="SET NULL")),
        Column("dashboard_id", Integer, ForeignKey("reporting_dashboards.id", ondelete="SET NULL")),
        Column("export_profile_id", Integer,
               ForeignKey("reporting_export_profiles.id", ondelete="SET NULL")),
        Column("frequency", Text, nullable=False, server_default="manual"),
        Column("next_run_at", DateTime(timezone=True)),
        Column("last_run_at", DateTime(timezone=True)),
        Column("recipients", JSON),
        # Delivery metadata references Communications; Workflow may create/own the schedule trigger.
        Column("conversation_id", Integer,
               ForeignKey("communication_conversations.id", ondelete="SET NULL")),
        Column("workflow_instance_id", Integer,
               ForeignKey("workflow_instances.id", ondelete="SET NULL")),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("frequency", SCHEDULE_FREQUENCIES), name="ck_report_schedule_frequency"),
    )
    reports = Table(
        "reports", metadata,
        Column("id", Integer, primary_key=True),
        Column("name", Text, nullable=False),
        Column("report_definition_id", Integer,
               ForeignKey("report_definitions.id", ondelete="SET NULL")),
        Column("dashboard_id", Integer, ForeignKey("reporting_dashboards.id", ondelete="SET NULL")),
        Column("schedule_id", Integer, ForeignKey("report_schedules.id", ondelete="SET NULL")),
        Column("export_profile_id", Integer,
               ForeignKey("reporting_export_profiles.id", ondelete="SET NULL")),
        Column("report_type", Text, nullable=False, server_default="dashboard"),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("category", Text, nullable=False, server_default="general"),
        Column("period_key", Text),
        # Optional client anchor (a client-specific report run); firm reports carry none.
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("generated_at", DateTime(timezone=True)),
        Column("generated_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("result_metadata", JSON),        # column/row summary — NOT KPI truth
        Column("snapshot_captured", Boolean, nullable=False, server_default="false"),
        Column("tags", JSON),
        Column("report_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("report_type", REPORT_TYPES), name="ck_report_type"),
        CheckConstraint(_in("status", REPORT_STATUSES), name="ck_report_status"),
        CheckConstraint(_in("category", REPORT_CATEGORIES), name="ck_report_category"),
    )
    # Append-only audit ledger (polymorphic; no FK so parent deletes never touch immutable rows).
    events = Table(
        "reporting_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # report | dashboard | definition | schedule
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "report_templates": templates,
        "reporting_kpi_groups": kpi_groups,
        "reporting_scorecards": scorecards,
        "report_definitions": definitions,
        "reporting_dashboards": dashboards,
        "reporting_widgets": widgets,
        "reporting_saved_views": saved_views,
        "reporting_export_profiles": export_profiles,
        "report_schedules": schedules,
        "reports": reports,
        "reporting_events": events,
    }
