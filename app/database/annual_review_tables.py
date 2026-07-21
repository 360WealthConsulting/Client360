"""Declared schema for the Phase D.11 Annual Review Workspace.

Mirrors the live schema created by migration ``i9a1n2r3e4v5``. The Annual Review
Workspace is a composition layer over existing services; its ONLY persistence is
``annual_review_sessions`` — a mutable advisor-activity record (notes + a
presentation-only checklist). It is not an append-only ledger (rows are edited in
place); the partial-unique OPEN guard lives only in the migration.
"""
from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import JSONB

_STATUSES = ("draft", "in_progress", "completed", "archived")


def define_annual_review_tables(metadata: MetaData):
    annual_review_sessions = Table(
        "annual_review_sessions", metadata,
        Column("id", Integer, primary_key=True),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="CASCADE"),
               nullable=False),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("advisor_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("started_at", DateTime(timezone=True)),
        Column("completed_at", DateTime(timezone=True)),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("notes", Text, nullable=False, server_default=""),
        Column("checklist_state", JSONB, nullable=False, server_default="{}"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint(
            "status IN ('draft','in_progress','completed','archived')",
            name="ck_annual_review_sessions_status"),
    )
    return {"annual_review_sessions": annual_review_sessions}
