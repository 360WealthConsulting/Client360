"""Insurance commissions — expected/received ledger, statements, reconciliation (Phase 5).

Release 0.10.0, Phase 5 (NON-REGULATED). Adds the commission ledger and carrier
statement import/reconciliation surface:

- ``insurance_commissions`` — one expected/received row **per producer split**, so a
  split-commission policy credits each producer (and an ``override`` upline) correctly.
- ``insurance_commission_statements`` / ``insurance_commission_statement_lines`` —
  carrier statement import; lines reconcile against expected ledger rows.

Plus two OPERATIONAL exception types (``INS_COMMISSION_VARIANCE``,
``INS_COMMISSION_OUTSTANDING``) so the shared Exception Engine surfaces reconciliation
variance and unpaid-but-due commissions, and the ``insurance.commissions.write``
capability (the read capability was seeded in Phase 0).

This is money movement and reconciliation only — an operational/financial concern. It
makes NO suitability, replacement/1035, licensing, or CE determination and blocks no
lifecycle; those regulated determinations remain behind the AD-5 gate.

Additive and reversible; single Alembic head.
"""
import sqlalchemy as sa
from alembic import op

revision = "b8i9k1l2g3j4"
down_revision = "a7g8i9k0f1h2"
branch_labels = None
depends_on = None

# expected -> reconciled clean; partial/variance = a surfaced reconciliation gap.
COMMISSION_STATUSES = ("expected", "received", "partial", "variance", "written_off", "cancelled")
SCHEDULES = ("first_year", "renewal", "trail", "override", "other")
PRODUCER_ENTITY_TYPES = ("user", "organization")
PRODUCER_ROLES = ("writing_agent", "servicing_agent", "broker_of_record", "override")
STATEMENT_STATUSES = ("imported", "partially_reconciled", "reconciled")
LINE_STATUSES = ("unmatched", "matched", "reconciled")

# (code, category, severity, owner_role, blocks_lifecycle, compliance_visible, name)
# Both are OPERATIONAL: a variance/overdue-payment is a money-reconciliation gap, not a
# compliance conclusion (compliance_visible=False, blocks_lifecycle=False).
INS_EXCEPTION_TYPES = [
    ("INS_COMMISSION_VARIANCE", "operational", "high", "operations", False, False,
     "Commission variance vs expected"),
    ("INS_COMMISSION_OUTSTANDING", "operational", "medium", "operations", False, False,
     "Expected commission outstanding / overdue"),
]
SLA_BY_SEVERITY = {"blocker": 1440, "high": 2880, "medium": 7200, "low": 14400}


def _in(col, values):
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


def upgrade():
    # --- carrier statement import header ------------------------------------
    op.create_table(
        "insurance_commission_statements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("carrier_id", sa.Integer, sa.ForeignKey("relationship_entities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("statement_date", sa.Date, nullable=True),
        sa.Column("reference", sa.String(96), nullable=True),
        sa.Column("stated_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="imported"),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("status", STATEMENT_STATUSES), name="ck_ins_commission_statement_status"),
    )
    op.create_index("ix_ins_commission_statement_carrier", "insurance_commission_statements", ["carrier_id"])

    # --- commission ledger (one row per producer split) ----------------------
    op.create_table(
        "insurance_commissions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False),
        # denormalized policy anchor so the ledger scopes/rolls up without a join
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("relationship_entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("producer_entity_type", sa.String(16), nullable=False),
        sa.Column("producer_entity_id", sa.Integer, nullable=False),
        sa.Column("producer_role", sa.String(24), nullable=False, server_default="writing_agent"),
        sa.Column("split_percentage", sa.Numeric(6, 3), nullable=True),
        sa.Column("schedule", sa.String(16), nullable=False, server_default="first_year"),
        sa.Column("period_label", sa.String(24), nullable=True),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("expected_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("received_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="expected"),
        sa.Column("statement_id", sa.Integer, sa.ForeignKey("insurance_commission_statements.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("status", COMMISSION_STATUSES), name="ck_ins_commission_status"),
        sa.CheckConstraint(_in("schedule", SCHEDULES), name="ck_ins_commission_schedule"),
        sa.CheckConstraint(_in("producer_entity_type", PRODUCER_ENTITY_TYPES), name="ck_ins_commission_producer_type"),
        sa.CheckConstraint(_in("producer_role", PRODUCER_ROLES), name="ck_ins_commission_producer_role"),
    )
    op.create_index("ix_ins_commission_policy", "insurance_commissions", ["policy_id"])
    op.create_index("ix_ins_commission_producer", "insurance_commissions", ["producer_entity_type", "producer_entity_id"])
    op.create_index("ix_ins_commission_status", "insurance_commissions", ["status"])
    op.create_index("ix_ins_commission_statement", "insurance_commissions", ["statement_id"])
    op.create_index("ix_ins_commission_org", "insurance_commissions", ["organization_id"])

    # --- statement lines (reconcile to ledger rows) --------------------------
    op.create_table(
        "insurance_commission_statement_lines",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("statement_id", sa.Integer, sa.ForeignKey("insurance_commission_statements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_number", sa.String(64), nullable=True),
        sa.Column("policy_id", sa.Integer, sa.ForeignKey("insurance_policies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("producer_reference", sa.String(96), nullable=True),
        sa.Column("schedule", sa.String(16), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("matched_commission_id", sa.Integer, sa.ForeignKey("insurance_commissions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="unmatched"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("status", LINE_STATUSES), name="ck_ins_commission_line_status"),
    )
    op.create_index("ix_ins_commission_line_statement", "insurance_commission_statement_lines", ["statement_id"])
    op.create_index("ix_ins_commission_line_policy", "insurance_commission_statement_lines", ["policy_id"])

    # --- capability (read cap was seeded in Phase 0) -------------------------
    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO capabilities (code, description, sensitive) "
        "VALUES ('insurance.commissions.write', 'Manage insurance commissions and reconciliation', false) "
        "ON CONFLICT (code) DO NOTHING"))
    # Grant to the roles that already hold commissions.read or manage the book.
    bind.execute(sa.text(
        "INSERT INTO role_capabilities (role_id, capability_id) "
        "SELECT r.id, c.id FROM roles r CROSS JOIN capabilities c "
        "WHERE c.code = 'insurance.commissions.write' "
        "AND r.code IN ('administrator', 'insurance_agent', 'insurance_operations') "
        "ON CONFLICT DO NOTHING"))

    # --- operational exception types -----------------------------------------
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
    codes = [t[0] for t in INS_EXCEPTION_TYPES]
    bind.execute(sa.text(
        "DELETE FROM exceptions WHERE exception_type_id IN "
        "(SELECT id FROM exception_types WHERE code IN :codes)"
    ).bindparams(sa.bindparam("codes", expanding=True)), {"codes": codes})
    bind.execute(sa.text("DELETE FROM exception_types WHERE code IN :codes")
                 .bindparams(sa.bindparam("codes", expanding=True)), {"codes": codes})
    bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id IN "
                         "(SELECT id FROM capabilities WHERE code = 'insurance.commissions.write')"))
    bind.execute(sa.text("DELETE FROM capabilities WHERE code = 'insurance.commissions.write'"))
    op.drop_table("insurance_commission_statement_lines")
    op.drop_table("insurance_commissions")
    op.drop_table("insurance_commission_statements")
