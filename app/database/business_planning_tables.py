"""Declared schema for the Phase D.12 Business Owner Planning workspace.

Mirrors the live schema created by migration ``j0b1u2s3o4w5``. The workspace is a
composition layer over existing domains; its ONLY persistence is
``business_planning_profiles`` — a mutable 1:1-per-business record holding the
succession / continuity / buy-sell / valuation / key-person planning facts that the
audit proved have no authoritative home. The CHECK constraints (status/source vocab)
live only in the migration.
"""
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    func,
)

STATUS_VOCAB = ("unknown", "not_started", "in_progress", "documented",
                "review_needed", "complete", "not_applicable")
SOURCE_VOCAB = ("advisor_entered", "client_reported", "document_derived")


def define_business_planning_tables(metadata: MetaData):
    business_planning_profiles = Table(
        "business_planning_profiles", metadata,
        Column("id", Integer, primary_key=True),
        Column("business_id", Integer,
               ForeignKey("relationship_entities.id", ondelete="CASCADE"),
               nullable=False, unique=True),
        Column("succession_plan_status", Text, nullable=False, server_default="unknown"),
        Column("buy_sell_status", Text, nullable=False, server_default="unknown"),
        Column("continuity_plan_status", Text, nullable=False, server_default="unknown"),
        Column("key_person_risk_status", Text, nullable=False, server_default="unknown"),
        Column("successor_person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("emergency_contact_person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("buy_sell_reviewed_at", Date),
        Column("valuation_amount", Numeric(16, 2)),
        Column("valuation_as_of", Date),
        Column("notes", Text, nullable=False, server_default=""),
        Column("source_type", Text, nullable=False, server_default="advisor_entered"),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("updated_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("source_type IN ('advisor_entered','client_reported','document_derived')",
                        name="ck_business_planning_source_type"),
    )
    return {"business_planning_profiles": business_planning_profiles}
