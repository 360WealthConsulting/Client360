"""insurance requirements + underwriting-status tracking (Release 0.10.0, Phase 2, non-regulated)

Operational new-business plumbing behind the AD-5 compliance gate line: a requirement
is an outstanding checklist item (APS, medical exam, signed application/illustration,
carrier form), tracked through requested -> satisfied. Underwriting status is a tracked
field recording the carrier's status — NOT an automated underwriting decision. Neither
contains suitability, replacement/1035, licensing, or CE determination.

Additive and reversible; single Alembic head.
"""
import sqlalchemy as sa
from alembic import op

revision = "y5e6g7i8d9f0"
down_revision = "x4d5f6h7c8e9"
branch_labels = None
depends_on = None

REQUIREMENT_STATUS = ("requested", "received", "waived", "satisfied", "cancelled")


def _check(col, allowed):
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in allowed) + ")"


def upgrade():
    op.create_table(
        "insurance_requirements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("insurance_cases.id", ondelete="CASCADE")),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE")),
        sa.Column("requirement_type", sa.String(48), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="requested"),
        sa.Column("description", sa.String(500)),
        # document collection reuses the shared documents table (no new doc store)
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("requested_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("requested_date", sa.Date),
        sa.Column("due_date", sa.Date),
        sa.Column("satisfied_date", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_check("status", REQUIREMENT_STATUS), name="ck_ins_requirement_status"),
        sa.CheckConstraint("case_id IS NOT NULL OR policy_id IS NOT NULL", name="ck_ins_requirement_anchor"),
    )
    op.create_index("ix_ins_requirement_case", "insurance_requirements", ["case_id"])
    op.create_index("ix_ins_requirement_policy", "insurance_requirements", ["policy_id"])

    # Underwriting status recorded from carrier communication — a tracked field, not a
    # decision the platform makes. Free-form String so no decision vocabulary is implied.
    op.add_column("insurance_policies", sa.Column("underwriting_status", sa.String(48)))


def downgrade():
    op.drop_column("insurance_policies", "underwriting_status")
    op.drop_table("insurance_requirements")
