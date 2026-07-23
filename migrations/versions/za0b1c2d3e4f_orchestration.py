"""Enterprise Workflow Orchestration Engine, State Management & Policy-Driven Process Execution (D.33).

D.33 centralizes workflow ORCHESTRATION behind a single declarative engine. The engine consumes the
D.28 ``RuntimeContext`` and the D.32 Runtime Policy Engine — it never evaluates runtime configuration
directly (the runtime engine remains the sole evaluator) and never makes business decisions (the policy
engine remains the sole decision engine). It coordinates existing services and never duplicates domain
behavior; the mature domain lifecycles remain authoritative and are registered ``in_domain``.

Three tables: ``orchestration_definitions`` (the discoverable registry of declarative workflow
definitions — seeded with 2 ``active`` + 13 ``in_domain`` definitions covering the workflow inventory),
``orchestration_instances`` (running instances with deterministic state), and ``orchestration_events``
(the append-only ledger enabling deterministic replay). Reuses the existing D.17 ``workflow.*``
capabilities (no new capabilities, no RBAC changes). Additive and reversible. Single Alembic head
(down ``z9b0c1d2e3f4``).
"""
import json

import sqlalchemy as sa
from alembic import op

from app.database.orchestration_seed import ORCHESTRATION_DEFINITIONS_SEED

revision = "za0b1c2d3e4f"
down_revision = "z9b0c1d2e3f4"
branch_labels = None
depends_on = None

_DEFINITION_STATUSES = ("active", "in_domain", "deprecated", "retired")
_INSTANCE_STATES = ("pending", "active", "waiting", "completed", "cancelled", "failed", "compensated")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade():
    bind = op.get_bind()
    op.create_table(
        "orchestration_definitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("owner", sa.Text),
        sa.Column("initial_stage", sa.Text),
        sa.Column("stages", sa.JSON),
        sa.Column("transitions", sa.JSON),
        sa.Column("completion_stages", sa.JSON),
        sa.Column("policy_refs", sa.JSON),
        sa.Column("runtime_refs", sa.JSON),
        sa.Column("depends_on", sa.JSON),
        sa.Column("timeout_seconds", sa.Integer),
        sa.Column("retry_policy", sa.JSON),
        sa.Column("compensation", sa.JSON),
        sa.Column("deprecated_at", sa.DateTime(timezone=True)),
        sa.Column("deprecated_reason", sa.Text),
        sa.Column("definition_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _DEFINITION_STATUSES), name="ck_orchestration_definition_status"),
    )
    op.create_table(
        "orchestration_instances",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("definition_code", sa.Text, nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.Text),
        sa.Column("runtime_snapshot_id", sa.Integer),
        sa.Column("context", sa.JSON),
        sa.Column("person_id", sa.Integer),
        sa.Column("household_id", sa.Integer),
        sa.Column("idempotency_key", sa.Text, unique=True),
        sa.Column("launched_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(_in("status", _INSTANCE_STATES), name="ck_orchestration_instance_status"),
    )
    op.create_table(
        "orchestration_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("instance_id", sa.Integer,
                  sa.ForeignKey("orchestration_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_stage", sa.Text),
        sa.Column("to_stage", sa.Text),
        sa.Column("action", sa.Text),
        sa.Column("policy_decision", sa.JSON),
        sa.Column("runtime_snapshot_id", sa.Integer),
        sa.Column("payload", sa.JSON),
        sa.Column("actor_user_id", sa.Integer),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("instance_id", "seq", name="uq_orchestration_event_seq"),
    )

    for d in ORCHESTRATION_DEFINITIONS_SEED:
        if bind.execute(sa.text("SELECT id FROM orchestration_definitions WHERE code=:c"),
                        {"c": d["code"]}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO orchestration_definitions "
                "(code, category, name, description, status, version, owner, initial_stage, stages, "
                " transitions, completion_stages, policy_refs, runtime_refs, depends_on, timeout_seconds, "
                " retry_policy, compensation) "
                "VALUES (:c, :cat, :n, :desc, :s, :v, :o, :init, CAST(:stg AS json), CAST(:tr AS json), "
                " CAST(:cs AS json), CAST(:pr AS json), CAST(:rr AS json), CAST(:dep AS json), :to, "
                " CAST(:retry AS json), CAST(:comp AS json))"),
                {"c": d["code"], "cat": d["category"], "n": d["name"], "desc": d.get("description"),
                 "s": d["status"], "v": d["version"], "o": d["owner"], "init": d["initial_stage"],
                 "stg": json.dumps(d["stages"]), "tr": json.dumps(d["transitions"]),
                 "cs": json.dumps(d["completion_stages"]), "pr": json.dumps(d["policy_refs"]),
                 "rr": json.dumps(d["runtime_refs"]), "dep": json.dumps(d["depends_on"]),
                 "to": d.get("timeout_seconds"), "retry": json.dumps(d.get("retry_policy") or {}),
                 "comp": json.dumps(d.get("compensation") or {})})


def downgrade():
    op.drop_table("orchestration_events")
    op.drop_table("orchestration_instances")
    op.drop_table("orchestration_definitions")
