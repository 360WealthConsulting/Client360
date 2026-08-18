"""Billing & Invoicing MVP — service agreements, schedules, invoices, line items, payments.

Additive + reversible. Monetary amounts are integer minor units (USD cents); no floats, single currency.
Reuses existing subjects (person/household/organization ids, same convention as the feature framework
and communication hub), ``service_lines``, ``engagements``, and ``tax_engagements`` (optional links) —
no per-service columns. Seeds two dedicated capabilities (billing.read / billing.write) granted to the
administrator profile; other roles receive them through the existing role/capability administration.

Explicitly NOT modeled: card/ACH instruments or credentials (a payment stores only a processor
``external_ref`` + status), general ledger, reconciliation, autopay. Balances are DERIVED from settled
payments; past-due is DERIVED from due_date — neither is stored.
"""
import sqlalchemy as sa
from alembic import op

revision = "billing01"
down_revision = "commhub01"
branch_labels = None
depends_on = None

_CAPS = (("billing.read", "View client billing: service agreements, invoices, balances, payments."),
         ("billing.write", "Manage client billing: agreements, invoices, line items, payments."))
_ROLES = ("administrator",)


def _seed_capabilities(bind):
    for code, desc in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is None:
            cid = bind.execute(sa.text(
                "INSERT INTO capabilities (code, description, sensitive) VALUES (:c, :d, false) RETURNING id"),
                {"c": code, "d": desc}).scalar()
        for role_code in _ROLES:
            rid = bind.execute(sa.text("SELECT id FROM roles WHERE code = :r"), {"r": role_code}).scalar()
            if rid is None:
                continue
            if not bind.execute(sa.text(
                    "SELECT 1 FROM role_capabilities WHERE role_id = :r AND capability_id = :c"),
                    {"r": rid, "c": cid}).scalar():
                bind.execute(sa.text(
                    "INSERT INTO role_capabilities (role_id, capability_id) VALUES (:r, :c)"),
                    {"r": rid, "c": cid})


def upgrade():
    _seed_capabilities(op.get_bind())

    op.create_table(
        "service_agreements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bill_to_type", sa.String(20), nullable=False),        # person | household | organization
        sa.Column("bill_to_id", sa.Integer(), nullable=False),
        sa.Column("service_line_id", sa.Integer(), sa.ForeignKey("service_lines.id", ondelete="SET NULL")),
        sa.Column("engagement_id", sa.Integer(), sa.ForeignKey("engagements.id", ondelete="SET NULL")),
        sa.Column("tax_engagement_id", sa.Integer(),
                  sa.ForeignKey("tax_engagements.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),  # active|paused|ended
        sa.Column("default_amount_cents", sa.Integer()),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date", sa.Date()),
        sa.Column("metadata", sa.JSON()),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_service_agreements_subject", "service_agreements", ["bill_to_type", "bill_to_id"])

    op.create_table(
        "billing_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agreement_id", sa.Integer(),
                  sa.ForeignKey("service_agreements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),           # monthly | annual | one_time
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("anchor_day", sa.Integer()),
        sa.Column("next_run_on", sa.Date()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_period_key", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_billing_schedules_agreement", "billing_schedules", ["agreement_id"])

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bill_to_type", sa.String(20), nullable=False),
        sa.Column("bill_to_id", sa.Integer(), nullable=False),
        sa.Column("agreement_id", sa.Integer(),
                  sa.ForeignKey("service_agreements.id", ondelete="SET NULL")),
        sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("billing_schedules.id", ondelete="SET NULL")),
        sa.Column("period_key", sa.String(40)),                          # recurring idempotency
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),  # draft|issued|paid|partial|void
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credit_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issue_date", sa.Date()),
        sa.Column("due_date", sa.Date()),
        sa.Column("pdf_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("metadata", sa.JSON()),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("issued_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("voided_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("number", name="uq_invoice_number"),
        sa.UniqueConstraint("schedule_id", "period_key", name="uq_invoice_schedule_period"),
    )
    op.create_index("ix_invoices_subject", "invoices", ["bill_to_type", "bill_to_id"])

    op.create_table(
        "invoice_line_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("agreement_id", sa.Integer(),
                  sa.ForeignKey("service_agreements.id", ondelete="SET NULL")),
        sa.Column("service_line_id", sa.Integer(), sa.ForeignKey("service_lines.id", ondelete="SET NULL")),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_amount_cents", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="fee"),  # fee|adjustment|credit
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_invoice_line_items_invoice", "invoice_line_items", ["invoice_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="SET NULL")),
        sa.Column("bill_to_type", sa.String(20), nullable=False),
        sa.Column("bill_to_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("method", sa.String(20), nullable=False, server_default="manual"),  # manual|check|ach|card
        sa.Column("external_ref", sa.String(120)),                       # processor reference only (no PAN)
        sa.Column("status", sa.String(20), nullable=False, server_default="settled"),  # recorded|settled|failed
        sa.Column("received_on", sa.Date()),
        sa.Column("recorded_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("metadata", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_payments_subject", "payments", ["bill_to_type", "bill_to_id"])
    op.create_index("ix_payments_invoice", "payments", ["invoice_id"])


def downgrade():
    op.drop_table("payments")
    op.drop_index("ix_invoice_line_items_invoice", table_name="invoice_line_items")
    op.drop_table("invoice_line_items")
    op.drop_index("ix_invoices_subject", table_name="invoices")
    op.drop_table("invoices")
    op.drop_index("ix_billing_schedules_agreement", table_name="billing_schedules")
    op.drop_table("billing_schedules")
    op.drop_index("ix_service_agreements_subject", table_name="service_agreements")
    op.drop_table("service_agreements")
    bind = op.get_bind()
    for code, _ in _CAPS:
        cid = bind.execute(sa.text("SELECT id FROM capabilities WHERE code = :c"), {"c": code}).scalar()
        if cid is not None:
            bind.execute(sa.text("DELETE FROM role_capabilities WHERE capability_id = :c"), {"c": cid})
            bind.execute(sa.text("DELETE FROM capabilities WHERE id = :c"), {"c": cid})
