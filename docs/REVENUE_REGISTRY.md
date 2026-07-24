# Revenue Registry (Phase D.57)

The **revenue registry** (`REVENUE_REGISTRY` in `app/services/financial_operations/registry.py`) is the
declarative catalog of the firm's revenue types and, for each, the **authoritative owner** plus its reporting
and revenue-recognition owners. It is metadata only: the Financial Operations layer owns no revenue or
revenue-recognition state — it references the owners and explains the result with a deep link.

## Revenue types

Each type declares its `category`, `authoritative_owner` (or `not_configured`), `reporting_owner`,
`recognition_owner` (or `not_configured`), and `runtime_gate`.

| Type | Category | Authoritative owner | Recognition owner |
| --- | --- | --- | --- |
| `recurring_revenue` | recurring | portfolio | not_configured |
| `one_time_revenue` | one_time | bizdev | not_configured |
| `commissions` | commission | insurance_commissions | insurance_commissions |
| `advisory_fees` | advisory | portfolio | not_configured |
| `tax_preparation` | tax | not_configured | not_configured |
| `planning_engagements` | planning | not_configured | not_configured |
| `consulting` | consulting | not_configured | not_configured |
| `implementation` | implementation | not_configured | not_configured |

## Owned vs not-configured revenue (reported honestly)

Three revenue signals have real authoritative owners: **commissions** (the `insurance_commissions` ledger, the
one money owner, with `insurance_commissions` as the recognition owner), **recurring / advisory** revenue (the
portfolio AUM basis), and **one-time** business-development revenue (the bizdev owner, via the Analytics
`total_bd_revenue` metric). The remaining types — tax preparation, planning engagements, consulting,
implementation — have **no authoritative billing owner** and declare `not_configured`. Revenue *recognition*
(ASC 606-style) is owned only for commissions; for AUM/BD revenue it is `not_configured` (there is no
recognition engine). The layer reports these honestly and never fabricates a recognized-revenue figure.

## Ownership boundaries (never re-implemented here)

- **Commissions** — `insurance_commissions` (canonical ledger) + `insurance_reporting.commission_report`
  (expected vs received, producer-payout vs agency-retained split). The layer never recognizes or pays a
  commission.
- **Recurring / advisory revenue** — the portfolio AUM basis (`portfolio.book_aum`), surfaced via the single
  Analytics Registry. Fee billing itself is not owned (`not_configured`).
- **One-time revenue** — business-development revenue (`bizdev`), via the Analytics `total_bd_revenue` metric.

## How the registry is used

The revenue + commissions + financial-operations dashboards compose `registered_revenue` (this registry),
`recurring_revenue`, `business_development_revenue`, `revenue_mix`, `commission_revenue`,
`commission_reconciliation`, and `producer_payouts`. Governance validates that every type declares all fields
(category + authoritative + reporting + recognition owner + runtime gate) and that keys are unique.

See [FINANCIAL_REGISTRY.md](FINANCIAL_REGISTRY.md), [FINANCIAL_OPERATIONS.md](FINANCIAL_OPERATIONS.md), and
[ADR-062](adr/ADR-062-financial-operations.md).
