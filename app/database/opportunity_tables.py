"""Declared schema for the Phase D.13 Opportunity & Pipeline domain.

Mirrors the live schema created by migration ``k1o2p3p4t5y6``. This is a first-class
authoritative domain: it references canonical People / Households / Organizations by nullable
FK but owns its own pipeline/opportunity/activity data. The append-only trigger on
``opportunity_events`` and the seed data live only in the migration.
"""
from sqlalchemy import (
    BigInteger,
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

STAGE_CATEGORIES = ("open", "won", "lost", "dormant", "cancelled")
OPPORTUNITY_STATUSES = ("open", "won", "lost", "dormant", "cancelled")


def define_opportunity_tables(metadata: MetaData):
    pipelines = Table(
        "opportunity_pipelines", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Text, nullable=False, unique=True),
        Column("name", Text, nullable=False),
        Column("is_default", Boolean, nullable=False, server_default="false"),
        Column("active", Boolean, nullable=False, server_default="true"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    stages = Table(
        "opportunity_stages", metadata,
        Column("id", Integer, primary_key=True),
        Column("pipeline_id", Integer, ForeignKey("opportunity_pipelines.id", ondelete="CASCADE"),
               nullable=False),
        Column("code", Text, nullable=False),
        Column("name", Text, nullable=False),
        Column("sort_order", Integer, nullable=False, server_default="0"),
        Column("category", Text, nullable=False, server_default="open"),
        Column("default_probability", Numeric(5, 2), nullable=False, server_default="0"),
        Column("active", Boolean, nullable=False, server_default="true"),
        CheckConstraint("category IN ('open','won','lost','dormant','cancelled')",
                        name="ck_opportunity_stages_category"),
        UniqueConstraint("pipeline_id", "code", name="uq_opportunity_stage_code"),
    )
    opportunities = Table(
        "opportunities", metadata,
        Column("id", Integer, primary_key=True),
        Column("pipeline_id", Integer, ForeignKey("opportunity_pipelines.id", ondelete="RESTRICT"),
               nullable=False),
        Column("stage_id", Integer, ForeignKey("opportunity_stages.id", ondelete="RESTRICT"),
               nullable=False),
        Column("title", Text, nullable=False),
        Column("status", Text, nullable=False, server_default="open"),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("household_id", Integer, ForeignKey("households.id", ondelete="SET NULL")),
        Column("organization_id", Integer, ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        Column("primary_advisor_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("supporting_advisor_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("primary_service_line", Text),
        Column("secondary_service_lines", JSONB, nullable=False, server_default="[]"),
        Column("source", Text),
        Column("referral_source_person_id", Integer, ForeignKey("people.id", ondelete="SET NULL")),
        Column("referral_source_text", Text),
        Column("originating_campaign", Text),
        Column("probability", Numeric(5, 2)),
        Column("expected_revenue", Numeric(16, 2)),
        Column("expected_close_date", Date),
        Column("next_action", Text),
        Column("next_action_date", Date),
        Column("win_reason", Text),
        Column("loss_reason", Text),
        Column("tags", JSONB, nullable=False, server_default="[]"),
        Column("notes", Text, nullable=False, server_default=""),
        Column("closed_at", DateTime(timezone=True)),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("updated_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        CheckConstraint("status IN ('open','won','lost','dormant','cancelled')",
                        name="ck_opportunities_status"),
    )
    participants = Table(
        "opportunity_participants", metadata,
        Column("id", Integer, primary_key=True),
        Column("opportunity_id", Integer, ForeignKey("opportunities.id", ondelete="CASCADE"),
               nullable=False),
        Column("person_id", Integer, ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        Column("role", Text),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("opportunity_id", "person_id", name="uq_opportunity_participant"),
    )
    events = Table(
        "opportunity_events", metadata,
        Column("id", Integer, primary_key=True),
        Column("opportunity_id", Integer, ForeignKey("opportunities.id", ondelete="CASCADE"),
               nullable=False),
        Column("event_type", Text, nullable=False),
        Column("from_stage_id", Integer, ForeignKey("opportunity_stages.id", ondelete="SET NULL")),
        Column("to_stage_id", Integer, ForeignKey("opportunity_stages.id", ondelete="SET NULL")),
        Column("from_status", Text),
        Column("to_status", Text),
        Column("actor_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("note", Text),
        Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    activities = Table(
        "opportunity_activities", metadata,
        Column("id", Integer, primary_key=True),
        Column("opportunity_id", Integer, ForeignKey("opportunities.id", ondelete="CASCADE"),
               nullable=False),
        Column("activity_type", Text, nullable=False),
        Column("subject", Text),
        Column("body", Text),
        Column("activity_date", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("actor_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("timeline_event_id", Integer, ForeignKey("timeline_events.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    work_links = Table(
        "opportunity_work_links", metadata,
        Column("id", Integer, primary_key=True),
        Column("opportunity_id", Integer, ForeignKey("opportunities.id", ondelete="CASCADE"),
               nullable=False),
        Column("advisor_work_item_id", BigInteger,
               ForeignKey("advisor_work_items.id", ondelete="CASCADE"), nullable=False),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("opportunity_id", "advisor_work_item_id", name="uq_opportunity_work_link"),
    )
    return {
        "opportunity_pipelines": pipelines, "opportunity_stages": stages,
        "opportunities": opportunities, "opportunity_participants": participants,
        "opportunity_events": events, "opportunity_activities": activities,
        "opportunity_work_links": work_links,
    }
