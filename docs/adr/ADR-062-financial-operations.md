# ADR-062 — Enterprise Financial Operations & Revenue Intelligence: A Read-Only Composition, Not a Second Accounting/ERP/Billing Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Financial Operations / Revenue Intelligence / Firm Performance);
Security / Authorization (RBAC ownership); Finance / Revenue Operations; Compliance; Business Operations Owner
(Michael Shelton).

## Context
The mandatory D.57 audit found the platform owns a narrow but real set of financial read surfaces — and no
accounting, ERP, billing, fee-calculation, payroll, general-ledger, or profitability owner exists:

* **Insurance commission ledger (the one authoritative money owner)** — `insurance_commissions`
  (`list_commissions` / `list_statements` / `get_commission`, the canonical `insurance_commissions` table) +
  `insurance_reporting.commission_report(principal)` → the operational reconciliation + revenue rollup
  (`expected_total` / `received_total` / `outstanding_total` / `variance_total` / `producer_payouts` vs
  `agency_retained`). Money reconciliation and revenue reporting only.
* **Portfolio AUM owner** — `portfolio.book_aum(person_ids)` (the advisory revenue basis), surfaced firm-wide
  through the single Analytics Registry `aum` metric.
* **Analytics Registry revenue metrics (D.15/D.48)** — `analytics.metrics` category `revenue`
  (`aum`, `total_bd_revenue`, `campaign_revenue`, `referral_revenue`, `forecast_revenue`) + `pipeline_value`,
  fed from portfolio, bizdev, opportunity, campaign, and referral owners; `analytics.trends` for the trend.
* **Executive Reporting (D.48)** — `executive_intelligence.executive_summary` (firm KPIs incl. `firm_aum`,
  `revenue_kpi`). **Practice Management (D.49)** — capacity utilization (an operating-efficiency signal).

There is **no billing / invoicing / fee-calculation service, no payroll service** (payroll is a *disabled*
benefits integration), **no accounting / GL / journal / bookkeeping service** (QuickBooks is a connector stub
only), and **no profitability / margin engine** (no cost data exists to compute margin). There was **no
financial-operations composition layer** unifying revenue, profitability, expenses, payroll, commissions, and
firm KPIs into named, firm-wide views. Building a second accounting platform, ERP, billing engine, commission
engine, payroll system, bookkeeping platform, general ledger, or budgeting application would violate the
"no second system" invariant and duplicate governed infrastructure.

## Decision
Phase D.57 adds a **governed, read-only financial-operations composition layer**
(`app/services/financial_operations/`) with NO new metrics, NO persistence, and NO mutation:

1. Two declarative **registries** (`registry.py`): `FINANCIAL_REGISTRY` (10 categories — advisory revenue,
   tax revenue, insurance commissions, planning fees, subscriptions, payroll, operating expenses, technology
   costs, vendor spend, profitability — each naming authoritative / reporting / calculation owner + runtime
   gate + deep links) and `REVENUE_REGISTRY` (8 types — recurring, one-time, commissions, advisory fees, tax
   preparation, planning engagements, consulting, implementation — each naming authoritative / reporting /
   recognition owner), plus `PANEL_REGISTRY` (20 panels) and `FINANCIAL_DASHBOARDS` (7 dashboards: firm
   performance, revenue, profitability, expenses, payroll, commissions, financial operations).
2. Normalized read-models (`model.py`): `PanelResult` + `FinancialDashboard`, each explainable (explanation +
   source + deep link, a hard emit gate) and reference-only; **firm-level aggregate totals + status only,
   never a payroll detail / tax return / bank account number / payment credential / accounting payload**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the insurance commission ledger, the portfolio AUM owner, the single Analytics Registry, Executive
   Reporting, Practice Management, the D.56 Vendor Management layer). **Billing / fee calculation / payroll /
   operating expenses / GL / profitability have no authoritative owner** — those registry categories carry a
   `not_configured` owner and their panels report `not_configured` honestly (the D.55 / D.56 precedent).
   Fail-closed; every panel self-restricts (firm financial figures require `analytics.executive`).
