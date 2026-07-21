"""Advisor work management (Phase D.9).

Advisor-facing operational work derived from Advisor Intelligence *recommendations*.
This is a NEW, separately namespaced layer — it does not touch the existing Work
Management system (``/work``, ``work.read``/``work.write``, tasks/exceptions/workflow
steps). Completing an advisor work item records operational activity only; it never
changes the underlying recommendation.

Adds:
- ``advisor_work_items`` — one work item per recommendation snapshot, with a partial
  unique guard so there is at most one OPEN item per (recommendation, person, rule).
- ``advisor_work_events`` — the append-only lifecycle history (trigger-blocked).

Seeds four capabilities (``advisor_work.read/create/assign/update``) into the advisor,
operations, and administrator roles. Additive and reversible; no data backfilled.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "g1w2o3r4k5m6"
down_revision = "f8a9u1t2h3r4"
branch_labels = None
depends_on = None

_OPEN = ("new", "assigned", "in_progress", "waiting")
_STATUSES = _OPEN + ("completed", "cancelled", "archived")

NEW_CAPS = {
    "advisor_work.read": ("View advisor work items.", False),
    "advisor_work.create": ("Create advisor work from a recommendation.", False),
    "advisor_work.assign": ("Assign an owner to advisor work.", False),
    "advisor_work.update": ("Update advisor work status/completion.", False),
}
_ROLES = ("advisor", "operations", "administrator")


def upgrade():
    op.create_table(
        "advisor_work_items",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("recommendation_id", sa.Text, nullable=False),
        sa.Column("recommendation_type", sa.Text, nullable=False),
        sa.Column("governing_rule", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("policy_gate", sa.Text, nullable=False),
        sa.Column("priority", sa.Text, nullable=False),
        sa.Column("recommendation_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("owner_principal_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text, nullable=False, server_default="new"),
        sa.Column("due_date", sa.Date),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("completion_notes", sa.Text),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in _STATUSES) + ")",
                           name="ck_advisor_work_items_status"),
    )
    op.create_index("ix_advisor_work_items_status", "advisor_work_items", ["status"])
    op.create_index("ix_advisor_work_items_owner", "advisor_work_items", ["owner_principal_id"])
    op.create_index("ix_advisor_work_items_person", "advisor_work_items", ["person_id"])
    op.create_index("ix_advisor_work_items_household", "advisor_work_items", ["household_id"])
    op.create_index("ix_advisor_work_items_rule", "advisor_work_items", ["governing_rule"])
    op.create_index(
        "uq_open_advisor_work", "advisor_work_items",
        ["recommendation_id", "person_id", "governing_rule"], unique=True,
        postgresql_where=sa.text("status IN (" + ", ".join(f"'{s}'" for s in _OPEN) + ")"),
    )

    op.create_table(
        "advisor_work_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("advisor_work_item_id", sa.BigInteger,
                  sa.ForeignKey("advisor_work_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("prior_status", sa.Text),
        sa.Column("new_status", sa.Text),
        sa.Column("actor_principal_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_advisor_work_events_item", "advisor_work_events", ["advisor_work_item_id"])
    op.execute(
        "CREATE FUNCTION prevent_advisor_work_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'advisor_work_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER advisor_work_events_immutable BEFORE UPDATE OR DELETE ON advisor_work_events "
        "FOR EACH ROW EXECUTE FUNCTION prevent_advisor_work_event_mutation()"
    )

    bind = op.get_bind()
    role_ids = [r for r in (
        bind.execute(sa.text("SELECT id FROM roles WHERE code = :c"), {"c": code}).scalar()
        for code in _ROLES) if r is not None]
    for code, (description, sensitive) in NEW_CAPS.items():
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, :s) RETURNING id"),
                {"c": code, "d": description, "s": sensitive},
            ).scalar()
        for role_id in role_ids:
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid},
            ).scalar()
            if not exists:
                bind.execute(
                    sa.text("INSERT INTO role_capabilities (role_id, capability_id) VALUES (:r, :c)"),
                    {"r": role_id, "c": cid},
                )


def downgrade():
    bind = op.get_bind()
    for code in NEW_CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
    op.execute("DROP TRIGGER IF EXISTS advisor_work_events_immutable ON advisor_work_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_advisor_work_event_mutation()")
    op.drop_table("advisor_work_events")
    op.drop_table("advisor_work_items")
