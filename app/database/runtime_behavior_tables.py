"""Declared schema for the Phase D.30 Runtime Behavior registry.

Mirrors the live schema created by migration ``z4e5f6a7b8c9``. D.30 migrates application BEHAVIOR to
consume the D.28 Runtime Configuration Engine (via ``RuntimeContext`` / the consumption API). The only
persistence it adds is a **behavioral-migration registry** (``runtime_behaviors``): the catalog of the
behavioral switches that have been migrated to the runtime engine, their status, and the runtime key
they consume — so migration coverage / adoption percentage is durable and analyzable.

This owns no configuration metadata and performs no evaluation — the runtime engine remains the sole
evaluator and D.27 the sole metadata owner. The registry records *which* application behaviors consume
the runtime engine, not the configuration values themselves.
"""
from sqlalchemy import (
    JSON,
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
# legacy       — a migratable behavioral switch that does not yet consume the runtime engine
# migrated     — consumes the runtime engine (with a legacy default fallback preserving behavior)
# retired      — the legacy fallback path has been removed (runtime is authoritative)
# deterministic — data-driven / capability-driven; no behavioral switch to migrate (documented)
BEHAVIOR_STATUSES = ("legacy", "migrated", "retired", "deterministic")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_runtime_behavior_tables(metadata: MetaData):
    behaviors = Table(
        "runtime_behaviors", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("module", Text, nullable=False),
        Column("name", Text, nullable=False),
        Column("category", Text),
        Column("status", Text, nullable=False, server_default="legacy"),
        Column("runtime_key", Text),                 # the feature/config code the behavior consumes
        Column("consumes_config", Boolean, nullable=False, server_default="false"),
        # (D.31) the runtime engine is the authoritative source for this behavior (a D.27 definition
        # exists); the legacy default remains only as a documented compatibility shim.
        Column("authoritative", Boolean, nullable=False, server_default="false"),
        Column("compatibility_shim", Boolean, nullable=False, server_default="false"),
        Column("runtime_default", JSON),             # the seeded runtime default value/spec
        Column("default_behavior", JSON),            # the legacy default preserved on migration
        Column("description", Text),
        Column("migrated_at", DateTime(timezone=True)),
        Column("retired_at", DateTime(timezone=True)),
        Column("behavior_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", BEHAVIOR_STATUSES), name="ck_runtime_behavior_status"),
    )
    return {"runtime_behaviors": behaviors}
