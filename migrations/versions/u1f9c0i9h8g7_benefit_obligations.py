"""benefit compliance & renewal obligations (Release 0.9.11, Phase 5)

Minimal, proportional obligation model (ADR-18 §17A): a reference ``benefit_obligation_templates``
(definitions, no dates) and instantiated ``benefit_obligations`` (Organization-specific, with
actual dates). Everything else reuses Engagements, Work Management, Documents, Timeline, Audit,
and the Exception Engine — no second task/reminder/assignment/exception system.

Additive and reversible; single Alembic head. Seeds a small set of standard templates only.
"""
from alembic import op
import sqlalchemy as sa

revision = "u1f9c0i9h8g7"
down_revision = "t0e8b9h8g7f6"
branch_labels = None
depends_on = None

APPLIES_TO = ("organization", "plan", "plan_year", "engagement")
RECURRENCE = ("one_time", "annual", "none")
STATUS = ("scheduled", "in_progress", "completed", "cancelled", "waived")
SOURCE = ("manual", "template", "renewal", "system")

# (code, name, obligation_type, service_line, applies_to, warn_days, recurrence, role)
TEMPLATES = [
    ("form_5500", "Form 5500 filing", "form_5500", "retirement", "plan_year", 60, "annual", "renewal_owner"),
    ("fiduciary_review", "Annual fiduciary review", "fiduciary_review", "retirement", "plan", 30, "annual", "renewal_owner"),
    ("nondiscrimination_testing", "Nondiscrimination testing", "nondiscrimination_testing", "retirement", "plan_year", 45, "annual", "renewal_owner"),
    ("safe_harbor_notice", "Safe harbor notice", "safe_harbor_notice", "retirement", "plan_year", 30, "annual", "renewal_owner"),
    ("qdia_notice", "QDIA notice", "qdia_notice", "retirement", "plan_year", 30, "annual", "renewal_owner"),
    ("auto_enrollment_notice", "Automatic enrollment notice", "auto_enrollment_notice", "retirement", "plan_year", 30, "annual", "renewal_owner"),
    ("fee_disclosure", "Participant fee disclosure", "fee_disclosure", "retirement", "plan_year", 30, "annual", "renewal_owner"),
    ("plan_amendment", "Plan amendment / restatement", "plan_amendment", "retirement", "plan", 30, "one_time", "renewal_owner"),
    ("benefit_renewal", "Benefit renewal", "renewal", "benefits", "plan", 90, "annual", "renewal_owner"),
    ("census_due", "Census due", "census_due", "benefits", "engagement", 14, "annual", "benefits_consultant"),
    ("open_enrollment", "Open enrollment", "open_enrollment_ends", "benefits", "plan_year", 7, "annual", "benefits_consultant"),
    ("spd_delivery", "SPD delivery", "spd_delivery", "benefits", "plan", 0, "one_time", "benefits_consultant"),
    ("sbc_delivery", "SBC delivery", "sbc_delivery", "benefits", "plan_year", 0, "annual", "benefits_consultant"),
]


def _check(col, allowed):
    return f"{col} IN (" + ", ".join(f"'{v}'" for v in allowed) + ")"


def upgrade():
    op.create_table(
        "benefit_obligation_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("obligation_type", sa.String(50), nullable=False),
        sa.Column("service_line", sa.String(20), nullable=False),
        sa.Column("applies_to", sa.String(20), nullable=False),
        sa.Column("default_warning_days", sa.Integer),
        sa.Column("recurrence", sa.String(20), nullable=False, server_default="annual"),
        sa.Column("default_responsible_role", sa.String(60)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("applies_to", APPLIES_TO), name="ck_obligation_tmpl_applies"),
        sa.CheckConstraint(_check("recurrence", RECURRENCE), name="ck_obligation_tmpl_recurrence"),
    )

    op.create_table(
        "benefit_obligations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer,
                  sa.ForeignKey("relationship_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_line_id", sa.Integer, sa.ForeignKey("service_lines.id")),
        sa.Column("engagement_id", sa.Integer, sa.ForeignKey("engagements.id", ondelete="SET NULL")),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("benefit_plans.id", ondelete="CASCADE")),
        sa.Column("plan_year_id", sa.Integer, sa.ForeignKey("benefit_plan_years.id", ondelete="CASCADE")),
        sa.Column("template_id", sa.Integer, sa.ForeignKey("benefit_obligation_templates.id")),
        sa.Column("obligation_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("warning_days", sa.Integer),
        sa.Column("recurrence", sa.String(20), nullable=False, server_default="one_time"),
        sa.Column("responsible_role", sa.String(60)),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("completed_date", sa.Date),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("evidence_document_id", sa.Integer, sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("notes", sa.Text),
        sa.Column("materialization_key", sa.String(255), unique=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(_check("recurrence", RECURRENCE), name="ck_obligation_recurrence"),
        sa.CheckConstraint(_check("status", STATUS), name="ck_obligation_status"),
        sa.CheckConstraint(_check("source", SOURCE), name="ck_obligation_source"),
    )
    op.create_index("ix_benefit_obligations_org", "benefit_obligations", ["organization_id"])
    op.create_index("ix_benefit_obligations_due", "benefit_obligations", ["status", "due_date"])
    op.create_index("ix_benefit_obligations_plan_year", "benefit_obligations", ["plan_year_id"])

    op.bulk_insert(
        sa.table("benefit_obligation_templates", sa.column("code"), sa.column("name"),
                 sa.column("obligation_type"), sa.column("service_line"), sa.column("applies_to"),
                 sa.column("default_warning_days"), sa.column("recurrence"), sa.column("default_responsible_role")),
        [{"code": c, "name": n, "obligation_type": t, "service_line": sl, "applies_to": ap,
          "default_warning_days": w, "recurrence": r, "default_responsible_role": role}
         for (c, n, t, sl, ap, w, r, role) in TEMPLATES])


def downgrade():
    op.drop_table("benefit_obligations")
    op.drop_table("benefit_obligation_templates")
