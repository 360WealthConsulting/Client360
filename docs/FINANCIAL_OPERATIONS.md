# Financial Operations, Revenue Intelligence & Firm Performance Governance (Phase D.57)

`app/services/financial_operations/` is a governed, **read-only composition** that provides a single
operational view of firm financial performance — revenue, profitability, operating expenses, payroll,
commissions, and firm KPIs — over the platform's **authoritative** financial owners. It is **not** a second
accounting platform, ERP, billing engine, commission engine, payroll system, bookkeeping platform, general
ledger, or budgeting application: **no new metrics, no persistence, no mutation, no duplicated accounting
data, no new capability, no migration** (single Alembic head `n5s6u7p8v9w0`).

## What it composes (existing owners only)

| Domain | Authoritative owner | Composed read |
| --- | --- | --- |
| Commissions (the one money owner) | `insurance_commissions` / `insurance_reporting` | `commission_report(principal)` → expected / received / outstanding / variance / producer-payout split |
| Advisory revenue basis (AUM) | `portfolio` | `book_aum(person_ids)`, surfaced via the Analytics `aum` metric |
| Revenue metrics | Analytics Registry (`analytics.metrics` / `analytics.trends`) | `aum`, `total_bd_revenue`, `pipeline_value`, `forecast_revenue`, AUM trend |
| Firm KPIs | Executive Reporting (`executive_intelligence`) | `executive_summary(principal)` |
| Operating efficiency | Practice Management | `practice_summary(principal)` (capacity utilization) |
| Technology / vendor dependencies | Vendor Management (D.56) | `vendor_summary(principal)` (counts; spend `not_configured`) |
| Tax workload | Tax domain | `analytics.sources.tax_dashboard` (an operational proxy) |

## The billing / payroll / GL / profitability gap (reported honestly)

There is **no authoritative billing, invoicing, fee-calculation, payroll, accounting, general-ledger, or
profitability owner in the platform today** (the D.57 audit confirmed: payroll is a *disabled* benefits
integration; QuickBooks is a connector stub; no journal/GL/margin logic exists anywhere). Rather than
fabricate an invoice, payroll, or margin figure, those `FINANCIAL_REGISTRY` categories are declared
`owner = not_configured` and their panels report `not_configured` honestly. This mirrors the D.55 backup and
D.56 procurement precedents: report the owner's real state, never invent one. When an authoritative billing /
payroll / accounting owner is added to the platform, the layer composes it — never a second accounting system.

## Registries, panels, dashboards

Two declarative registries — `FINANCIAL_REGISTRY` (10 categories) + `REVENUE_REGISTRY` (8 types) — plus 20
panels and 7 dashboards (firm performance, revenue, profitability, expenses, payroll, commissions, financial
operations). See [FINANCIAL_REGISTRY.md](FINANCIAL_REGISTRY.md) and [REVENUE_REGISTRY.md](REVENUE_REGISTRY.md).
Every dashboard carries a generated timestamp, governing services, source inventory, explainable panels, and
deep links; every panel is explainable (owner + source + deep link, a hard emit gate) and carries **firm-level
aggregate totals + status only — never a payroll detail, tax return, bank account number, payment credential,
or accounting payload**.

## Authorization

- Routes + dashboards are gated by `analytics.view`; diagnostics by `observability.audit`.
- Each **panel self-restricts** to its own permission: firm financial figures require `analytics.executive`;
  catalog / operational panels require `analytics.view`. A principal lacking the panel capability receives a
  `restricted` panel with `value = None` — never leaked. Commission panels additionally require
  `insurance.commissions.read` internally and fail closed otherwise.
- All composed reads inherit the record scope + capability checks of their authoritative owner.

## Runtime, governance, analytics, observability

Every surface is gated through the Runtime Engine (`financial_operations.enabled` / `revenue.enabled` /
`profitability.enabled`) and the Policy Engine — **no environment bypass**. Governance
(`validate_financial_operations()`) returns `{ok, issue_count, findings}` and forbids persistence, mutation,
any accounting/billing/payroll mutation, a second metrics registry, and incomplete registries — see
[FINANCIAL_GOVERNANCE.md](FINANCIAL_GOVERNANCE.md). Four low-cardinality operational counters
(`financial_dashboards_composed`, `financial_panels_composed`, `financial_panel_failures`,
`financial_authorization_failures`) register into the **single** Analytics Registry — no second metrics store.
Internal diagnostics (`/financial-operations/diagnostics`, `observability.audit`) report registry coverage,
panel availability, and the governance summary.

## Surfaces

- **Advisor Workspace** — a **Financial Performance** panel (`ws["financial_performance"]`).
- **Client 360 / Household 360** — a **Financial Relationship** section (`analytics.executive`): the advisory
  revenue basis (the entity's AUM) the firm relationship rests on, from the portfolio owner.
- **Executive Dashboard** — a **Financial Operations** dashboard reusing existing widgets (`revenue_kpi`,
  `firm_aum`, `operational_health` — no new widget).
- **AI Assist** — summarizes firm KPIs / revenue trends / recurring revenue / advisory revenue basis (fact
  class `DERIVED`, aggregates only, deep links only). It **never** issues invoices, processes payroll, modifies
  accounting records, changes commissions, alters billing, or executes payments.

## Routes

`/financial-operations` (HTML) + `/api/v1/financial-operations/{dashboards, dashboard/{key}, summary,
registry, panel/{key}, metrics}` + `/financial-operations/diagnostics`.

See [FINANCIAL_REGISTRY.md](FINANCIAL_REGISTRY.md), [REVENUE_REGISTRY.md](REVENUE_REGISTRY.md),
[FINANCIAL_GOVERNANCE.md](FINANCIAL_GOVERNANCE.md), and [ADR-062](adr/ADR-062-financial-operations.md).

**Related (D.58):** the **Enterprise Risk Management** layer (`/enterprise-risk`) composes this layer's
`firm_financial_summary` + the commission ledger for its financial-control risk panels (reconciliation status,
commission exceptions) — read-only, `analytics.executive`. It never bills, reconciles, or pays anything;
Financial Operations + the commission ledger remain the authoritative owners (financial authorization itself
stays `not_configured`). See [ENTERPRISE_RISK_MANAGEMENT.md](ENTERPRISE_RISK_MANAGEMENT.md) and
[ADR-063](adr/ADR-063-enterprise-risk-management.md).
