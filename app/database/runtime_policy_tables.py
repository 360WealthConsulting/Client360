"""Declared schema for the Phase D.32 Runtime Policy registry.

Mirrors the live schema created by migration ``z9b0c1d2e3f4``. D.32 centralizes application BUSINESS
DECISIONS (eligibility / routing / gating / visibility) behind a single Runtime Policy Engine that
consumes the D.28 ``RuntimeContext`` (it never evaluates configuration itself and never bypasses the
runtime engine — the engine remains the sole evaluator; D.27 remains the sole metadata owner). The
only persistence D.32 adds is a **policy registry** (``runtime_policies``): the durable, discoverable
catalog of the declarative business-decision policies — their category, version, lifecycle status,
owner, the runtime definition each consumes, the capabilities each references, and the dependency
graph between policies — so policy discovery / governance / coverage is durable and analyzable.

This owns no configuration metadata and performs no configuration evaluation. It records *which*
business decisions are centralized as policies, not the configuration values themselves.
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

# Deterministic controlled vocabulary (metadata only).
# active     — a live policy the Runtime Policy Engine evaluates (decision routed through the engine)
# in_domain  — registered + governed, but enforcement stays in the owning domain by documented
#              constraint (regulatory approval / a certified frozen module / a deterministic state
#              machine) — the generic engine never evaluates it (a documented exception, like a shim)
# deprecated — superseded by another policy; retained for one release for reference
# retired    — removed from the decision path
POLICY_STATUSES = ("active", "in_domain", "deprecated", "retired")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def define_runtime_policy_tables(metadata: MetaData):
    policies = Table(
        "runtime_policies", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("category", Text, nullable=False),       # the decision area (workflow, advisor_workspace, ...)
        Column("name", Text, nullable=False),
        Column("description", Text),
        Column("status", Text, nullable=False, server_default="active"),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("owner", Text),                            # the domain/team that owns the decision
        Column("consumes_feature", Text),                # the runtime feature code the policy consumes
        Column("consumes_config", Text),                 # the runtime config key the policy consumes
        Column("required_capabilities", JSON),           # RBAC capability codes the decision references
        Column("depends_on", JSON),                      # policy codes this policy composes (dependency graph)
        # a per-instance policy has an unbounded key space (e.g. automation.job.<type>,
        # reporting.module.<id>) so its runtime definitions cannot be fully pre-seeded (like a D.31 shim).
        Column("per_instance", Boolean, nullable=False, server_default="false"),
        # an authoritative policy REQUIRES its runtime definition to be present (governance flags a gap).
        Column("requires_definition", Boolean, nullable=False, server_default="false"),
        # enforcement stays in the owning domain by documented constraint (regulatory/frozen/deterministic).
        Column("in_domain", Boolean, nullable=False, server_default="false"),
        Column("default_decision", JSON),                # the behavior-preserving legacy default
        Column("deprecated_at", DateTime(timezone=True)),
        Column("deprecated_reason", Text),
        Column("policy_metadata", JSON),
        Column("created_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(_in("status", POLICY_STATUSES), name="ck_runtime_policy_status"),
    )
    return {"runtime_policies": policies}
