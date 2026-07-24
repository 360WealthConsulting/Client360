"""Enterprise Financial Operations, Revenue Intelligence & Firm Performance Governance layer (Phase D.57).

A governed, READ-ONLY composition that provides a single operational view of firm financial performance —
revenue, profitability, operating expenses, payroll, commissions, and firm KPIs — WITHOUT introducing a
second accounting platform, ERP, billing engine, commission engine, payroll system, bookkeeping platform,
general ledger, or budgeting application. It composes named financial dashboards from declarative financial +
revenue + panel registries over the platform's AUTHORITATIVE owners: the insurance commission ledger
(`insurance_commissions` / `insurance_reporting.commission_report`, the one money owner), the portfolio AUM
owner, the single Analytics Registry revenue metrics (`analytics.metrics` / `analytics.trends`), Executive
Reporting, and Practice Management. Billing / fee calculation / payroll / operating expenses / GL /
profitability have no authoritative owner in the platform today — those are declared registry categories with
a `not_configured` owner, never a fabricated figure. It defines no new metrics, owns no persistence, and never
creates an invoice, posts a journal entry, processes payroll, calculates taxes, pays a commission, or modifies
an accounting record; every panel is explainable, deep-links to its authoritative financial-owner surface,
and carries firm-level aggregate totals + status only — never a payroll detail, tax return, bank account
number, payment credential, or accounting payload.
"""
from .service import (
    client_financial,
    compose_dashboard,
    firm_financial_summary,
    get_panel,
    household_financial,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "firm_financial_summary",
    "client_financial",
    "household_financial",
]
