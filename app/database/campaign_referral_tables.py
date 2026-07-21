"""Declared schema for the Phase D.14 Campaign & Referral Source domains + attribution.

Mirrors the live schema created by migration ``l2c3d4e5f6a7``. Campaigns and Referral Sources
are authoritative source domains; the attribution linkage (``opportunity_attributions`` and the
additive ``opportunities`` columns) is owned by the Opportunity domain. Seed/behaviour lives in
the migration; referral metrics are computed, never stored.
"""
from sqlalchemy import (
    Boolean,
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

CAMPAIGN_STATUSES = ("draft", "active", "paused", "completed", "archived")
REFERRAL_SOURCE_TYPES = (
    "individual", "organization", "existing_client", "cpa", "attorney", "bank",
    "financial_advisor", "insurance_agent", "estate_planner", "mortgage_broker", "coi",
    "employee", "marketing_vendor", "website", "event", "advertising", "other")


def define_campaign_referral_tables(metadata: MetaData):
    campaigns = Table(
        "campaigns", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("campaign_type", Text),
        Column("status", Text, nullable=False, server_default="draft"),
        Column("start_date", Date),
        Column("end_date", Date),
        Column("budget", Numeric(16, 2)),
        Column("actual_cost", Numeric(16, 2)),
        Column("owner_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("objective", Text),
        Column("description", Text),
        Column("target_audience", Text),
        Column("marketing_channel", Text),
        Column("expected_roi", Numeric(8, 2)),
        Column("actual_roi", Numeric(8, 2)),
        Column("tags", JSONB, nullable=False, server_default="[]"),
        Column("notes", Text, nullable=False, server_default=""),
        Column("archived_at", DateTime(timezone=True)),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("updated_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("status IN ('draft','active','paused','completed','archived')",
                        name="ck_campaigns_status"),
    )
    campaign_events = Table(
        "campaign_events", metadata,
        Column("id", Integer, primary_key=True),
        Column("campaign_id", Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("note", Text),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    campaign_activities = Table(
        "campaign_activities", metadata,
        Column("id", Integer, primary_key=True),
        Column("campaign_id", Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        Column("activity_type", Text, nullable=False),
        Column("subject", Text),
        Column("body", Text),
        Column("activity_date", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("actor_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("timeline_event_id", Integer, ForeignKey("timeline_events.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    campaign_documents = Table(
        "campaign_documents", metadata,
        Column("id", Integer, primary_key=True),
        Column("campaign_id", Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        Column("document_id", Integer, ForeignKey("documents.id", ondelete="SET NULL")),
        Column("label", Text),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("campaign_id", "document_id", name="uq_campaign_document"),
    )
    referral_sources = Table(
        "referral_sources", metadata,
        Column("id", Integer, primary_key=True),
        Column("name", Text, nullable=False),
        Column("source_type", Text, nullable=False, server_default="other"),
        Column("status", Text, nullable=False, server_default="active"),
        Column("relationship_type", Text),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("organization_id", Integer, ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        Column("email", Text),
        Column("phone", Text),
        Column("notes", Text, nullable=False, server_default=""),
        Column("introduced_by_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("primary_advisor_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("updated_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("status IN ('active','inactive')", name="ck_referral_sources_status"),
    )
    referral_source_advisors = Table(
        "referral_source_advisors", metadata,
        Column("id", Integer, primary_key=True),
        Column("referral_source_id", Integer, ForeignKey("referral_sources.id", ondelete="CASCADE"),
               nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("role", Text, nullable=False, server_default="supporting"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("referral_source_id", "user_id", name="uq_referral_source_advisor"),
    )
    referral_source_events = Table(
        "referral_source_events", metadata,
        Column("id", Integer, primary_key=True),
        Column("referral_source_id", Integer, ForeignKey("referral_sources.id", ondelete="CASCADE"),
               nullable=False),
        Column("event_type", Text, nullable=False),
        Column("actor_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("note", Text),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    opportunity_attributions = Table(
        "opportunity_attributions", metadata,
        Column("id", Integer, primary_key=True),
        Column("opportunity_id", Integer, ForeignKey("opportunities.id", ondelete="CASCADE"),
               nullable=False),
        Column("campaign_id", Integer, ForeignKey("campaigns.id", ondelete="SET NULL")),
        Column("referral_source_id", Integer, ForeignKey("referral_sources.id", ondelete="SET NULL")),
        Column("weight", Numeric(5, 2), nullable=False, server_default="100"),
        Column("is_primary", Boolean, nullable=False, server_default="false"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    return {
        "campaigns": campaigns, "campaign_events": campaign_events,
        "campaign_activities": campaign_activities, "campaign_documents": campaign_documents,
        "referral_sources": referral_sources, "referral_source_advisors": referral_source_advisors,
        "referral_source_events": referral_source_events,
        "opportunity_attributions": opportunity_attributions,
    }
