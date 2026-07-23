"""Declared schema for the Phase D.33 Enterprise Workflow Orchestration Engine.

Mirrors the live schema created by migration ``za0b1c2d3e4f``. D.33 centralizes workflow ORCHESTRATION
(the coordination of multi-stage processes) behind a single declarative engine. The engine consumes the
D.28 ``RuntimeContext`` and the D.32 Runtime Policy Engine — it never evaluates runtime configuration
directly (the runtime engine remains the sole evaluator) and never makes business decisions itself (the
policy engine remains the sole decision engine). It coordinates existing services and never duplicates
domain behavior; the mature domain lifecycles (compliance approval, the workflow-template engine,
operations/scheduling/advisor state machines) remain authoritative and are registered ``in_domain``.

Three tables:
- ``orchestration_definitions`` — the discoverable registry of declarative workflow definitions
  (category, version, lifecycle status, owner, the stage/transition graph, policy + runtime references,
  dependency graph). Mirrors the in-code definitions; governance reconciles the two.
- ``orchestration_instances`` — running orchestration instances (deterministic state, current stage,
  the runtime snapshot the run is bound to, the client anchor).
- ``orchestration_events`` — the append-only event ledger (one row per lifecycle event) that makes an
  orchestration deterministically replayable (records the transition, the policy decision, the runtime
  snapshot).
"""
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)

# Declarative lifecycle status of a registered orchestration definition.
# active     — the engine drives this workflow (its call sites are coordinated through it)
# in_domain  — registered + governed, but the lifecycle stays authoritative in the owning domain by
#              documented constraint (regulatory approval / a mature certified state machine) — the
#              generic engine never drives it (a documented exception, mirroring D.32 in-domain policies)
# deprecated — superseded; retained for one release
# retired    — removed from the orchestration path
DEFINITION_STATUSES = ("active", "in_domain", "deprecated", "retired")

# The canonical, deterministic orchestration instance states.
INSTANCE_STATES = ("pending", "active", "waiting", "completed", "cancelled", "failed", "compensated")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_orchestration_tables(metadata: MetaData):
    definitions = Table(
        "orchestration_definitions", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("category", Text, nullable=False),
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("status", Text, nullable=False, server_default="active"),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("owner", Text),
        Column("initial_stage", Text),
        Column("stages", JSON),                 # [{"name","kind","entry_actions","exit_actions"}, …]
        Column("transitions", JSON),            # [{"from","action","to","policy"}, …]
        Column("completion_stages", JSON),      # terminal-success stages
        Column("policy_refs", JSON),            # policy codes the routing consumes
        Column("runtime_refs", JSON),           # runtime feature/config keys consumed via RuntimeContext
        Column("depends_on", JSON),             # other orchestration definition codes (dependency graph)
        Column("timeout_seconds", Integer),
        Column("retry_policy", JSON),
        Column("compensation", JSON),           # {stage: hook} compensation hooks
        Column("deprecated_at", DateTime(timezone=True)),
        Column("deprecated_reason", Text),
        Column("definition_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", DEFINITION_STATUSES), name="ck_orchestration_definition_status"),
    )

    instances = Table(
        "orchestration_instances", metadata,
        Column("id", Integer, primary_key=True),
        Column("definition_code", Text, nullable=False),
        Column("subject", Text),                # the coordinated entity (e.g. job_type, run id, review code)
        Column("status", Text, nullable=False, server_default="pending"),
        Column("current_stage", Text),
        Column("runtime_snapshot_id", Integer),  # the runtime snapshot the run is bound to (replay)
        Column("context", JSON),                # immutable launch inputs
        Column("person_id", Integer),
        Column("household_id", Integer),
        Column("idempotency_key", Text, unique=True),
        Column("launched_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("last_error", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("completed_at", DateTime(timezone=True)),
        CheckConstraint(_in("status", INSTANCE_STATES), name="ck_orchestration_instance_status"),
    )

    events = Table(
        "orchestration_events", metadata,
        Column("id", Integer, primary_key=True),
        Column("instance_id", Integer, ForeignKey("orchestration_instances.id", ondelete="CASCADE"),
               nullable=False),
        Column("seq", Integer, nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_stage", Text),
        Column("to_stage", Text),
        Column("action", Text),
        Column("policy_decision", JSON),        # the recorded policy decision (deterministic replay)
        Column("runtime_snapshot_id", Integer),
        Column("payload", JSON),
        Column("actor_user_id", Integer),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("instance_id", "seq", name="uq_orchestration_event_seq"),
    )
    return {"orchestration_definitions": definitions, "orchestration_instances": instances,
            "orchestration_events": events}
