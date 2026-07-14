import csv
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from app.portfolio.models import AccountRecord, PortfolioBatch, PositionRecord, SnapshotRecord, TransactionRecord

def normalize_header(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())

def decimal_value(value):
    cleaned = re.sub(r"[^0-9.()-]", "", str(value or "0"))
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return Decimal(cleaned or "0")
    except InvalidOperation:
        return Decimal("0")

def date_value(value, default=None):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime((value or "").strip(), fmt).date()
        except ValueError:
            pass
    return default

def pick(row, *names):
    return next((row.get(normalize_header(n), "") for n in names if row.get(normalize_header(n))), "")

class SchwabCsvAdapter:
    """Converts Schwab exports to custodian-neutral records."""
    custodian_code = "schwab"

    def read(self, path: Path) -> PortfolioBatch:
        lines = path.read_text(encoding="utf-8-sig").splitlines(True)
        header = next((i for i, line in enumerate(lines) if "account" in line.lower()), 0)
        rows = [{normalize_header(k): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(lines[header:])]
        headers = set().union(*(r.keys() for r in rows)) if rows else set()
        name = normalize_header(path.name)
        kind = next((k for k in ("positions", "transactions", "billing", "performance", "cash") if k in name), None)
        if not kind:
            kind = "positions" if "symbol" in headers and "quantity" in headers else "accounts"
        batch = PortfolioBatch(kind)
        getattr(self, "_" + kind)(rows, batch)
        return batch

    def _accounts(self, rows, batch):
        for r in rows:
            number = pick(r, "Account Number", "Account#", "Account")
            if number:
                batch.accounts.append(AccountRecord(number, pick(r, "Name", "Primary Account Holder"), pick(r, "Registration", "Registration Type"), pick(r, "Status") or "open", decimal_value(pick(r, "Total Value", "Market Value")), decimal_value(pick(r, "Cash Available", "Cash Value")), date_value(pick(r, "Closed Date")), pick(r, "Email", "Primary Email"), pick(r, "HH Group ID", "Household")))

    def _positions(self, rows, batch):
        for r in rows:
            number = pick(r, "Account Number", "Account#", "Account")
            symbol = pick(r, "Symbol", "Ticker", "CUSIP")
            if number and symbol:
                batch.positions.append(PositionRecord(number, symbol, pick(r, "Description", "Security Name") or symbol, decimal_value(pick(r, "Quantity", "Shares")), decimal_value(pick(r, "Price")), decimal_value(pick(r, "Market Value", "Value")), decimal_value(pick(r, "Cost Basis")), pick(r, "Asset Class", "Security Type") or "Other", date_value(pick(r, "As Of Date"), date.today())))

    def _transactions(self, rows, batch):
        for i, r in enumerate(rows):
            number = pick(r, "Account Number", "Account#", "Account")
            when = date_value(pick(r, "Date", "Transaction Date"))
            if number and when:
                external = pick(r, "Transaction ID", "Reference Number") or f"{when}:{i}:{pick(r, 'Amount')}"
                batch.transactions.append(TransactionRecord(number, external, when, pick(r, "Type", "Transaction Type") or "other", decimal_value(pick(r, "Amount", "Net Amount")), pick(r, "Description")))

    def _snapshot(self, rows, batch, target, fields):
        for r in rows:
            number = pick(r, "Account Number", "Account#", "Account")
            if number:
                getattr(batch, target).append(SnapshotRecord(number, date_value(pick(r, "As Of Date", "Date"), date.today()), {key: decimal_value(pick(r, *aliases)) for key, aliases in fields.items()}))

    def _cash(self, rows, batch): self._snapshot(rows, batch, "cash", {"cash_value": ("Cash", "Cash Value", "Cash Balance")})
    def _billing(self, rows, batch): self._snapshot(rows, batch, "billing", {"billable_value": ("Billable Value",), "fee_amount": ("Fee", "Fee Amount"), "billing_rate": ("Rate", "Billing Rate")})
    def _performance(self, rows, batch): self._snapshot(rows, batch, "performance", {"market_value": ("Market Value",), "net_contributions": ("Net Contributions",), "gain_loss": ("Gain Loss",), "return_percent": ("Return Percent", "Return")})
