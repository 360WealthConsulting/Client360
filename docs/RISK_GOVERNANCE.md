# Enterprise Risk Governance (Phase D.58)

`app/services/enterprise_risk/governance.py` is a read-only checker that verifies the Enterprise Risk layer
stays a **composition** over the authoritative risk / control / assurance owners and never becomes a second GRC
platform, risk register, compliance engine, exception system, audit platform, incident-management system,
control-testing application, policy engine, or approval engine. It returns `{ok, issue_count, findings}` and
**never raises** into normal use. `validate_enterprise_risk()` is surfaced through the internal diagnostics
endpoint (`/enterprise-risk/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit(` / `write_audit_event`). No `rm_*` projection
   table is read directly.
2. **No second GRC / risk / incident engine — no mutation.** No module calls a risk / finding / exception /
   incident / review / approval / policy **mutation** — `raise_exception(`, `acknowledge(`, `escalate(`,
   `resolve(`, `assign(`, `reopen(`, `submit_review(`, `record_decision(`, `create_incident(`,
   `create_finding(`, `approve_exception(`, `write_audit(`, `publish_event(`. The layer composes **reads**
   only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every risk domain + control family + assurance source +
   panel + dashboard is fully declared; every **configured** entry names an authoritative owner (a configured
   entry with a `not_configured` owner is a finding); every panel names owner + source + deep link +
   permission; all registry keys are unique.
5. **Control testing stays not_configured.** Every control family's `test_owner` must be `not_configured` — a
   control declaring a test owner is rejected (`fabricated_control_test_owner`). The layer never invents
   control effectiveness.
6. **No fabricated composite risk score.** Any `*_score` / `posture` panel must be labeled `derived`
   (`unlabeled_derived_score` otherwise). The `enterprise_risk_posture` panel is a DERIVED coverage summary and
   never a certified rating.
7. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in both
   `model.py` and `panels.py`; a non-explainable panel is never emitted.
8. **No raw environment gating.** Gates flow through the Runtime Engine
   (`runtime.consumption.feature_enabled`) and policy through the Policy Engine — never `os.getenv` /
   `os.environ`.

## No sensitive evidence, ever

Panels and summaries carry **counts, status, severity distributions, and coverage summaries only** — never
client-sensitive evidence, audit payloads, security details, credentials, tokens, bank information, tax-return
contents, document contents, or private incident narratives. The composed owners already strip sensitive
payloads; the risk layer surfaces only aggregates about them. Diagnostics and analytics counters are
low-cardinality aggregates about the layer itself.

## Honest not_configured reporting

Control testing / effectiveness, model/AI risk, privacy risk, financial authorization, and change management
have **no authoritative owner in the platform today**. Rather than fabricate risk / control / assurance status,
those registry entries declare `not_configured`, governance validates the honesty (a configured entry must
have an owner; a control test owner must stay `not_configured`), and the posture/coverage panels report the
not_configured lists explicitly. **An absent finding never certifies compliance** — the summary carries
`not_compliance_certification: True`.

## Authorization & least privilege

- Risk routes admit a **supervisor OR an executive** (`compliance.supervise` / `analytics.executive`, via
  `require_any_capability`); diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities`; otherwise
  `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to its authoritative-source capability (compliance `compliance.supervise`,
  security `security.view`, data `governance.view`, integration `integration.view`, resilience
  `observability.view`, financial `analytics.executive`, documentation `documents.view`, workflow
  `automation.view`). A principal lacking the panel capability receives a `restricted` panel with `value =
  None`, no hidden count, and no leaking metadata.
- Client-scoped sections compose ONLY owners that support per-entity record scope — firm-wide findings are
  never exposed to a client-scoped view.

## AI Assist boundary

AI Assist may **summarize** risk-domain counts, severity distributions, control coverage, open findings,
overdue reviews, assurance gaps, remediation workload, and source-provided ratings — distinguishing confirmed
facts, authoritative-source ratings, derived summaries, unavailable information, and not_configured domains
(fact class `DERIVED`, counts only, deep links only). It **never** assigns risk, changes severity, accepts
risk, closes findings, certifies controls, approves exceptions, acknowledges incidents, assigns remediation,
certifies compliance, invents evidence, or infers regulatory approval.

## Enforcement

`tests/test_enterprise_risk.py` exercises the three registries, completeness + duplicate-key prevention +
configured-owner validation + honest not_configured + control-testing-not_configured, explainable composition,
authorization (`None` + restricted with no leaking metadata), gate/policy behavior, the analytics-counter
reuse, diagnostics, the routes (registered + capability-gated for supervisor OR executive), AI summarize-only,
the no-fabricated-composite-risk-score invariant, and the architecture invariants (no second GRC / risk /
incident / exception / control-testing system, no mutation, no sensitive evidence, every configured panel
references an authoritative owner, every dashboard deep-links, every derived summary labeled). Route count,
section registries, ADR count, and the single migration head are guarded by
`tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [ENTERPRISE_RISK_MANAGEMENT.md](ENTERPRISE_RISK_MANAGEMENT.md),
[ENTERPRISE_RISK_REGISTRY.md](ENTERPRISE_RISK_REGISTRY.md), [CONTROL_REGISTRY.md](CONTROL_REGISTRY.md),
[ASSURANCE_REGISTRY.md](ASSURANCE_REGISTRY.md), and [ADR-063](adr/ADR-063-enterprise-risk-management.md).
