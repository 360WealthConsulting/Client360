"""Enterprise Analytics, KPI warehouse, executive scorecards & firm intelligence (Phase D.15).

Analytics is a READ-MODEL: it computes KPIs deterministically by composing existing
principal-scoped domain reports and running bounded, scope-filtered COUNT/SUM aggregates. It
owns NO business data and is never a source of truth. The only things it persists are
analytics-specific CONFIG and prospective SNAPSHOTS:

- ``analytics_targets``   — executive targets/thresholds per metric/dimension/period.
- ``analytics_snapshots`` — prospective point-in-time metric captures for trends (no backfill —
                            that would fabricate history; snapshots accumulate going forward).
- ``analytics_dashboards`` / ``analytics_dashboard_widgets`` — custom dashboards + visualization
                            metadata (card/table/chart/leaderboard/gauge/sparkline/trendline/heatmap).

Four tables + five capabilities. Linear, reversible; capabilities seeded idempotently. No
source-domain table is touched.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "m3d4e5f6a7b8"
down_revision = "l2c3d4e5f6a7"
branch_labels = None
depends_on = None

_CAPS = (
    ("analytics.view", "View analytics dashboards and metrics (own book).", False,
     ("administrator", "advisor", "operations")),
    ("analytics.executive", "View firm-wide / executive analytics and revenue metrics.", True,
     ("administrator",)),
    ("analytics.export", "Export analytics data.", False, ("administrator", "operations")),
    ("analytics.manage_targets", "Create and edit metric targets and thresholds.", False,
     ("administrator",)),
    ("analytics.manage_dashboards", "Create and edit custom dashboards and capture snapshots.",
     False, ("administrator", "operations")),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "analytics_targets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("metric_key", sa.Text, nullable=False),
        sa.Column("dimension_key", sa.Text),          # e.g. "advisor:5"; NULL = firm
        sa.Column("period", sa.Text, nullable=False, server_default="all"),
        sa.Column("target_value", sa.Numeric(18, 2)),
        sa.Column("threshold_warning", sa.Numeric(18, 2)),
        sa.Column("threshold_critical", sa.Numeric(18, 2)),
        sa.Column("direction", sa.Text, nullable=False, server_default="higher_is_better"),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("direction IN ('higher_is_better','lower_is_better')",
                           name="ck_analytics_targets_direction"),
        sa.UniqueConstraint("metric_key", "dimension_key", "period", name="uq_analytics_target"),
    )

    op.create_table(
        "analytics_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("metric_key", sa.Text, nullable=False),
        sa.Column("dimension_key", sa.Text),
        sa.Column("period_key", sa.Text, nullable=False),   # e.g. "2026-07" or "2026-Q3"
        sa.Column("value", sa.Numeric(20, 4), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("captured_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.UniqueConstraint("metric_key", "dimension_key", "period_key", name="uq_analytics_snapshot"),
    )
    op.create_index("ix_analytics_snapshots_metric", "analytics_snapshots", ["metric_key", "period_key"])

    op.create_table(
        "analytics_dashboards",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("executive_only", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("layout", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "analytics_dashboard_widgets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dashboard_id", sa.Integer,
                  sa.ForeignKey("analytics_dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("metric_key", sa.Text),
        sa.Column("viz_type", sa.Text, nullable=False, server_default="card"),
        sa.Column("dimension_key", sa.Text),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "viz_type IN ('card','table','chart','leaderboard','heatmap','trendline','gauge','sparkline')",
            name="ck_analytics_widget_viz"),
    )
    op.create_index("ix_analytics_widgets_dashboard", "analytics_dashboard_widgets", ["dashboard_id"])

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
    op.drop_table("analytics_dashboard_widgets")
    op.drop_table("analytics_dashboards")
    op.drop_table("analytics_snapshots")
    op.drop_table("analytics_targets")
    bind = op.get_bind()
    for code, _d, _s, _roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
