"""Business Owner Planning Workspace (Phase D.12).

Adds the single new persistence justified by the pre-implementation audit — the
``business_planning_profiles`` table — plus the three capabilities that gate the
workspace (``business_owner.read`` / ``.update`` / ``.planning_update``).

The Business Owner Planning Workspace is a COMPOSITION layer: it reads existing
authoritative domains (organizations/ownership, tax, retirement, benefits, insurance,
Advisor Intelligence, Advisor Work, Activity Timeline, Compliance, Annual Review) and
never mutates them. The ONLY facts it needs to persist are business SUCCESSION,
CONTINUITY, BUY-SELL, VALUATION, and KEY-PERSON planning — which the audit proved have
no authoritative home anywhere in the schema (only a ``buy_sell_agreement`` relationship
label + an advisor-AI text nudge exist). This table holds exactly those facts, keyed 1:1
to a business entity (``relationship_entities`` of entity_type ``business``). It duplicates
NO business/ownership/insurance/retirement/benefits/tax/work/compliance/annual-review data.

Profiles are MUTABLE advisor/client-reported planning records (edited in place), so this
is NOT an append-only ledger. Status fields use a controlled vocabulary (no free strings).
No backfill — there is no source of truth to backfill from (that would fabricate facts);
data is prospective only. Additive and reversible.
"""
import sqlalchemy as sa
from alembic import op

revision = "j0b1u2s3o4w5"
down_revision = "i9a1n2r3e4v5"
branch_labels = None
depends_on = None

# Controlled planning-status vocabulary (no unrestricted strings).
_STATUS_VOCAB = ("unknown", "not_started", "in_progress", "documented",
                 "review_needed", "complete", "not_applicable")
_STATUS_SQL = "('" + "','".join(_STATUS_VOCAB) + "')"
_SOURCE_VOCAB = ("advisor_entered", "client_reported", "document_derived")
_SOURCE_SQL = "('" + "','".join(_SOURCE_VOCAB) + "')"

_STATUS_COLS = ("succession_plan_status", "buy_sell_status",
                "continuity_plan_status", "key_person_risk_status")

_CAPS = (
    ("business_owner.read", "View the business owner planning workspace.",
     ("administrator", "advisor", "operations")),
    ("business_owner.update", "Update business owner workspace records.",
     ("administrator", "advisor")),
    ("business_owner.planning_update", "Update business succession / continuity planning facts.",
     ("administrator", "advisor")),
)


def upgrade():
    bind = op.get_bind()
    cols = [
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("successor_person_id", sa.Integer,
                  sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("emergency_contact_person_id", sa.Integer,
                  sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("buy_sell_reviewed_at", sa.Date),
        sa.Column("valuation_amount", sa.Numeric(16, 2)),
        sa.Column("valuation_as_of", sa.Date),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("source_type", sa.Text, nullable=False, server_default="advisor_entered"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("source_type IN " + _SOURCE_SQL,
                           name="ck_business_planning_source_type"),
    ]
    for name in _STATUS_COLS:
        cols.append(sa.Column(name, sa.Text, nullable=False, server_default="unknown"))
        cols.append(sa.CheckConstraint(name + " IN " + _STATUS_SQL,
                                       name="ck_business_planning_" + name))
    op.create_table("business_planning_profiles", *cols)
    op.create_index("ix_business_planning_profiles_business_id",
                    "business_planning_profiles", ["business_id"], unique=True)

    for code, description, roles in _CAPS:
        cid = bind.execute(
            sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, false) RETURNING id"),
                {"c": code, "d": description}).scalar()
        for role_code in roles:
            role_id = bind.execute(
                sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
            if role_id is None:
                continue
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities "
                        "WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid}).scalar()
            if not exists:
                bind.execute(
                    sa.text("INSERT INTO role_capabilities (role_id, capability_id) "
                            "VALUES (:r, :c)"), {"r": role_id, "c": cid})


def downgrade():
    bind = op.get_bind()
    op.drop_index("ix_business_planning_profiles_business_id",
                  table_name="business_planning_profiles")
    op.drop_table("business_planning_profiles")
    for code, _description, _roles in _CAPS:
        cid = bind.execute(
            sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"),
                         {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
