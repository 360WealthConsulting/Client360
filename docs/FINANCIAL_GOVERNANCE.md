# Financial Operations Governance (Phase D.57)

`app/services/financial_operations/governance.py` is a read-only checker that verifies the Financial
Operations layer stays a **composition** over the authoritative financial owners and never becomes a second
accounting platform, ERP, billing engine, commission engine, payroll system, bookkeeping platform, general
ledger, or budgeting application. It returns `{ok, issue_count, findings}` and **never raises** into normal
use. `validate_financial_operations()` is surfaced through the internal diagnostics endpoint
(`/financial-operations/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No second accounting / billing / payroll engine — no mutation.** No module calls a commission /
   accounting / billing / payroll **mutation** — `record_expected(`, `record_received(`, `record_adjustment(`,
   `write_off(`, `generate_expected(`, `import_statement(`, `reconcile_line(`, `reconcile_statement(`,
   `create_invoice(`, `post_journal_entry(`, `run_payroll(`, `pay_commission(`, `process_payment(`,
   `calculate_tax(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every financial category declares authoritative + reporting +
   calculation owner + runtime gate + deep links; every revenue type declares category + authoritative +
   reporting + recognition owner + runtime gate; every dashboard declares owner + audience + runtime gate +
   navigation + panels + required capabilities + governing services, and references only registered panels;
   every panel declares owner + source + deep link + explainability + permission; all registry keys are unique.
5. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in both
   `model.py` and `panels.py`; a non-explainable panel is never emitted.
6. **Composition anchored to the authoritative owners.** The composition must reference the authoritative
   commission ledger (`commission_report`) and the single Analytics Registry revenue metrics
   (`analytics.metrics`) — no second ledger, no second revenue engine.
7. **No raw environment gating.** Gates flow through the Runtime Engine
   (`runtime.consumption.feature_enabled`) and policy through the Policy Engine — never `os.getenv` /
   `os.environ`.

## No payroll details, tax returns, bank account numbers, payment credentials, or accounting payloads, ever

Panels and summaries carry **firm-level aggregate totals + status only** — never payroll details, tax returns,
bank account numbers, payment credentials, or accounting payloads. The commission owner exposes aggregate
totals (expected / received / outstanding / payout split), not statement line payloads. Diagnostics and
analytics counters are low-cardinality aggregates about the layer itself.

## Honest not-configured reporting

Billing / fee calculation / payroll / operating expenses / general ledger / profitability have **no
authoritative owner in the platform today**. Rather than fabricate an invoice, payroll run, or margin, the
registry declares those categories with a `not_configured` owner and their panels report `not_configured`.
This is a structural invariant: the layer reports the owner's real state and never invents one (the D.55 /
D.56 precedent).

## Authorization & least privilege

- Financial routes are gated by `analytics.view`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities` (`analytics.view`);
  otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to its own permission: firm financial figures require `analytics.executive`;
  catalog / operational panels require `analytics.view`. Commission panels compose the ledger under
  `insurance.commissions.read` internally and fail closed otherwise. A principal lacking the panel capability
  receives a `restricted` panel with `value = None` — never leaked.
- All composed reads inherit the record scope + capability checks of their authoritative owner.

## AI Assist boundary

AI Assist may **summarize** firm KPIs, revenue trends, recurring revenue, profitability indicators, and
operating metrics — fact class `DERIVED`, aggregates only, deep links only. It **never** issues invoices,
processes payroll, modifies accounting records, changes commissions, alters billing, or executes payments —
every fact comes from a composed section/summary.

## Enforcement

`tests/test_financial_operations.py` exercises the registries, explainable composition, authorization (`None` +
restricted, executive financial panels require `analytics.executive`), gate/policy behavior, the
analytics-counter reuse, diagnostics, the routes (registered + capability-gated), AI summarize-only, and the
architecture invariants (no second accounting/billing/payroll system, no duplicated accounting data, no
mutation, commissions composed from `commission_report`, revenue from `analytics.metrics`, every dashboard
deep-links). Route count, section registries, ADR count, and the single migration head are guarded by
`tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [FINANCIAL_OPERATIONS.md](FINANCIAL_OPERATIONS.md), [FINANCIAL_REGISTRY.md](FINANCIAL_REGISTRY.md),
[REVENUE_REGISTRY.md](REVENUE_REGISTRY.md), and [ADR-062](adr/ADR-062-financial-operations.md).
