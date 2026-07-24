# Financial Registry (Phase D.57)

The **financial registry** (`FINANCIAL_REGISTRY` in `app/services/financial_operations/registry.py`) is the
declarative catalog of the firm's financial categories and, for each, the **authoritative owner** plus its
reporting and calculation owners. It is metadata only: the Financial Operations layer owns no financial,
revenue, commission, payroll, or accounting state — it references the owners and explains the result with a
deep link.

## Financial categories

Each category declares its `authoritative_owner` (or `not_configured`), `reporting_owner`, `calculation_owner`,
`runtime_gate`, and `deep_links`.

| Category | Authoritative owner | Reporting owner | Calculation owner |
| --- | --- | --- | --- |
| `advisory_revenue` | portfolio | analytics.metrics | not_configured |
| `tax_revenue` | not_configured | tax_domain | not_configured |
| `insurance_commissions` | insurance_commissions | insurance_reporting | insurance_commissions |
| `planning_fees` | not_configured | not_configured | not_configured |
| `subscriptions` | not_configured | not_configured | not_configured |
| `payroll` | not_configured | not_configured | not_configured |
| `operating_expenses` | not_configured | not_configured | not_configured |
| `technology_costs` | not_configured | vendor_management | not_configured |
| `vendor_spend` | not_configured | vendor_management | not_configured |
| `profitability` | not_configured | not_configured | not_configured |

## The billing / payroll / GL / profitability gap (reported honestly)

The insurance commission ledger is the **only** authoritative money owner in the platform; advisory revenue is
the AUM basis owned by the portfolio owner (fee *billing* itself is not owned). There is **no authoritative
billing, fee-calculation, payroll, operating-expense, general-ledger, or profitability owner today**. Rather
than fabricate an invoice, payroll run, or margin, those categories declare `authoritative_owner =
not_configured` (and `calculation_owner = not_configured`). This is a structural invariant: the layer reports
the owner's real state and never invents one (the D.55 / D.56 precedent). A margin cannot be computed —
there is no cost data — so `profitability` is reported `not_configured`, never fabricated.

## Ownership boundaries (never re-implemented here)

- **Commissions** are owned by `insurance_commissions` (the canonical ledger) and reported by
  `insurance_reporting.commission_report`. The layer never records, adjusts, writes off, or pays a commission.
- **Advisory revenue basis (AUM)** is owned by `portfolio` (`book_aum`) and surfaced via the single Analytics
  Registry `aum` metric. The layer never calculates a fee or bills a client.
- **Revenue metrics** are owned by the single Analytics Registry (`analytics.metrics` / `analytics.trends`).
  The layer defines no metric of its own.
- **Technology / vendor spend** has no authoritative cost owner; the layer references the D.56 Vendor
  Management dependency *counts* only (spend `not_configured`).

## How the registry is used

The revenue / profitability / expenses / payroll / commissions / firm-performance / financial-operations
dashboards compose `registered_financial` (this registry), `firm_aum`, `recurring_revenue`,
`business_development_revenue`, `commission_revenue`, `financial_coverage`, and the composed indicators.
Governance validates that every category declares all fields (authoritative + reporting + calculation owner +
runtime gate + deep links) and that keys are unique.

See [REVENUE_REGISTRY.md](REVENUE_REGISTRY.md), [FINANCIAL_OPERATIONS.md](FINANCIAL_OPERATIONS.md), and
[ADR-062](adr/ADR-062-financial-operations.md).
