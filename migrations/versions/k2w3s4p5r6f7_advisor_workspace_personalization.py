"""Advisor Workspace personalization (Phase D.38).

Adds per-advisor workspace VIEW STATE so the advisor home can be personalized:
- ``workspace_presets`` — named saved layouts (order/hidden/pinned/filters snapshots).
- ``workspace_preferences`` — the live per-advisor layout state (exactly one row per user).

These store UI settings only — no business data, no authoritative state, no ledger. The
authoritative write side, the transactional outbox, projections, governance, and RBAC are
untouched. Seeds one non-sensitive capability ``workspace.personalize`` into the advisor,
operations, and administrator roles (the workspace page itself remains gated by ``client.read``).
Additive and reversible; no data backfilled. Single Alembic head (down ``zd3e4f5a6b7c``).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "k2w3s4p5r6f7"
down_revision = "zd3e4f5a6b7c"
branch_labels = None
depends_on = None

NEW_CAPS = {
    "workspace.personalize": ("Personalize your advisor workspace layout (order, hidden, pinned, "
                              "saved presets).", False),
}
_ROLES = ("advisor", "operations", "administrator")


def upgrade():
    op.create_table(
        "workspace_presets",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("layout", postgresql.JSONB, nullable=False),
        sa.Column("is_favorite", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "name", name="uq_workspace_preset_user_name"),
    )
    op.create_index("ix_workspace_presets_user", "workspace_presets", ["user_id"])

    op.create_table(
        "workspace_preferences",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("widget_order", postgresql.JSONB),
        sa.Column("hidden_widgets", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("pinned_widgets", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("filters", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("active_preset_id", sa.BigInteger,
                  sa.ForeignKey("workspace_presets.id", ondelete="SET NULL")),
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
    op.drop_table("workspace_preferences")
    op.drop_table("workspace_presets")
