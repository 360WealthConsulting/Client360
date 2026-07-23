"""Declared schema for the Phase D.38 Advisor Workspace personalization tables.

Mirrors the live schema created by migration ``k2w3s4p5r6f7``. These tables store
per-advisor UI VIEW STATE only (widget order / hidden / pinned / remembered filters /
saved presets) — they hold no business data and no authoritative state. They are
personal, mutable settings, not a ledger; nothing downstream depends on them.
"""
from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB


def define_workspace_tables(metadata: MetaData):
    # Named saved layouts (presets) — created first so preferences can FK an active preset.
    workspace_presets = Table(
        "workspace_presets", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("name", Text, nullable=False),
        # Snapshot of {order:[keys], hidden:[keys], pinned:[keys], filters:{key:{...}}}.
        Column("layout", JSONB, nullable=False),
        Column("is_favorite", Boolean, nullable=False, server_default="false"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("user_id", "name", name="uq_workspace_preset_user_name"),
    )
    # Live per-advisor workspace layout state — exactly one row per user.
    workspace_preferences = Table(
        "workspace_preferences", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"),
               nullable=False, unique=True),
        Column("widget_order", JSONB),          # null → registry default order
        Column("hidden_widgets", JSONB, nullable=False, server_default="[]"),
        Column("pinned_widgets", JSONB, nullable=False, server_default="[]"),
        Column("filters", JSONB, nullable=False, server_default="{}"),
        Column("active_preset_id", BigInteger,
               ForeignKey("workspace_presets.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {"workspace_presets": workspace_presets, "workspace_preferences": workspace_preferences}