4. The **financial-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `firm_financial_summary`, plus `client_financial` / `household_financial` (the advisory revenue basis — the
   entity's AUM — from the portfolio owner). Every dashboard carries generated timestamp, governing services,
   source inventory, explainable panels, and deep links. Dashboard-level authorization (`analytics.view`).
5. **Runtime gates** (`financial_operations.enabled` + `revenue.enabled` + `profitability.enabled`), **policy
   composition**, **analytics reuse** (four operational counters registered into the ONE Analytics Registry —
   no second registry), internal **diagnostics** (`observability.audit`), and a read-only **governance**
   checker that forbids mutation, persistence, and any commission / accounting / billing / payroll mutation
   (`record_expected`, `record_received`, `write_off`, `import_statement`, `reconcile_statement`,
   `create_invoice`, `post_journal_entry`, `run_payroll`, `pay_commission`, `process_payment`, …). AI Assist
   may summarize firm KPIs / revenue trends / recurring revenue but never issues invoices, processes payroll,
   modifies accounting records, changes commissions, alters billing, or executes payments.

No migration, no new table, no new capability (reuses `analytics.view` + `analytics.executive` +
`insurance.commissions.read` + `observability.audit`), no new metric, no new outbox contract. Single Alembic
head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second accounting platform / ERP / billing engine / commission engine / payroll system / bookkeeping
  platform / general ledger / budgeting application.** Rejected: the insurance commission ledger, the
  portfolio AUM owner, and the single Analytics Registry are the authoritative owners; D.57 composes them.
  Governance forbids a second store and any accounting/billing/payroll mutation. Where no owner exists
  (billing / fee calc / payroll / expenses / GL / profitability), the layer declares `not_configured` rather
  than inventing one.
- **A second metrics registry.** Rejected: revenue figures come from the owners' reads; the layer registers
  only operational counters (about itself) into the single Analytics Registry — the house style.
- **Computing profitability / margin from a fabricated cost model.** Rejected: no authoritative operating
  expense, payroll, or GL owner exists, so a margin cannot be honestly computed; the layer reports
  `not_configured` and never fabricates a profit figure.

## Reasons for the decision
Firm financial performance needs one operational view; the commission ledger, the portfolio owner, and the
Analytics Registry already own every number with the correct scoping. A read-only composition gives that view
with full explainability (source + deep link) while every commission stays owned by `insurance_commissions`,
every revenue metric by the single Analytics Registry, and every KPI by Executive Reporting. Deep links (never
inline billing) route the operator to the authoritative surface to act. Emitting aggregate totals + status
only keeps payroll details, tax returns, bank account numbers, payment credentials, and accounting payloads
out of the layer entirely.

## Rationale for avoiding a second accounting, ERP, or billing platform
A second accounting / ERP / billing platform would require duplicated invoices, journal entries, ledgers,
payroll records, and commission calculations, plus its own recognition + reconciliation model — duplicating
governed infrastructure and creating reconciliation + drift + shadow-ledger risk, with no benefit the
composition does not already provide. Composing over the single commission ledger + the single Analytics
Registry keeps one source of truth for every dollar and zero duplicated accounting data.

## Consequences

### Positive consequences
- One firm-wide financial surface with no second accounting platform, ERP, billing engine, commission engine,
  payroll system, bookkeeping platform, GL, or budgeting application.
- Record scope + capability are inherited from the composed owner reads; a non-`analytics.view` principal sees
  no dashboards, and firm financial figures additionally require `analytics.executive`.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Financial Performance panel + Client 360 / Household 360 Financial Relationship sections +
  an Executive Financial Operations dashboard (reusing existing widgets) + AI summarize-only, all from one
  layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- Billing / fee calc / payroll / operating expenses / GL / profitability panels declare `not_configured`
  owners until an authoritative owner is added to the platform — honest, not a fabricated ledger.
- The layer's coverage is bounded by the owners' read surface; a genuinely new financial signal is added to
  the owning domain first, then surfaces here.

## Enforcement
`tests/test_financial_operations.py` (two registries + single ownership + `not_configured` honesty;
explainable dashboard composition; authorization — unauthorized → None, unentitled panel restricted never
valued, executive financial panels require `analytics.executive`; runtime + policy gates; the firm summary +
client/household rollups; analytics reuse — the 4 counters in the ONE registry; diagnostics; routes registered
+ capability-gated; AI summarize-only; and the architecture invariants — no second accounting/billing/payroll
engine, no duplicated accounting data, no mutation, commissions composed from `commission_report`, revenue
from `analytics.metrics`, every dashboard deep-links). `app/services/financial_operations/governance.py`
enforces the invariants at runtime. Route count, section registries, ADR count, and migration head are guarded
by `tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` +
`tests/test_household360_workspace.py` + `tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global revenue reads that do not self-gate (Analytics Registry revenue metrics) are exposed only within
dashboards whose required capability (`analytics.view`) the principal holds; each panel additionally
self-restricts to its own capability. Firm financial figures require `analytics.executive`; commission panels
that compose the ledger require `insurance.commissions.read` internally and fail closed otherwise.

## Revisit conditions
Revisit when an authoritative billing / fee-calculation / payroll / accounting / GL / profitability owner is
added to the platform (compose it here, replacing the `not_configured` categories — never a second accounting
platform), or if a materialized financial read-model is ever justified (it would be a governed projection,
never a second ledger).

## References
- `app/services/financial_operations/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/financial_operations.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Financial Performance panel in
  `app/services/workspace/service.py`; Executive Financial Operations dashboard in
  `app/services/executive_intelligence/registry.py`; AI grounding in `app/services/ai_assist/context.py`;
  analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/insurance_commissions.py`, `app/services/insurance_reporting.py`,
  `app/services/portfolio.py`, `app/services/analytics/{metrics,trends,sources}.py`,
  `app/services/executive_intelligence/*`, `app/services/practice_management/*`,
  `app/services/vendor_management/*`, `app/services/tax_domain.py`
- `docs/FINANCIAL_OPERATIONS.md`, `docs/FINANCIAL_REGISTRY.md`, `docs/REVENUE_REGISTRY.md`,
  `docs/FINANCIAL_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_financial_operations.py`; relates to ADR-015, ADR-048, ADR-049, ADR-053, ADR-061
