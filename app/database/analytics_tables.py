"""Declared schema for the Phase D.15 Analytics domain.

Mirrors the live schema created by migration ``m3d4e5f6a7b8``. Analytics is a read-model — it
owns no business data; these tables hold only analytics CONFIG (targets/thresholds, dashboards,
widgets) and prospective metric SNAPSHOTS. CHECK constraints live in the migration.
"""
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
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
from sqlalchemy.dialects.postgresql import JSONB

WIDGET_VIZ_TYPES = ("card", "table", "chart", "leaderboard", "heatmap", "trendline",
                    "gauge", "sparkline")


def define_analytics_tables(metadata: MetaData):
    targets = Table(
        "analytics_targets", metadata,
        Column("id", Integer, primary_key=True),
        Column("metric_key", Text, nullable=False),
        Column("dimension_key", Text),
        Column("period", Text, nullable=False, server_default="all"),
        Column("target_value", Numeric(18, 2)),
        Column("threshold_warning", Numeric(18, 2)),
        Column("threshold_critical", Numeric(18, 2)),
        Column("direction", Text, nullable=False, server_default="higher_is_better"),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("notes", Text, nullable=False, server_default=""),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("updated_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("direction IN ('higher_is_better','lower_is_better')",
                        name="ck_analytics_targets_direction"),
        UniqueConstraint("metric_key", "dimension_key", "period", name="uq_analytics_target"),
    )
    snapshots = Table(
        "analytics_snapshots", metadata,
        Column("id", Integer, primary_key=True),
        Column("metric_key", Text, nullable=False),
        Column("dimension_key", Text),
        Column("period_key", Text, nullable=False),
        Column("value", Numeric(20, 4), nullable=False),
        Column("captured_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("captured_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        UniqueConstraint("metric_key", "dimension_key", "period_key", name="uq_analytics_snapshot"),
    )
    dashboards = Table(
        "analytics_dashboards", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("is_system", Boolean, nullable=False, server_default="false"),
        Column("executive_only", Boolean, nullable=False, server_default="false"),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("layout", JSONB, nullable=False, server_default="{}"),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("updated_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    widgets = Table(
        "analytics_dashboard_widgets", metadata,
        Column("id", Integer, primary_key=True),
        Column("dashboard_id", Integer, ForeignKey("analytics_dashboards.id", ondelete="CASCADE"),
               nullable=False),
        Column("title", Text, nullable=False),
        Column("metric_key", Text),
        Column("viz_type", Text, nullable=False, server_default="card"),
        Column("dimension_key", Text),
        Column("sort_order", Integer, nullable=False, server_default="0"),
        Column("config", JSONB, nullable=False, server_default="{}"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(
            "viz_type IN ('card','table','chart','leaderboard','heatmap','trendline','gauge','sparkline')",
            name="ck_analytics_widget_viz"),
    )
    return {"analytics_targets": targets, "analytics_snapshots": snapshots,
            "analytics_dashboards": dashboards, "analytics_dashboard_widgets": widgets}
