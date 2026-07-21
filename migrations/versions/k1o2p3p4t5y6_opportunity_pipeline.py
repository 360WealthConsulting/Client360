"""Opportunity & Pipeline Intelligence domain (Phase D.13).

Introduces a first-class, authoritative Opportunity / sales-pipeline domain. It REFERENCES
canonical People / Households / Organizations (via validated, nullable FKs) but never
duplicates or creates them. Configurable pipelines/stages (no hard-coded stage names in
business logic — logic keys off ``opportunity_stages.category``). Lifecycle history is a
CASCADE-deletable log (``opportunity_events``); security-relevant mutations remain in the
separate ``audit_events`` record. Activities may REFERENCE an
existing Microsoft 365 timeline event (no calendar/mail duplication). Advisor Work is
referenced through an Opportunity-owned link table (Advisor Work never owns an opportunity).

Seven tables + a default pipeline with the twelve default stages + seven capabilities. Linear,
reversible; capabilities seeded idempotently. No source-domain table is modified.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "k1o2p3p4t5y6"
down_revision = "j0b1u2s3o4w5"
branch_labels = None
depends_on = None

_STAGE_CATEGORIES = ("open", "won", "lost", "dormant", "cancelled")
_OPP_STATUSES = ("open", "won", "lost", "dormant", "cancelled")

# Default pipeline stages (configurable — seeded, not hard-coded in logic).
# (code, name, sort_order, category, default_probability)
_DEFAULT_STAGES = (
    ("lead", "Lead", 10, "open", 10),
    ("qualified", "Qualified", 20, "open", 25),
    ("discovery_scheduled", "Discovery Scheduled", 30, "open", 30),
    ("discovery_completed", "Discovery Completed", 40, "open", 40),
    ("proposal", "Proposal", 50, "open", 60),
    ("waiting_on_client", "Waiting on Client", 60, "open", 65),
    ("negotiation", "Negotiation", 70, "open", 75),
    ("implementation", "Implementation", 80, "open", 90),
    ("won", "Won", 90, "won", 100),
    ("lost", "Lost", 100, "lost", 0),
    ("dormant", "Dormant", 110, "dormant", 0),
    ("cancelled", "Cancelled", 120, "cancelled", 0),
)

_CAPS = (
    ("opportunity.view", "View opportunities and the sales pipeline.", False,
     ("administrator", "advisor", "operations")),
    ("opportunity.edit", "Create and edit opportunities and log activities.", False,
     ("administrator", "advisor", "operations")),
    ("opportunity.delete", "Delete opportunities.", True, ("administrator",)),
    ("opportunity.assign", "Assign the primary/supporting advisor on an opportunity.", False,
     ("administrator", "advisor")),
    ("opportunity.close", "Close an opportunity as won or lost.", False,
     ("administrator", "advisor")),
    ("opportunity.report", "View pipeline reports.", False,
     ("administrator", "advisor", "operations")),
    ("opportunity.forecast", "View sensitive revenue forecasts.", True,
     ("administrator", "advisor")),
)


def upgrade():
    bind = op.get_bind()

    op.create_table(
        "opportunity_pipelines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "opportunity_stages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("pipeline_id", sa.Integer,
                  sa.ForeignKey("opportunity_pipelines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("category", sa.Text, nullable=False, server_default="open"),
        sa.Column("default_probability", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint("category IN ('open','won','lost','dormant','cancelled')",
                           name="ck_opportunity_stages_category"),
        sa.UniqueConstraint("pipeline_id", "code", name="uq_opportunity_stage_code"),
    )
    op.create_index("ix_opportunity_stages_pipeline", "opportunity_stages", ["pipeline_id"])

    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("pipeline_id", sa.Integer,
                  sa.ForeignKey("opportunity_pipelines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("stage_id", sa.Integer,
                  sa.ForeignKey("opportunity_stages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="SET NULL")),
        sa.Column("primary_advisor_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("supporting_advisor_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("primary_service_line", sa.Text),
        sa.Column("secondary_service_lines", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source", sa.Text),
        sa.Column("referral_source_person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("referral_source_text", sa.Text),
        sa.Column("originating_campaign", sa.Text),
        sa.Column("probability", sa.Numeric(5, 2)),
        sa.Column("expected_revenue", sa.Numeric(16, 2)),
        sa.Column("expected_close_date", sa.Date),
        sa.Column("next_action", sa.Text),
        sa.Column("next_action_date", sa.Date),
        sa.Column("win_reason", sa.Text),
        sa.Column("loss_reason", sa.Text),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('open','won','lost','dormant','cancelled')",
                           name="ck_opportunities_status"),
    )
    for col in ("primary_advisor_id", "stage_id", "person_id", "household_id",
                "organization_id", "status", "expected_close_date"):
        op.create_index(f"ix_opportunities_{col}", "opportunities", [col])

    op.create_table(
        "opportunity_participants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("opportunity_id", sa.Integer,
                  sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("opportunity_id", "person_id", name="uq_opportunity_participant"),
    )
    op.create_index("ix_opportunity_participants_opp", "opportunity_participants", ["opportunity_id"])

    op.create_table(
        "opportunity_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("opportunity_id", sa.Integer,
                  sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("from_stage_id", sa.Integer, sa.ForeignKey("opportunity_stages.id", ondelete="SET NULL")),
        sa.Column("to_stage_id", sa.Integer, sa.ForeignKey("opportunity_stages.id", ondelete="SET NULL")),
        sa.Column("from_status", sa.Text),
        sa.Column("to_status", sa.Text),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("note", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_opportunity_events_opp", "opportunity_events", ["opportunity_id"])

    op.create_table(
        "opportunity_activities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("opportunity_id", sa.Integer,
                  sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_type", sa.Text, nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("body", sa.Text),
        sa.Column("activity_date", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        # Reference an EXISTING Microsoft 365 timeline event (calendar/mail) — no duplication.
        sa.Column("timeline_event_id", sa.Integer,
                  sa.ForeignKey("timeline_events.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_opportunity_activities_opp", "opportunity_activities", ["opportunity_id"])

    op.create_table(
        "opportunity_work_links",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("opportunity_id", sa.Integer,
                  sa.ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("advisor_work_item_id", sa.BigInteger,
                  sa.ForeignKey("advisor_work_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("opportunity_id", "advisor_work_item_id", name="uq_opportunity_work_link"),
    )
    op.create_index("ix_opportunity_work_links_opp", "opportunity_work_links", ["opportunity_id"])

    # Seed the default pipeline + its twelve stages.
    pipeline_id = bind.execute(sa.text(
        "INSERT INTO opportunity_pipelines (code, name, is_default, active) "
        "VALUES ('default', 'Default Pipeline', true, true) RETURNING id")).scalar()
    for code, name, order, category, prob in _DEFAULT_STAGES:
        bind.execute(sa.text(
            "INSERT INTO opportunity_stages "
            "(pipeline_id, code, name, sort_order, category, default_probability, active) "
            "VALUES (:p, :c, :n, :o, :cat, :prob, true)"),
            {"p": pipeline_id, "c": code, "n": name, "o": order, "cat": category, "prob": prob})

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
    op.drop_table("opportunity_work_links")
    op.drop_table("opportunity_activities")
    op.drop_table("opportunity_events")
    op.drop_table("opportunity_participants")
    op.drop_table("opportunities")
    op.drop_table("opportunity_stages")
    op.drop_table("opportunity_pipelines")
    for code, _d, _s, _roles in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
