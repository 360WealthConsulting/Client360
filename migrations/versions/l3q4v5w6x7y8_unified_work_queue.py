"""Unified Work Queue saved views (Phase D.39).

Adds per-user QUEUE VIEW STATE so the Unified Work Queue (GET /work) can offer saved views + a default
view + remembered filters:
- ``work_queue_saved_views`` — named saved filter/sort views (one per user+name).
- ``work_queue_preferences`` — the user's default view + last-used filters (one row per user).

These store presentation state only — no authoritative work data, no ledger, and they never alter a
source record. The authoritative work services, the transactional outbox, projections, governance, and
RBAC are untouched. Seeds one non-sensitive capability ``work_queue.saved_views`` into the advisor,
operations, compliance, and administrator roles (viewing the queue reuses ``work.read``/``capacity.read``;
actions reuse the owning services' capabilities). Additive and reversible. Single Alembic head
(down ``k2w3s4p5r6f7``).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "l3q4v5w6x7y8"
down_revision = "k2w3s4p5r6f7"
branch_labels = None
depends_on = None

NEW_CAPS = {
    "work_queue.saved_views": ("Save, apply, and manage personal Unified Work Queue views.", False),
}
_ROLES = ("advisor", "operations", "compliance", "administrator")


def upgrade():
    op.create_table(
        "work_queue_saved_views",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("filters", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sort", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "name", name="uq_work_queue_view_user_name"),
    )
    op.create_index("ix_work_queue_saved_views_user", "work_queue_saved_views", ["user_id"])
    op.create_table(
        "work_queue_preferences",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("default_view", sa.Text),
        sa.Column("last_filters", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
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
                {"c": code, "d": description, "s": sensitive}).scalar()
        for role_id in role_ids:
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(
                    sa.text("INSERT INTO role_capabilities (role_id, capability_id) VALUES (:r, :c)"),
                    {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    for code in NEW_CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
    op.drop_table("work_queue_preferences")
    op.drop_table("work_queue_saved_views")
