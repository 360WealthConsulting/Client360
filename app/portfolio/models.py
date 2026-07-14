from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

@dataclass(frozen=True)
class AccountRecord:
    account_number: str
    name: str = ""
    registration: str = ""
    status: str = "open"
    total_value: Decimal = Decimal("0")
    cash_value: Decimal = Decimal("0")
    closed_date: Optional[date] = None
    owner_email: str = ""
    household_key: str = ""

@dataclass(frozen=True)
class PositionRecord:
    account_number: str
    symbol: str
    name: str
    quantity: Decimal
    price: Decimal
    market_value: Decimal
    cost_basis: Optional[Decimal] = None
    asset_class: str = "Other"
    as_of_date: date = field(default_factory=date.today)

@dataclass(frozen=True)
class TransactionRecord:
    account_number: str
    external_id: str
    transaction_date: date
    transaction_type: str
    amount: Decimal
    description: str = ""

@dataclass(frozen=True)
class SnapshotRecord:
    account_number: str
    as_of_date: date
    values: dict[str, Any]

@dataclass(frozen=True)
class BeneficiaryRecord:
    account_number: str
    name: str
    beneficiary_type: str = "primary"
    percentage: Optional[Decimal] = None

@dataclass
class PortfolioBatch:
    source_type: str
    accounts: list[AccountRecord] = field(default_factory=list)
    positions: list[PositionRecord] = field(default_factory=list)
    transactions: list[TransactionRecord] = field(default_factory=list)
    cash: list[SnapshotRecord] = field(default_factory=list)
    performance: list[SnapshotRecord] = field(default_factory=list)
    billing: list[SnapshotRecord] = field(default_factory=list)
    beneficiaries: list[BeneficiaryRecord] = field(default_factory=list)
