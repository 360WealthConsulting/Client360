"""Insurance in-force servicing — policy reviews as a first-class state machine.

Release 0.10.0, Phase 3 (NON-REGULATED). Adds ``insurance_policy_reviews`` — a
servicing review lifecycle (due → scheduled → completed / deferred / overdue /
cancelled) anchored to a policy or case, plus one insurance exception type
(``INS_REVIEW_OVERDUE``) so the obligation calendar can raise an operational
exception through the shared Exception Engine (no second engine, no second
history model).

This is operational servicing only. It contains NO suitability, replacement/1035,
licensing, or CE determination: ``review_type`` is a scheduling category and the
review's outcome is a free-text servicing note, never a compliance conclusion.
Those remain behind the AD-5 compliance gate.

Additive and reversible; single Alembic head.
"""
import sqlalchemy as sa
from alembic import op

revision = "z6f7h8j9e0g1"
down_revision = "y5e6g7i8d9f0"
branch_labels = None
depends_on = None

REVIEW_TYPES = ("annual", "inforce", "servicing")
REVIEW_STATUSES = ("due", "scheduled", "in_progress", "completed", "deferred", "overdue", "cancelled")

# (code, category, severity, owner_role, blocks_lifecycle, compliance_visible, name)
# An overdue servicing review is an OPERATIONAL lapse, not a compliance determination
# (compliance_visible=False, blocks_lifecycle=False). SLA derives from severity.
INS_EXCEPTION_TYPES = [
    ("INS_REVIEW_OVERDUE", "workflow", "medium", "operations", False, False, "Insurance policy review overdue"),
]
SLA_BY_SEVERITY = {"blocker": 1440, "high": 2880, "medium": 7200, "low": 14400}


def _in(col, values):
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


def upgrade():
    op.create_table(
        "insurance_policy_reviews",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=True),
        sa.Column("case_id", sa.Integer, sa.ForeignKey("insurance_cases.id", ondelete="CASCADE"), nullable=True),
        sa.Column("review_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="due"),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("scheduled_date", sa.Date, nullable=True),
        sa.Column("completed_date", sa.Date, nullable=True),
        sa.Column("next_review_date", sa.Date, nullable=True),
        sa.Column("outcome_note", sa.Text, nullable=True),
        sa.Column("reviewer_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("materialization_key", sa.String(255), nullable=True, unique=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("review_type", REVIEW_TYPES), name="ck_insurance_policy_reviews_type"),
        sa.CheckConstraint(_in("status", REVIEW_STATUSES), name="ck_insurance_policy_reviews_status"),
        sa.CheckConstraint("policy_id IS NOT NULL OR case_id IS NOT NULL",
                           name="ck_insurance_policy_reviews_anchor"),
    )
    op.create_index("ix_insurance_policy_reviews_policy_id", "insurance_policy_reviews", ["policy_id"])
    op.create_index("ix_insurance_policy_reviews_case_id", "insurance_policy_reviews", ["case_id"])
    op.create_index("ix_insurance_policy_reviews_status", "insurance_policy_reviews", ["status"])
    op.create_index("ix_insurance_policy_reviews_due_date", "insurance_policy_reviews", ["due_date"])

    # Seed the insurance exception type used by the obligation calendar's overdue detector.
    op.bulk_insert(
        sa.table("exception_types", sa.column("domain"), sa.column("code"), sa.column("category"),
                 sa.column("name"), sa.column("default_severity"), sa.column("default_owner_role"),
                 sa.column("sla_minutes"), sa.column("blocks_lifecycle"), sa.column("compliance_visible")),
        [{"domain": "insurance", "code": code, "category": category, "name": name,
          "default_severity": severity, "default_owner_role": owner,
          "sla_minutes": SLA_BY_SEVERITY[severity], "blocks_lifecycle": blocks, "compliance_visible": comp}
         for (code, category, severity, owner, blocks, comp, name) in INS_EXCEPTION_TYPES],
    )


def downgrade():
    bind = op.get_bind()
    codes = tuple(t[0] for t in INS_EXCEPTION_TYPES)
    # Remove any exceptions raised against these types before dropping the types (FK).
    bind.execute(sa.text(
        "DELETE FROM exceptions WHERE exception_type_id IN "
        "(SELECT id FROM exception_types WHERE code IN :codes)"
    ).bindparams(sa.bindparam("codes", expanding=True)), {"codes": list(codes)})
    bind.execute(sa.text("DELETE FROM exception_types WHERE code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": list(codes)})
    op.drop_table("insurance_policy_reviews")
