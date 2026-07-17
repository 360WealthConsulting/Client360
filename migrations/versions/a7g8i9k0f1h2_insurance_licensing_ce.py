"""Insurance producer licensing & continuing-education RECORDS (Phase 4, non-regulated).

Release 0.10.0, Phase 4. Adds ``insurance_licenses`` and ``insurance_ce_records`` —
firm-internal producer (user) licensing and CE **records**, plus two date-driven
exception types (``INS_LICENSE_EXPIRING``, ``INS_CE_PERIOD_ENDING``) so the shared
Exception Engine can raise operational expiry reminders (no second engine, no second
history model).

This is data capture + calendar reminders only. It contains NO licensing *validation*
(whether a producer is licensed to sell a product in a state) and NO CE *determination*
(whether a CE requirement is satisfied), and it blocks nothing. Those regulated
determinations remain behind the AD-5 compliance gate.

Additive and reversible; single Alembic head.
"""
import sqlalchemy as sa
from alembic import op

revision = "a7g8i9k0f1h2"
down_revision = "z6f7h8j9e0g1"
branch_labels = None
depends_on = None

LICENSE_STATUSES = ("active", "inactive", "expired", "suspended", "pending")
CE_STATUSES = ("in_progress", "completed", "overdue")

# (code, category, severity, owner_role, blocks_lifecycle, compliance_visible, name)
# Date-driven expiry reminders are OPERATIONAL (compliance_visible=False, blocks_lifecycle
# =False) — they flag an upcoming date, they do not conclude licensing/CE compliance.
INS_EXCEPTION_TYPES = [
    ("INS_LICENSE_EXPIRING", "workflow", "high", "operations", False, False, "Producer license expiring"),
    ("INS_CE_PERIOD_ENDING", "workflow", "medium", "operations", False, False, "Continuing-education period ending"),
]
SLA_BY_SEVERITY = {"blocker": 1440, "high": 2880, "medium": 7200, "low": 14400}


def _in(col, values):
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


def upgrade():
    op.create_table(
        "insurance_licenses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("producer_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("license_number", sa.String(64), nullable=True),
        sa.Column("npn", sa.String(32), nullable=True),
        sa.Column("lines", sa.JSON, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("issue_date", sa.Date, nullable=True),
        sa.Column("expiry_date", sa.Date, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("status", LICENSE_STATUSES), name="ck_insurance_licenses_status"),
        sa.UniqueConstraint("producer_user_id", "state", "license_number", name="uq_insurance_licenses"),
    )
    op.create_index("ix_insurance_licenses_producer", "insurance_licenses", ["producer_user_id"])
    op.create_index("ix_insurance_licenses_expiry", "insurance_licenses", ["expiry_date"])

    op.create_table(
        "insurance_ce_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("producer_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(2), nullable=True),
        sa.Column("period_start", sa.Date, nullable=True),
        sa.Column("period_end", sa.Date, nullable=True),
        sa.Column("credits_required", sa.Numeric(6, 2), nullable=True),
        sa.Column("credits_completed", sa.Numeric(6, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("status", CE_STATUSES), name="ck_insurance_ce_records_status"),
    )
    op.create_index("ix_insurance_ce_records_producer", "insurance_ce_records", ["producer_user_id"])
    op.create_index("ix_insurance_ce_records_period_end", "insurance_ce_records", ["period_end"])

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
    bind.execute(sa.text(
        "DELETE FROM exceptions WHERE exception_type_id IN "
        "(SELECT id FROM exception_types WHERE code IN :codes)"
    ).bindparams(sa.bindparam("codes", expanding=True)), {"codes": list(codes)})
    bind.execute(sa.text("DELETE FROM exception_types WHERE code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": list(codes)})
    op.drop_table("insurance_ce_records")
    op.drop_table("insurance_licenses")
