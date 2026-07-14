from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, JSON, MetaData,
    Numeric, String, Table, Text, UniqueConstraint, func,
)


def define_portfolio_tables(metadata: MetaData):
    custodians = Table(
        "custodians", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(50), nullable=False, unique=True),
        Column("name", String(255), nullable=False),
        Column("active", Boolean, nullable=False, server_default="true"),
    )
    registrations = Table(
        "account_registrations", metadata,
        Column("id", Integer, primary_key=True),
        Column("code", String(100), nullable=False, unique=True),
        Column("name", String(255), nullable=False),
        Column("tax_treatment", String(100)),
        Column("requires_beneficiary", Boolean, nullable=False, server_default="false"),
        Column("retirement_account", Boolean, nullable=False, server_default="false"),
    )
    securities = Table(
        "securities", metadata,
        Column("id", Integer, primary_key=True),
        Column("symbol", String(100)), Column("cusip", String(20)),
        Column("name", String(500), nullable=False),
        Column("security_type", String(100)), Column("asset_class", String(100)),
        UniqueConstraint("symbol", name="uq_security_symbol"),
    )
    import_runs = Table(
        "portfolio_import_runs", metadata,
        Column("id", Integer, primary_key=True),
        Column("custodian_id", Integer, ForeignKey("custodians.id"), nullable=False),
        Column("source_type", String(100), nullable=False),
        Column("source_file", String(1000)), Column("file_hash", String(64)),
        Column("status", String(50), nullable=False), Column("stats", JSON, nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("completed_at", DateTime(timezone=True)),
        UniqueConstraint("custodian_id", "source_type", "file_hash", name="uq_portfolio_import_file"),
    )
    holdings = Table(
        "account_holdings", metadata,
        Column("id", Integer, primary_key=True), Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        Column("security_id", Integer, ForeignKey("securities.id"), nullable=False),
        Column("quantity", Numeric(24, 8), nullable=False, server_default="0"),
        Column("price", Numeric(18, 6)), Column("market_value", Numeric(18, 2), nullable=False, server_default="0"),
        Column("cost_basis", Numeric(18, 2)), Column("unrealized_gain", Numeric(18, 2)),
        Column("as_of_date", Date, nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("account_id", "security_id", name="uq_current_account_security"),
    )
    positions = Table(
        "position_snapshots", metadata,
        Column("id", Integer, primary_key=True), Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        Column("security_id", Integer, ForeignKey("securities.id"), nullable=False),
        Column("as_of_date", Date, nullable=False), Column("quantity", Numeric(24, 8)),
        Column("price", Numeric(18, 6)), Column("market_value", Numeric(18, 2), nullable=False),
        Column("cost_basis", Numeric(18, 2)), Column("asset_class", String(100)),
        UniqueConstraint("account_id", "security_id", "as_of_date", name="uq_position_snapshot"),
    )
    lots = Table(
        "tax_lots", metadata,
        Column("id", Integer, primary_key=True), Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        Column("security_id", Integer, ForeignKey("securities.id"), nullable=False),
        Column("external_lot_id", String(255), nullable=False), Column("acquired_date", Date),
        Column("quantity", Numeric(24, 8)), Column("cost_basis", Numeric(18, 2)), Column("market_value", Numeric(18, 2)),
        UniqueConstraint("account_id", "external_lot_id", name="uq_account_tax_lot"),
    )
    transactions = Table(
        "portfolio_transactions", metadata,
        Column("id", Integer, primary_key=True), Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        Column("external_transaction_id", String(255), nullable=False), Column("transaction_date", Date, nullable=False),
        Column("transaction_type", String(100), nullable=False), Column("amount", Numeric(18, 2)), Column("description", Text),
        UniqueConstraint("account_id", "external_transaction_id", name="uq_account_transaction"),
    )
    snapshots = {}
    for table_name, extra in (
        ("cash_snapshots", [Column("cash_value", Numeric(18, 2), nullable=False)]),
        ("performance_snapshots", [Column("market_value", Numeric(18, 2), nullable=False), Column("net_contributions", Numeric(18, 2)), Column("gain_loss", Numeric(18, 2)), Column("return_percent", Numeric(10, 4))]),
        ("billing_snapshots", [Column("billable_value", Numeric(18, 2), nullable=False), Column("fee_amount", Numeric(18, 2)), Column("billing_rate", Numeric(10, 6))]),
    ):
        snapshots[table_name] = Table(
            table_name, metadata,
            Column("id", Integer, primary_key=True), Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
            Column("as_of_date", Date, nullable=False), *extra,
            UniqueConstraint("account_id", "as_of_date", name=f"uq_{table_name}_account_date"),
        )
    beneficiaries = Table(
        "account_beneficiaries", metadata,
        Column("id", Integer, primary_key=True), Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        Column("beneficiary_name", String(500), nullable=False), Column("beneficiary_type", String(50), nullable=False),
        Column("percentage", Numeric(7, 4)), Column("reviewed_at", Date), Column("active", Boolean, nullable=False, server_default="true"),
        UniqueConstraint("account_id", "beneficiary_name", "beneficiary_type", name="uq_account_beneficiary"),
    )
    household_rollups = Table(
        "household_portfolio_snapshots", metadata,
        Column("id", Integer, primary_key=True), Column("household_id", Integer, ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        Column("as_of_date", Date, nullable=False), Column("total_value", Numeric(18, 2), nullable=False),
        Column("cash_value", Numeric(18, 2), nullable=False), Column("asset_allocation", JSON, nullable=False),
        UniqueConstraint("household_id", "as_of_date", name="uq_household_portfolio_date"),
    )
    return {table.name: table for table in [custodians, registrations, securities, import_runs, holdings, positions, lots, transactions, *snapshots.values(), beneficiaries, household_rollups]}
