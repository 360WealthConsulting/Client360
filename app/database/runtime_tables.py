"""Declared schema for the Phase D.28 Enterprise Runtime Configuration Engine.

Mirrors the live schema created by migration ``z0a1b2c3d4e5``. D.28 is the RUNTIME EVALUATION layer
that safely consumes the Phase D.27 Enterprise Configuration metadata. It owns runtime evaluation
only — **it never edits configuration metadata** (D.27 remains the sole owner/mutator). The only
tables D.28 owns are its own **immutable effective-configuration snapshots**
(``runtime_config_snapshots``) and an append-only lifecycle ledger (``runtime_events``); everything
else it evaluates is read from the D.27 ``configuration_*`` metadata through the engine.

``runtime_config_snapshots`` are immutable once written (trigger-blocked BEFORE UPDATE OR DELETE) so
a snapshot captured for startup / a refresh / the scheduler is a stable, comparable, auditable record
of the effective configuration + active features + edition/license at a point in time.
``runtime_events`` is the append-only audit ledger (trigger-blocked BEFORE UPDATE OR DELETE).
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
SNAPSHOT_SCOPES = ("startup", "manual", "refresh", "scheduler", "background", "request")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_runtime_tables(metadata: MetaData):
    # --- immutable effective-configuration snapshots -------------------------------------------
    snapshots = Table(
        "runtime_config_snapshots", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("snapshot_uid", Text, nullable=False, unique=True),
        Column("scope", Text, nullable=False, server_default="manual"),
        Column("version", Integer, nullable=False),           # monotonic snapshot version
        Column("config_hash", Text, nullable=False),          # sha256 of the canonical effective config
        Column("effective_config", JSON),                     # {item_code: {value, source}}
        Column("active_features", JSON),                       # {feature_code: {enabled, reason}}
        Column("edition_code", Text),
        Column("license_code", Text),
        Column("item_count", Integer, nullable=False, server_default="0"),
        Column("feature_count", Integer, nullable=False, server_default="0"),
        Column("source", Text),                               # free-form origin note
        Column("snapshot_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("scope", SNAPSHOT_SCOPES), name="ck_runtime_snapshot_scope"),
    )
    # --- append-only audit ledger (polymorphic; no FK so parent deletes never touch it) --------
    events = Table(
        "runtime_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("entity_type", Text, nullable=False),   # engine | snapshot | cache | rollout | feature
        Column("entity_id", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer),
        Column("payload", JSON),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "runtime_config_snapshots": snapshots,
        "runtime_events": events,
    }
