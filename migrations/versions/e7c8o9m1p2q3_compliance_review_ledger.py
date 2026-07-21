"""Compliance review + decision-evidence ledger (Phase D.7).

Adds a narrowly scoped, human-controlled compliance review layer for governed
Advisor Recommendations:

- ``compliance_reviews``     — one review per governed recommendation snapshot, with a
                               partial-unique guard so there is at most one OPEN review
                               per (recommendation, governing rule, rule version, source).
- ``compliance_decisions``   — the append-only decision ledger (a trigger blocks
                               UPDATE/DELETE); corrections create a NEW row that
                               references the prior via ``supersedes_decision_id``.
- ``reviewer_authorities``   — the authority catalog that establishes whether a
                               principal may make a FINAL compliance decision. Seeded
                               EMPTY on purpose: no authorized reviewer is known, so
                               final approval stays blocked (no reviewer is fabricated).

Also seeds four capabilities (``compliance.review.read/submit/assign/decide``) and
composes them into the existing ``compliance`` role. Capability alone never confers
approval authority — final approval double-gates on capability AND reviewer_authorities.

Additive and reversible. No unrelated table is modified; no review/decision/reviewer
record is backfilled.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7c8o9m1p2q3"
down_revision = "d4c5o6m7d8i9"
branch_labels = None
depends_on = None

_OPEN_STATUSES = (
    "pending_submission", "pending_assignment", "pending_review",
    "blocked_pending_authorized_reviewer",
)
_ALL_STATUSES = _OPEN_STATUSES + (
    "approved", "approved_with_conditions", "returned", "declined", "superseded", "closed",
)
_DECISIONS = ("approved", "approved_with_conditions", "returned", "declined")

NEW_CAPS = {
    "compliance.review.read": ("View the compliance review queue and review detail.", False),
    "compliance.review.submit": ("Submit a governed recommendation for compliance review.", False),
    "compliance.review.assign": ("Assign a compliance reviewer to a review.", True),
    "compliance.review.decide": ("Record a compliance review decision.", True),
}


def upgrade():
    op.create_table(
        "compliance_reviews",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("recommendation_id", sa.Text, nullable=False),
        sa.Column("recommendation_type", sa.Text, nullable=False),
        sa.Column("source_entity_type", sa.Text, nullable=False),
        sa.Column("source_entity_id", sa.BigInteger, nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id", ondelete="SET NULL")),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id", ondelete="SET NULL")),
        sa.Column("governing_rule", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("policy_gate", sa.Text, nullable=False),
        sa.Column("recommendation_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("assigned_reviewer_role", sa.Text),
        sa.Column("assigned_reviewer_name", sa.Text),
        sa.Column("assigned_reviewer_principal_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in _ALL_STATUSES) + ")",
                           name="ck_compliance_reviews_status"),
    )
    op.create_index("ix_compliance_reviews_status", "compliance_reviews", ["status"])
    op.create_index("ix_compliance_reviews_person", "compliance_reviews", ["person_id"])
    op.create_index("ix_compliance_reviews_household", "compliance_reviews", ["household_id"])
    op.create_index("ix_compliance_reviews_recommendation", "compliance_reviews", ["recommendation_id"])
    op.create_index("ix_compliance_reviews_rule", "compliance_reviews", ["governing_rule", "rule_version"])
    # At most one OPEN review per (recommendation, rule, version, source record).
    op.create_index(
        "uq_open_compliance_review", "compliance_reviews",
        ["recommendation_id", "governing_rule", "rule_version", "source_entity_type", "source_entity_id"],
        unique=True,
        postgresql_where=sa.text("status IN (" + ", ".join(f"'{s}'" for s in _OPEN_STATUSES) + ")"),
    )

    op.create_table(
        "compliance_decisions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("compliance_review_id", sa.BigInteger,
                  sa.ForeignKey("compliance_reviews.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("reviewer_role", sa.Text),
        sa.Column("reviewer_name", sa.Text),
        sa.Column("reviewer_principal_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope_reviewed", sa.Text),
        sa.Column("comments", sa.Text),
        sa.Column("exceptions", sa.Text),
        sa.Column("governing_rule", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("supersedes_decision_id", sa.BigInteger,
                  sa.ForeignKey("compliance_decisions.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("decision IN (" + ", ".join(f"'{d}'" for d in _DECISIONS) + ")",
                           name="ck_compliance_decisions_decision"),
    )
    op.create_index("ix_compliance_decisions_review", "compliance_decisions", ["compliance_review_id"])
    # Append-only ledger (same idiom as audit_events / exception_events).
    op.execute(
        "CREATE FUNCTION prevent_compliance_decision_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'compliance_decisions are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER compliance_decisions_immutable BEFORE UPDATE OR DELETE ON compliance_decisions "
        "FOR EACH ROW EXECUTE FUNCTION prevent_compliance_decision_mutation()"
    )

    op.create_table(
        "reviewer_authorities",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("principal_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_role", sa.Text, nullable=False),
        sa.Column("reviewer_name", sa.Text),
        sa.Column("authority_scope", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("effective_date", sa.Date),
        sa.Column("expiration_date", sa.Date),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("source_reference", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_reviewer_authorities_principal", "reviewer_authorities", ["principal_id"])
    op.create_index("ix_reviewer_authorities_status", "reviewer_authorities", ["status"])
    # Seeded EMPTY: no authorized reviewer is known; final approval stays blocked.

    # Capabilities + composition. The "compliance" role is the reviewer role; the
    # "administrator" superuser holds every capability (existing invariant). Holding
    # the decide capability never confers approval authority — final approval also
    # requires a recorded reviewer_authorities row (seeded empty), so it stays blocked.
    bind = op.get_bind()
    role_ids = [
        r for r in (
            bind.execute(sa.text("SELECT id FROM roles WHERE code = 'compliance'")).scalar(),
            bind.execute(sa.text("SELECT id FROM roles WHERE code = 'administrator'")).scalar(),
        ) if r is not None
    ]
    for code, (description, sensitive) in NEW_CAPS.items():
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, :s) RETURNING id"),
                {"c": code, "d": description, "s": sensitive},
            ).scalar()
        for role_id in role_ids:
            exists = bind.execute(
                sa.text("SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                {"r": role_id, "c": cid},
            ).scalar()
            if not exists:
                bind.execute(
                    sa.text("INSERT INTO role_capabilities (role_id, capability_id) VALUES (:r, :c)"),
                    {"r": role_id, "c": cid},
                )


def downgrade():
    bind = op.get_bind()
    for code in NEW_CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
    op.execute("DROP TRIGGER IF EXISTS compliance_decisions_immutable ON compliance_decisions")
    op.execute("DROP FUNCTION IF EXISTS prevent_compliance_decision_mutation()")
    op.drop_table("compliance_decisions")
    op.drop_table("compliance_reviews")
    op.drop_table("reviewer_authorities")
