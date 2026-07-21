"""Declared schema for the Phase D.9 advisor work-management tables.

Mirrors the live schema created by migration ``g1w2o3r4k5m6`` (the append-only trigger
and the partial-unique guard live only in the migration).
"""
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

_STATUSES = ("new", "assigned", "in_progress", "waiting", "completed", "cancelled", "archived")


def define_advisor_work_tables(metadata: MetaData):
    advisor_work_items = Table(
        "advisor_work_items", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("recommendation_id", Text, nullable=False),
        Column("recommendation_type", Text, nullable=False),
        Column("governing_rule", Text, nullable=False),
        Column("rule_version", Text, nullable=False),
        Column("policy_gate", Text, nullable=False),
        Column("priority", Text, nullable=False),
        Column("recommendation_snapshot", JSONB, nullable=False),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("owner_principal_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("status", Text, nullable=False, server_default="new"),
        Column("due_date", Date),
        Column("completed_at", DateTime(timezone=True)),
        Column("completed_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("completion_notes", Text),
        Column("archived_at", DateTime(timezone=True)),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in _STATUSES) + ")",
                        name="ck_advisor_work_items_status"),
    )
    advisor_work_events = Table(
        "advisor_work_events", metadata,
        Column("id", BigInteger, primary_key=True),
        Column("advisor_work_item_id", BigInteger,
               ForeignKey("advisor_work_items.id", ondelete="RESTRICT"), nullable=False),
        Column("event_type", Text, nullable=False),
        Column("prior_status", Text),
        Column("new_status", Text),
        Column("actor_principal_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        Column("note", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {"advisor_work_items": advisor_work_items, "advisor_work_events": advisor_work_events}
