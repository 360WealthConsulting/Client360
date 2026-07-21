"""Reviewer authority administration (Phase D.8).

Extends the D.7 ``reviewer_authorities`` catalog with the fields needed to record and
maintain reviewer authority from documented facts, and adds an append-only
``reviewer_authority_events`` ledger for a defensible history. The authority scope
mechanism from D.7 (the ``authority_scope`` jsonb list of governed rule ids and/or
policy-gate categories) is reused unchanged — no competing scope columns are added.

Also seeds two capabilities: ``compliance.authority.read`` (view records) and
``compliance.authority.manage`` (record/maintain factual authority). Holding manage
lets an administrator maintain factual records; it does NOT make them a compliance
reviewer, and it never confers approval authority.

Additive and reversible. The authority catalog is NOT seeded — no reviewer record is
fabricated (final regulated approval stays blocked until factual authority exists).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f8a9u1t2h3r4"
down_revision = "e7c8o9m1p2q3"
branch_labels = None
depends_on = None

_STATUSES = ("draft", "active", "suspended", "expired", "revoked", "superseded")

NEW_CAPS = {
    "compliance.authority.read": ("View reviewer-authority records.", False),
    "compliance.authority.manage": ("Record and maintain reviewer-authority records.", True),
}


def upgrade():
    op.add_column("reviewer_authorities", sa.Column("evidence_description", sa.Text))
    op.add_column("reviewer_authorities",
                  sa.Column("recorded_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("reviewer_authorities",
                  sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))
    op.add_column("reviewer_authorities", sa.Column("suspended_at", sa.DateTime(timezone=True)))
    op.add_column("reviewer_authorities", sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.add_column("reviewer_authorities", sa.Column("revocation_reason", sa.Text))
    op.add_column("reviewer_authorities",
                  sa.Column("supersedes_authority_id", sa.BigInteger,
                            sa.ForeignKey("reviewer_authorities.id", ondelete="RESTRICT")))
    op.alter_column("reviewer_authorities", "status", server_default="draft")
    op.create_check_constraint(
        "ck_reviewer_authorities_status", "reviewer_authorities",
        "status IN (" + ", ".join(f"'{s}'" for s in _STATUSES) + ")")

    op.create_table(
        "reviewer_authority_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("reviewer_authority_id", sa.BigInteger,
                  sa.ForeignKey("reviewer_authorities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("prior_status", sa.Text),
        sa.Column("new_status", sa.Text),
        sa.Column("actor_principal_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("evidence_snapshot", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_reviewer_authority_events_authority", "reviewer_authority_events",
                    ["reviewer_authority_id"])
    op.execute(
        "CREATE FUNCTION prevent_reviewer_authority_event_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'reviewer_authority_events are append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER reviewer_authority_events_immutable BEFORE UPDATE OR DELETE "
        "ON reviewer_authority_events FOR EACH ROW "
        "EXECUTE FUNCTION prevent_reviewer_authority_event_mutation()"
    )

    bind = op.get_bind()
    admin_role = bind.execute(sa.text("SELECT id FROM roles WHERE code = 'administrator'")).scalar()
    compliance_role = bind.execute(sa.text("SELECT id FROM roles WHERE code = 'compliance'")).scalar()
    grants = {
        "compliance.authority.read": [r for r in (admin_role, compliance_role) if r is not None],
        "compliance.authority.manage": [r for r in (admin_role,) if r is not None],
    }
    for code, (description, sensitive) in NEW_CAPS.items():
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(
                sa.text("INSERT INTO capabilities (code, description, sensitive) "
                        "VALUES (:c, :d, :s) RETURNING id"),
                {"c": code, "d": description, "s": sensitive},
            ).scalar()
        for role_id in grants[code]:
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
    op.execute("DROP TRIGGER IF EXISTS reviewer_authority_events_immutable ON reviewer_authority_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_reviewer_authority_event_mutation()")
    op.drop_table("reviewer_authority_events")
    op.drop_constraint("ck_reviewer_authorities_status", "reviewer_authorities", type_="check")
    op.alter_column("reviewer_authorities", "status", server_default="active")
    for col in ("supersedes_authority_id", "revocation_reason", "revoked_at", "suspended_at",
                "recorded_at", "recorded_by", "evidence_description"):
        op.drop_column("reviewer_authorities", col)
