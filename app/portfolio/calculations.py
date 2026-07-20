from collections import defaultdict
from decimal import Decimal

ZERO = Decimal("0")

def calculate_allocation(holdings):
    totals = defaultdict(lambda: ZERO)
    for item in holdings:
        totals[item.get("asset_class") or "Other"] += Decimal(item.get("market_value") or 0)
    total = sum(totals.values(), ZERO)
    return {key: {"value": value, "percent": value / total * 100 if total else ZERO} for key, value in sorted(totals.items())}

def aggregate_portfolio(account_rows, holding_rows):
    total = sum((Decimal(row.get("total_value") or 0) for row in account_rows), ZERO)
    cash = sum((Decimal(row.get("cash_value") or 0) for row in account_rows), ZERO)
    holdings = sorted(holding_rows, key=lambda row: Decimal(row.get("market_value") or 0), reverse=True)
    return {"total_aum": total, "cash": cash, "cash_percent": cash / total * 100 if total else ZERO, "accounts": account_rows, "holdings": holdings, "largest_holdings": holdings[:10], "asset_allocation": calculate_allocation(holdings), "largest_position_percent": Decimal(holdings[0].get("market_value") or 0) / total * 100 if holdings and total else ZERO}
