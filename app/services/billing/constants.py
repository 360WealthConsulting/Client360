"""Billing enumerations + money helpers (USD cents, integer only)."""
from __future__ import annotations

SUBJECT_TYPES: tuple[str, ...] = ("person", "household", "organization")
AGREEMENT_STATUSES: tuple[str, ...] = ("active", "paused", "ended")
FREQUENCIES: tuple[str, ...] = ("monthly", "annual", "one_time", "none")
INVOICE_STATUSES: tuple[str, ...] = ("draft", "issued", "paid", "partial", "void")
LINE_KINDS: tuple[str, ...] = ("fee", "adjustment", "credit")
PAYMENT_METHODS: tuple[str, ...] = ("manual", "check", "ach", "card")
PAYMENT_STATUSES: tuple[str, ...] = ("recorded", "settled", "failed")

# Line kinds that reduce the invoice total (credits/adjustments may be negative fees).
_CREDIT_KINDS = frozenset({"credit"})


def money(cents: int | None, currency: str = "USD") -> str:
    """Format integer minor units as a display string. USD only in the MVP."""
    c = int(cents or 0)
    sign = "-" if c < 0 else ""
    dollars, rem = divmod(abs(c), 100)
    return f"{sign}${dollars:,}.{rem:02d}"
