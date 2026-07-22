"""Declared schema for the Phase D.29 Distributed Runtime Coordination layer.

Mirrors the live schema created by migration ``z2c3d4e5f6a7``. D.29 makes the D.28 Runtime
Configuration Engine cluster-safe. It reuses the transactional outbox as the sole coordination bus
(no second messaging system) and adds only its own coordination metadata: a worker registry
(``runtime_workers``) with a heartbeat log (``runtime_worker_heartbeats``), a runtime version/
generation history with convergence tracking (``runtime_generations``), and an append-only
coordination lifecycle ledger (``runtime_coordination_events``).

The runtime engine remains the sole evaluator; Configuration remains the sole metadata owner. This
layer owns no configuration metadata and performs no evaluation — it coordinates *which runtime
version* each worker has converged to, keyed off the persisted, immutable
``runtime_config_snapshots`` (the single source of truth for the effective configuration).
``runtime_coordination_events`` is trigger-blocked (append-only).
"""
from sqlalchemy import (
    JSON,
    BigInteger,
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
WORKER_STATUSES = ("active", "draining", "stale", "stopped")
HEALTH_STATUSES = ("healthy", "degraded", "unreachable")
GENERATION_TRIGGERS = ("manual", "scheduled", "startup", "metadata_change", "emergency")
GENERATION_STATUSES = ("active", "superseded")
PROPAGATION_STATUSES = ("pending", "converging", "converged")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_runtime_coordination_tables(metadata: MetaData):
    # --- worker registry ------------------------------------------------------------------------
    workers = Table(
        "runtime_workers", metadata,
        Column("id", Integer, primary_key=True),
        Column("worker_uid", Text, nullable=False, unique=True),
        Column("hostname", Text),
        Column("pid", Integer),
        Column("status", Text, nullable=False, server_default="active"),
        Column("health_status", Text, nullable=False, server_default="healthy"),
        Column("runtime_version", Integer, nullable=False, server_default="0"),  # generation converged to
        Column("cache_version", Integer, nullable=False, server_default="0"),    # in-process cache version
        Column("snapshot_version", Integer),
        Column("last_heartbeat_at", DateTime(timezone=True)),
        Column("registered_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("worker_metadata", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", WORKER_STATUSES), name="ck_runtime_worker_status"),
        CheckConstraint(_in("health_status", HEALTH_STATUSES), name="ck_runtime_worker_health"),
    )
    heartbeats = Table(
        "runtime_worker_heartbeats", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("worker_id", Integer, ForeignKey("runtime_workers.id", ondelete="CASCADE"), nullable=False),
        Column("heartbeat_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("runtime_version", Integer),
        Column("cache_version", Integer),
        Column("snapshot_version", Integer),
        Column("health_status", Text),
        Column("detail", JSON),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    # --- runtime generations (version history + convergence) ------------------------------------
    generations = Table(
        "runtime_generations", metadata,
        Column("id", Integer, primary_key=True),
        Column("generation_uid", Text, nullable=False, unique=True),
        Column("version", Integer, nullable=False, unique=True),   # monotonic; ties to snapshot version
        Column("snapshot_uid", Text),                              # ref runtime_config_snapshots.snapshot_uid
        Column("config_hash", Text, nullable=False),
        Column("trigger", Text, nullable=False, server_default="manual"),
        Column("status", Text, nullable=False, server_default="active"),
        Column("propagation_status", Text, nullable=False, server_default="pending"),
        Column("event_id", Text),                                  # originating outbox event id (dedupe/replay)
        Column("worker_count_at_activation", Integer, nullable=False, server_default="0"),
        Column("converged_worker_count", Integer, nullable=False, server_default="0"),
        Column("activated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("converged_at", DateTime(timezone=True)),
        Column("generation_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("trigger", GENERATION_TRIGGERS), name="ck_runtime_generation_trigger"),
        CheckConstraint(_in("status", GENERATION_STATUSES), name="ck_runtime_generation_status"),
        CheckConstraint(_in("propagation_status", PROPAGATION_STATUSES),
                        name="ck_runtime_generation_propagation"),
    )
    # --- append-only coordination lifecycle ledger ---------------------------------------------
    events = Table(
        "runtime_coordination_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # cluster | worker | generation | refresh
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("worker_uid", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "runtime_workers": workers,
        "runtime_worker_heartbeats": heartbeats,
        "runtime_generations": generations,
        "runtime_coordination_events": events,
    }
