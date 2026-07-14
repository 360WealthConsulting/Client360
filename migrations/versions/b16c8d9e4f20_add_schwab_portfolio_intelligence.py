"""add Schwab portfolio intelligence

Revision ID: b16c8d9e4f20
Revises: 753c04edab33
"""
from alembic import op
import sqlalchemy as sa
from app.database.schema import metadata

revision = "b16c8d9e4f20"
down_revision = "753c04edab33"
branch_labels = None
depends_on = None

PORTFOLIO_TABLES = [
    "custodians", "account_registrations", "securities", "portfolio_import_runs",
    "account_holdings", "position_snapshots", "tax_lots", "portfolio_transactions",
    "cash_snapshots", "performance_snapshots", "billing_snapshots",
    "account_beneficiaries", "household_portfolio_snapshots",
]

def upgrade():
    bind = op.get_bind()
    for name in PORTFOLIO_TABLES[:3]:
        metadata.tables[name].create(bind, checkfirst=True)
    op.add_column("accounts", sa.Column("custodian_id", sa.Integer(), nullable=True))
    op.add_column("accounts", sa.Column("registration_id", sa.Integer(), nullable=True))
    op.add_column("accounts", sa.Column("last_imported_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("accounts", sa.Column("last_review_date", sa.Date(), nullable=True))
    op.create_foreign_key("fk_accounts_custodian", "accounts", "custodians", ["custodian_id"], ["id"])
    op.create_foreign_key("fk_accounts_registration", "accounts", "account_registrations", ["registration_id"], ["id"])
    for name in PORTFOLIO_TABLES[3:]:
        metadata.tables[name].create(bind, checkfirst=True)
    bind.execute(metadata.tables["custodians"].insert().values(code="schwab", name="Charles Schwab"))
    for code, name, tax, beneficiary, retirement in [
        ("individual", "Individual", "taxable", False, False),
        ("joint", "Joint", "taxable", False, False),
        ("traditional_ira", "Traditional IRA", "tax_deferred", True, True),
        ("roth_ira", "Roth IRA", "tax_free", True, True),
        ("trust", "Trust", "varies", False, False),
        ("llc", "LLC", "varies", False, False),
    ]:
        bind.execute(metadata.tables["account_registrations"].insert().values(code=code, name=name, tax_treatment=tax, requires_beneficiary=beneficiary, retirement_account=retirement))

def downgrade():
    bind = op.get_bind()
    for name in reversed(PORTFOLIO_TABLES[3:]): metadata.tables[name].drop(bind, checkfirst=True)
    op.drop_constraint("fk_accounts_registration", "accounts", type_="foreignkey")
    op.drop_constraint("fk_accounts_custodian", "accounts", type_="foreignkey")
    for column in ("last_review_date", "last_imported_at", "registration_id", "custodian_id"): op.drop_column("accounts", column)
    for name in reversed(PORTFOLIO_TABLES[:3]): metadata.tables[name].drop(bind, checkfirst=True)
