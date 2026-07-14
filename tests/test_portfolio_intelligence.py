from datetime import date
from decimal import Decimal
from pathlib import Path
from app.portfolio.adapters.schwab_csv import SchwabCsvAdapter, decimal_value
from app.services.advisor_ai import build_advisor_recommendations
from app.portfolio.calculations import aggregate_portfolio, calculate_allocation
from app.portfolio.matching import normalize_email

def test_schwab_account_master_adapter(tmp_path: Path):
    source = tmp_path / "AccountMaster.csv"
    source.write_text('Name,Account Number,Registration,Cash Available,Total Value,Status\nClient One,1234,Roth IRA,"$20,000","$100,000",Open\n')
    account = SchwabCsvAdapter().read(source).accounts[0]
    assert account.account_number == "1234"
    assert account.registration == "Roth IRA"

def test_position_adapter_and_allocation(tmp_path: Path):
    source = tmp_path / "Positions.csv"
    source.write_text("Account Number,Symbol,Description,Quantity,Price,Market Value,Cost Basis,Asset Class\n1234,ABC,ABC Corp,10,50,500,300,Equity\n")
    position = SchwabCsvAdapter().read(source).positions[0]
    assert position.market_value == Decimal("500")
    assert calculate_allocation([position.__dict__])["Equity"]["percent"] == 100

def test_household_aggregation_and_performance_inputs():
    result = aggregate_portfolio([{"total_value": Decimal("100"), "cash_value": Decimal("20")}, {"total_value": Decimal("300"), "cash_value": Decimal("10")}], [{"market_value": Decimal("250"), "asset_class": "Equity"}])
    assert result["total_aum"] == 400
    assert result["cash"] == 30
    assert result["largest_position_percent"] == Decimal("62.5")

def test_normalized_email_matching_key():
    assert normalize_email(" Client@Example.COM ") == "client@example.com"

def test_money_parser_and_idempotency_key_inputs():
    assert decimal_value("($1,234.50)") == Decimal("-1234.50")

def test_portfolio_advisor_recommendations():
    recommendations = build_advisor_recommendations({"document_count": 1, "activity_count": 1}, {"cash_percent": 20, "largest_position_percent": 30, "accounts_requiring_beneficiary": 1, "beneficiary_count": 0})
    assert any("cash" in item for item in recommendations)
    assert any("concentrated" in item for item in recommendations)
    assert any("beneficiary" in item for item in recommendations)
