"""Declared schema for the Phase D.39 Unified Work Queue saved-view tables.

Mirrors the live schema created by migration ``l3q4v5w6x7y8``. These tables store per-user QUEUE VIEW
STATE only (named saved filter/sort views + the user's default view and last-used filters). They hold
no authoritative work data and never alter a source record — presentation state only.
"""
from sqlalchemy import (
    BigInteger,
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


def define_work_queue_tables(metadata: MetaData):
    work_queue_saved_views = Table(
        "work_queue_saved_views", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("name", Text, nullable=False),
        Column("filters", JSONB, nullable=False, server_default="{}"),
        Column("sort", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("user_id", "name", name="uq_work_queue_view_user_name"),
    )
    work_queue_preferences = Table(
        "work_queue_preferences", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"),
               nullable=False, unique=True),
        Column("default_view", Text),          # a built-in key ("my_work") or "user:{view_id}"
        Column("last_filters", JSONB, nullable=False, server_default="{}"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {"work_queue_saved_views": work_queue_saved_views,
            "work_queue_preferences": work_queue_preferences}
