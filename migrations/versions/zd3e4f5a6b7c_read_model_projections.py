"""Enterprise Read Models & Projection Engine (Phase D.36).

D.36 consumes the D.34/D.35 domain events (from the transactional outbox — the sole event bus and event
log) to build fast, query-optimized READ MODELS. It changes no business behavior: the domain services
remain the sole authoritative mutation layer, and the outbox remains authoritative. Read models exist
only for querying/dashboards/analytics/timelines/reporting/search/AI, contain no authoritative business
logic or state, and are **disposable** — deletable and rebuildable entirely from events.

Creates: ``projection_definitions`` (registry) + ``projection_state`` (runtime checkpoint/health) + 12
disposable read-model tables (``rm_*``). Seeds the 12 projection definitions + an initial (unbuilt)
state row per projection. Reuses the existing D.26 ``observability.*`` capabilities (no new
capabilities, no RBAC changes). Additive and reversible. Single Alembic head (down ``zc2d3e4f5a6b``).
"""
import json

import sqlalchemy as sa
from alembic import op

from app.database.projection_seed import PROJECTION_DEFINITIONS_SEED

revision = "zd3e4f5a6b7c"
down_revision = "zc2d3e4f5a6b"
branch_labels = None
depends_on = None

_DEF_STATUSES = ("active", "deprecated", "retired")
_HEALTH = ("unbuilt", "healthy", "lagging", "failed", "building")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _ts():
    return sa.Column("last_event_type", sa.Text), sa.Column("last_event_at", sa.DateTime(timezone=True)), \
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def _rm(name, key_col, *extra):
    op.create_table(
        name,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(key_col, sa.BigInteger, nullable=False, unique=True),
        *extra, *_ts())


def upgrade():
    bind = op.get_bind()
    op.create_table(
        "projection_definitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("projection_id", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("owner", sa.Text),
        sa.Column("read_table", sa.Text, nullable=False),
        sa.Column("subscribed_events", sa.JSON),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("rebuild_strategy", sa.Text, nullable=False, server_default="full"),
        sa.Column("depends_on", sa.JSON),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("description", sa.Text),
        sa.Column("deprecated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _DEF_STATUSES), name="ck_projection_definition_status"),
    )
    op.create_table(
        "projection_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("projection_id", sa.Text, nullable=False, unique=True),
        sa.Column("health", sa.Text, nullable=False, server_default="unbuilt"),
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_processed_event_id", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_processed_at", sa.DateTime(timezone=True)),
        sa.Column("events_processed", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("failed_events", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("rebuild_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("replay_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_rebuild_at", sa.DateTime(timezone=True)),
        sa.Column("last_rebuild_duration_ms", sa.Integer),
        sa.Column("last_replay_at", sa.DateTime(timezone=True)),
        sa.Column("last_replay_duration_ms", sa.Integer),
        sa.Column("last_validation_ok", sa.Text),
        sa.Column("rebuild_history", sa.JSON),
        sa.Column("last_error", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("health", _HEALTH), name="ck_projection_state_health"),
    )

    _rm("rm_people_summary", "person_id",
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("update_count", sa.Integer, server_default="0"),
        sa.Column("merge_count", sa.Integer, server_default="0"))
    _rm("rm_household_summary", "household_id",
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("membership_change_count", sa.Integer, server_default="0"))
    _rm("rm_opportunity_pipeline", "opportunity_id",
        sa.Column("pipeline_id", sa.BigInteger), sa.Column("stage_id", sa.BigInteger),
        sa.Column("status", sa.Text), sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)))
    _rm("rm_operational_tasks", "task_id",
        sa.Column("project_id", sa.BigInteger), sa.Column("status", sa.Text),
        sa.Column("priority", sa.Text), sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)))
    _rm("rm_projects", "project_id",
        sa.Column("category", sa.Text), sa.Column("status", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)))
    _rm("rm_compliance_queue", "review_id",
        sa.Column("status", sa.Text), sa.Column("governing_rule", sa.Text), sa.Column("decision", sa.Text),
        sa.Column("opened_at", sa.DateTime(timezone=True)), sa.Column("decided_at", sa.DateTime(timezone=True)))
    _rm("rm_tax_pipeline", "return_id",
        sa.Column("engagement_id", sa.BigInteger), sa.Column("tax_year", sa.Integer),
        sa.Column("status", sa.Text), sa.Column("filing_status", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)))
    _rm("rm_insurance_pipeline", "case_id",
        sa.Column("case_type", sa.Text), sa.Column("status", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)))
    _rm("rm_benefits_enrollment", "enrollment_id",
        sa.Column("plan_year_id", sa.BigInteger), sa.Column("coverage_tier", sa.Text),
        sa.Column("status", sa.Text), sa.Column("created_at", sa.DateTime(timezone=True)))
    _rm("rm_document_status", "document_id",
        sa.Column("classification", sa.Text), sa.Column("status", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)), sa.Column("archived_at", sa.DateTime(timezone=True)))
    _rm("rm_exception_dashboard", "exception_id",
        sa.Column("code", sa.Text), sa.Column("domain", sa.Text), sa.Column("category", sa.Text),
        sa.Column("severity", sa.Text), sa.Column("status", sa.Text),
        sa.Column("opened_at", sa.DateTime(timezone=True)), sa.Column("resolved_at", sa.DateTime(timezone=True)))
    op.create_table(
        "rm_activity_feed",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Text, nullable=False, unique=True),
        sa.Column("outbox_event_id", sa.BigInteger, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("category", sa.Text),
        sa.Column("subject_ref", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    for (pid, name, category, owner, read_table, events, version, deps, desc) in PROJECTION_DEFINITIONS_SEED:
        if bind.execute(sa.text("SELECT id FROM projection_definitions WHERE projection_id=:p"),
                        {"p": pid}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO projection_definitions "
                "(projection_id, name, category, owner, read_table, subscribed_events, schema_version, "
                " rebuild_strategy, depends_on, status, description) "
                "VALUES (:p, :n, :cat, :o, :rt, CAST(:ev AS json), :v, 'full', CAST(:dep AS json), "
                " 'active', :desc)"),
                {"p": pid, "n": name, "cat": category, "o": owner, "rt": read_table,
                 "ev": json.dumps(events), "v": version, "dep": json.dumps(deps), "desc": desc})
            bind.execute(sa.text(
                "INSERT INTO projection_state (projection_id, health, schema_version) "
                "VALUES (:p, 'unbuilt', :v)"), {"p": pid, "v": version})


def downgrade():
    for name in ("rm_people_summary", "rm_household_summary", "rm_opportunity_pipeline",
                 "rm_operational_tasks", "rm_projects", "rm_compliance_queue", "rm_tax_pipeline",
                 "rm_insurance_pipeline", "rm_benefits_enrollment", "rm_document_status",
                 "rm_exception_dashboard", "rm_activity_feed"):
        op.drop_table(name)
    op.drop_table("projection_state")
    op.drop_table("projection_definitions")
