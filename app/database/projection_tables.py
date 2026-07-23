"""Declared schema for the Phase D.36 Read Models & Projection Engine.

Mirrors the live schema created by migration ``zd3e4f5a6b7c``. D.36 consumes the D.34/D.35 domain events
(from the transactional outbox — the sole event bus and event log) to build fast, query-optimized READ
MODELS. Read models exist only for querying/dashboards/analytics/timelines/reporting/search/AI — they
contain NO authoritative business logic and NO authoritative state. **Read models are disposable**: they
may be truncated and rebuilt entirely from the events, deterministically. The write side (the domain
services + their tables/ledgers) remains the sole authoritative mutation layer.

Two kinds of table:
- **Registry / runtime metadata** — ``projection_definitions`` (the discoverable catalog: owner,
  subscribed events, schema version, rebuild strategy, dependencies, status) and ``projection_state``
  (the runtime checkpoint + health: last processed outbox event, lag, counters, rebuild/replay history).
- **Read-model tables** (12) — one per projection. Each row is derived purely from events (references,
  statuses, timestamps only — never PII/secrets). Every read table is safe to ``DELETE FROM`` and
  rebuild.
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    func,
)

DEFINITION_STATUSES = ("active", "deprecated", "retired")
PROJECTION_HEALTH = ("unbuilt", "healthy", "lagging", "failed", "building")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_projection_tables(metadata: MetaData):
    t = {}

    # --- registry + runtime state -------------------------------------------
    t["projection_definitions"] = Table(
        "projection_definitions", metadata,
        Column("id", Integer, primary_key=True),
        Column("projection_id", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("category", Text, nullable=False),
        Column("owner", Text),
        Column("read_table", Text, nullable=False),
        Column("subscribed_events", JSON),        # event types consumed ("*" = all)
        Column("schema_version", Integer, nullable=False, server_default="1"),
        Column("rebuild_strategy", Text, nullable=False, server_default="full"),
        Column("depends_on", JSON),
        Column("status", Text, nullable=False, server_default="active"),
        Column("description", Text),
        Column("deprecated_at", DateTime(timezone=True)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", DEFINITION_STATUSES), name="ck_projection_definition_status"),
    )
    t["projection_state"] = Table(
        "projection_state", metadata,
        Column("id", Integer, primary_key=True),
        Column("projection_id", Text, nullable=False, unique=True),
        Column("health", Text, nullable=False, server_default="unbuilt"),
        Column("schema_version", Integer, nullable=False, server_default="1"),
        Column("last_processed_event_id", BigInteger, nullable=False, server_default="0"),
        Column("last_processed_at", DateTime(timezone=True)),
        Column("events_processed", BigInteger, nullable=False, server_default="0"),
        Column("failed_events", BigInteger, nullable=False, server_default="0"),
        Column("rebuild_count", Integer, nullable=False, server_default="0"),
        Column("replay_count", Integer, nullable=False, server_default="0"),
        Column("last_rebuild_at", DateTime(timezone=True)),
        Column("last_rebuild_duration_ms", Integer),
        Column("last_replay_at", DateTime(timezone=True)),
        Column("last_replay_duration_ms", Integer),
        Column("last_validation_ok", Text),        # null / "ok" / "mismatch"
        Column("rebuild_history", JSON),            # bounded list of recent runs
        Column("last_error", Text),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("health", PROJECTION_HEALTH), name="ck_projection_state_health"),
    )

    # --- read-model tables (12) — disposable, event-derived, references only -
    def _rm(name, key_col, extra):
        cols = [Column("id", Integer, primary_key=True),
                Column(key_col, BigInteger, nullable=False, unique=True)]
        cols += extra
        cols += [Column("last_event_type", Text), Column("last_event_at", DateTime(timezone=True)),
                 Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now())]
        t[name] = Table(name, metadata, *cols)

    _rm("rm_people_summary", "person_id",
        [Column("created_at", DateTime(timezone=True)), Column("update_count", Integer, server_default="0"),
         Column("merge_count", Integer, server_default="0")])
    _rm("rm_household_summary", "household_id",
        [Column("created_at", DateTime(timezone=True)),
         Column("membership_change_count", Integer, server_default="0")])
    _rm("rm_opportunity_pipeline", "opportunity_id",
        [Column("pipeline_id", BigInteger), Column("stage_id", BigInteger), Column("status", Text),
         Column("created_at", DateTime(timezone=True)), Column("closed_at", DateTime(timezone=True))])
    _rm("rm_operational_tasks", "task_id",
        [Column("project_id", BigInteger), Column("status", Text), Column("priority", Text),
         Column("created_at", DateTime(timezone=True)), Column("completed_at", DateTime(timezone=True))])
    _rm("rm_projects", "project_id",
        [Column("category", Text), Column("status", Text),
         Column("created_at", DateTime(timezone=True))])
    _rm("rm_compliance_queue", "review_id",
        [Column("status", Text), Column("governing_rule", Text), Column("decision", Text),
         Column("opened_at", DateTime(timezone=True)), Column("decided_at", DateTime(timezone=True))])
    _rm("rm_tax_pipeline", "return_id",
        [Column("engagement_id", BigInteger), Column("tax_year", Integer), Column("status", Text),
         Column("filing_status", Text), Column("created_at", DateTime(timezone=True))])
    _rm("rm_insurance_pipeline", "case_id",
        [Column("case_type", Text), Column("status", Text),
         Column("created_at", DateTime(timezone=True))])
    _rm("rm_benefits_enrollment", "enrollment_id",
        [Column("plan_year_id", BigInteger), Column("coverage_tier", Text), Column("status", Text),
         Column("created_at", DateTime(timezone=True))])
    _rm("rm_document_status", "document_id",
        [Column("classification", Text), Column("status", Text),
         Column("created_at", DateTime(timezone=True)), Column("archived_at", DateTime(timezone=True))])
    _rm("rm_exception_dashboard", "exception_id",
        [Column("code", Text), Column("domain", Text), Column("category", Text), Column("severity", Text),
         Column("status", Text), Column("opened_at", DateTime(timezone=True)),
         Column("resolved_at", DateTime(timezone=True))])

    # Activity feed — an append-only denormalized feed of ALL domain events (one row per event).
    t["rm_activity_feed"] = Table(
        "rm_activity_feed", metadata,
        Column("id", Integer, primary_key=True),
        Column("event_id", Text, nullable=False, unique=True),
        Column("outbox_event_id", BigInteger, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("category", Text),
        Column("subject_ref", Text),
        Column("occurred_at", DateTime(timezone=True)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return t


# The read-model table each projection owns (also used by governance to detect a projection reading an
# authoritative table: a projection may only touch outbox_events + its own read table).
READ_MODEL_TABLES = (
    "rm_people_summary", "rm_household_summary", "rm_opportunity_pipeline", "rm_operational_tasks",
    "rm_projects", "rm_compliance_queue", "rm_tax_pipeline", "rm_insurance_pipeline",
    "rm_benefits_enrollment", "rm_document_status", "rm_exception_dashboard", "rm_activity_feed",
)
