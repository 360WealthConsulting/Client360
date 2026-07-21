"""Campaigns, Referral Sources, and Business Development Attribution (Phase D.14).

Introduces two authoritative source domains — Campaigns and Referral Sources — and the
attribution linkage that ties Opportunities to them. Campaigns and Referral Sources are
authoritative (no campaign/referral data lives inside Opportunities); Opportunity *references*
them (campaign_id / referral_source_id + an opportunity-owned multi-touch attribution table)
but they never own Opportunities. Referral metrics (conversion, revenue, LTV, avg close time)
are COMPUTED from opportunities, never stored (no drift). Lifecycle logs are CASCADE-deletable
(security-relevant mutations remain in audit_events). Activities/documents REFERENCE existing
Microsoft 365 timeline events and documents (no duplication).

Eight new tables + additive attribution columns on ``opportunities`` + eleven capabilities.
Linear, reversible; capabilities seeded idempotently. No existing behavior is changed
(all new opportunity columns are nullable).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "l2c3d4e5f6a7"
down_revision = "k1o2p3p4t5y6"
branch_labels = None
depends_on = None

_CAMPAIGN_STATUSES = ("draft", "active", "paused", "completed", "archived")
_REFERRAL_TYPES = (
    "individual", "organization", "existing_client", "cpa", "attorney", "bank",
    "financial_advisor", "insurance_agent", "estate_planner", "mortgage_broker", "coi",
    "employee", "marketing_vendor", "website", "event", "advertising", "other")

_CAPS = (
    ("campaign.view", "View campaigns and campaign performance.", False,
     ("administrator", "advisor", "operations")),
    ("campaign.edit", "Create and edit campaigns.", False, ("administrator", "operations")),
    ("campaign.delete", "Delete campaigns.", False, ("administrator",)),
    ("campaign.report", "View campaign reports.", False, ("administrator", "advisor", "operations")),
    ("campaign.archive", "Archive campaigns.", False, ("administrator", "operations")),
    ("campaign.manage_budget", "Manage campaign budget and actual cost.", True, ("administrator",)),
    ("campaign.manage_roi", "Manage campaign expected/actual ROI.", True, ("administrator",)),
    ("referral.view", "View referral sources.", False, ("administrator", "advisor", "operations")),
    ("referral.edit", "Create and edit referral sources.", False,
     ("administrator", "advisor", "operations")),
    ("referral.delete", "Delete referral sources.", False, ("administrator",)),
    ("referral.report", "View referral reports.", False, ("administrator", "advisor", "operations")),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("campaign_type", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("start_date", sa.Date),
        sa.Column("end_date", sa.Date),
        sa.Column("budget", sa.Numeric(16, 2)),
        sa.Column("actual_cost", sa.Numeric(16, 2)),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("objective", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("target_audience", sa.Text),
        sa.Column("marketing_channel", sa.Text),
        sa.Column("expected_roi", sa.Numeric(8, 2)),
        sa.Column("actual_roi", sa.Numeric(8, 2)),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('draft','active','paused','completed','archived')",
                           name="ck_campaigns_status"),
    )
    op.create_index("ix_campaigns_status", "campaigns", ["status"])
    op.create_index("ix_campaigns_owner", "campaigns", ["owner_user_id"])

    op.create_table(
        "campaign_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("note", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_campaign_events_campaign", "campaign_events", ["campaign_id"])

    op.create_table(
        "campaign_activities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_type", sa.Text, nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("body", sa.Text),
        sa.Column("activity_date", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("timeline_event_id", sa.Integer, sa.ForeignKey("timeline_events.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_campaign_activities_campaign", "campaign_activities", ["campaign_id"])

    op.create_table(
        "campaign_documents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("label", sa.Text),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("campaign_id", "document_id", name="uq_campaign_document"),
    )
    op.create_index("ix_campaign_documents_campaign", "campaign_documents", ["campaign_id"])

    op.create_table(
        "referral_sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("source_type", sa.Text, nullable=False, server_default="other"),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("relationship_type", sa.Text),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        sa.Column("email", sa.Text),
        sa.Column("phone", sa.Text),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("introduced_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("primary_advisor_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('active','inactive')", name="ck_referral_sources_status"),
    )
    op.create_index("ix_referral_sources_status", "referral_sources", ["status"])
    op.create_index("ix_referral_sources_primary_advisor", "referral_sources", ["primary_advisor_id"])
    op.create_index("ix_referral_sources_type", "referral_sources", ["source_type"])

    op.create_table(
        "referral_source_advisors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("referral_source_id", sa.Integer,
                  sa.ForeignKey("referral_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text, nullable=False, server_default="supporting"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("referral_source_id", "user_id", name="uq_referral_source_advisor"),
    )
    op.create_index("ix_referral_source_advisors_src", "referral_source_advisors", ["referral_source_id"])

    op.create_table(
        "referral_source_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("referral_source_id", sa.Integer,
                  sa.ForeignKey("referral_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("note", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_referral_source_events_src", "referral_source_events", ["referral_source_id"])

    # Opportunity-owned multi-touch attribution (primary + secondary + weighted).
    op.create_table(
        "opportunity_attributions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("opportunity_id", sa.Integer,
                  sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="SET NULL")),
        sa.Column("referral_source_id", sa.Integer,
                  sa.ForeignKey("referral_sources.id", ondelete="SET NULL")),
        sa.Column("weight", sa.Numeric(5, 2), nullable=False, server_default="100"),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_opportunity_attributions_opp", "opportunity_attributions", ["opportunity_id"])
    op.create_index("ix_opportunity_attributions_campaign", "opportunity_attributions", ["campaign_id"])
    op.create_index("ix_opportunity_attributions_referral", "opportunity_attributions", ["referral_source_id"])

    # Additive attribution columns on opportunities (all nullable — no behavior change).
    op.add_column("opportunities", sa.Column("campaign_id", sa.Integer,
                  sa.ForeignKey("campaigns.id", ondelete="SET NULL")))
    op.add_column("opportunities", sa.Column("referral_source_id", sa.Integer,
                  sa.ForeignKey("referral_sources.id", ondelete="SET NULL")))
    op.add_column("opportunities", sa.Column("origin", sa.Text))
    op.add_column("opportunities", sa.Column("lead_method", sa.Text))
    op.add_column("opportunities", sa.Column("marketing_medium", sa.Text))
    op.add_column("opportunities", sa.Column("referral_type", sa.Text))
    op.add_column("opportunities", sa.Column("attribution_locked", sa.Boolean,
                  nullable=False, server_default=sa.text("false")))
    op.create_index("ix_opportunities_campaign", "opportunities", ["campaign_id"])
    op.create_index("ix_opportunities_referral_source", "opportunities", ["referral_source_id"])

    for code, description, sensitive, roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, :s) RETURNING id"),
                {"c": code, "d": description, "s": sensitive}).scalar()
        for role_code in roles:
            role_id = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
            if role_id is None:
                continue
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(sa.text("INSERT INTO role_capabilities (role_id, capability_id) "
                                     "VALUES (:r, :c)"), {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    op.drop_index("ix_opportunities_referral_source", table_name="opportunities")
    op.drop_index("ix_opportunities_campaign", table_name="opportunities")
    for col in ("attribution_locked", "referral_type", "marketing_medium", "lead_method",
                "origin", "referral_source_id", "campaign_id"):
        op.drop_column("opportunities", col)
    op.drop_table("opportunity_attributions")
    op.drop_table("referral_source_events")
    op.drop_table("referral_source_advisors")
    op.drop_table("referral_sources")
    op.drop_table("campaign_documents")
    op.drop_table("campaign_activities")
    op.drop_table("campaign_events")
    op.drop_table("campaigns")
    for code, _d, _s, _roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
