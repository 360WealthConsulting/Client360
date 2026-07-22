"""Declared schema for the Phase D.16 Document platform tables.

Mirrors the live schema created by migration ``n4e5f6a7b8c9``. Documents are the authoritative
source domain; these tables add folders, immutable version history, polymorphic multi-domain
relationships, retention policies, and a lifecycle event log. The existing ``documents`` table is
extended in the migration (additive columns) and picked up at runtime by reflection.
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
    UniqueConstraint,
    func,
)

DOCUMENT_CLASSIFICATIONS = ("client", "compliance", "tax", "insurance", "benefits", "retirement",
                            "estate", "investment", "operations", "marketing", "legal", "hr",
                            "internal", "archived")
DOCUMENT_STATUSES = ("draft", "active", "review", "approved", "superseded", "archived", "deleted")
DOCUMENT_ENTITY_TYPES = ("person", "household", "organization", "opportunity", "campaign",
                         "referral_source", "annual_review", "business_owner_plan",
                         "compliance_review", "advisor_work", "timeline_event")


def define_document_platform_tables(metadata: MetaData):
    folders = Table(
        "document_folders", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("parent_folder_id", Integer, ForeignKey("document_folders.id", ondelete="SET NULL")),
        Column("classification", Text),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    retention = Table(
        "document_retention_policies", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("retention_years", Integer),
        Column("action_on_expiry", Text, nullable=False, server_default="review"),
        Column("description", Text),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("action_on_expiry IN ('review','archive','delete')", name="ck_retention_action"),
    )
    # NOTE: document_versions already exists (client portal, migration f640a6c4e5f6); D.16
    # EXTENDS it additively in the migration and does not redeclare it here.
    relationships = Table(
        "document_relationships", metadata,
        Column("id", Integer, primary_key=True),
        Column("document_id", Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        Column("entity_type", Text, nullable=False),
        Column("entity_id", Integer, nullable=False),
        Column("relationship_type", Text),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("document_id", "entity_type", "entity_id", name="uq_document_relationship"),
    )
    events = Table(
        "document_events", metadata,
        Column("id", Integer, primary_key=True),
        Column("document_id", Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("note", Text),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {"document_folders": folders, "document_retention_policies": retention,
            "document_relationships": relationships, "document_events": events}
