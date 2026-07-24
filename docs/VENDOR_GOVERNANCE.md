# Vendor Management Governance (Phase D.56)

`app/services/vendor_management/governance.py` is a read-only checker that verifies the Vendor Management
layer stays a **composition** over the authoritative vendor / technology owners and never becomes a second
vendor-management platform, procurement system, contract repository, CMDB, asset inventory, licensing
platform, or risk engine. It returns `{ok, issue_count, findings}` and **never raises** into normal use.
`validate_vendor_management()` is surfaced through the internal diagnostics endpoint
(`/vendor-management/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No second vendor / licensing engine — no mutation.** No module calls a vendor / provider / connector /
   certificate / license / secret **mutation** — `create_provider(`, `create_connector(`,
   `set_connector_status(`, `create_certificate(`, `renew_certificate_reference(`, `rotate_secret(`,
   `create_secret_reference(`, `create_license(`, `renew_license(`, `create_incident(`,
   `set_incident_status(`, `run_sync(`, `run_due_syncs(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every vendor class declares authoritative + integration +
   security + lifecycle owner + provider type + runtime gate + deep links; every lifecycle class declares
   category + owner + lifecycle + renewal + support owner + runtime gate; every dashboard declares owner +
   audience + runtime gate + navigation + panels + required capabilities + governing services, and references
   only registered panels; every panel declares owner + source + deep link + explainability + permission; all
   registry keys are unique.
5. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in
   both `model.py` and `panels.py`; a non-explainable panel is never emitted.
6. **No raw environment gating.** Gates flow through the Runtime Engine (`runtime.consumption.feature_enabled`)
   and policy through the Policy Engine — never `os.getenv` / `os.environ`.

## No contract contents, credentials, license keys, secrets, or procurement payloads, ever

Panels and summaries carry **counts + status only** — never contract contents, credentials, license keys,
secrets, or procurement payloads. The composed owners already strip ciphertext from secret/credential
listings; certificates expose status + `not_after` only (no key material). Diagnostics and analytics counters
are low-cardinality aggregates about the layer itself.

## Honest not-configured reporting

Procurement / contracts / subscriptions have **no authoritative owner in the platform today**. Rather than
fabricate a vendor/subscription inventory, the registry declares those classes with a `not_configured` owner.
This is a structural invariant: the layer reports the owner's real state and never invents one (the D.55
precedent).

## Authorization & least privilege

- Vendor routes are gated by `integration.view`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities` (`integration.view`);
  otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to its own permission: most panels require `integration.view`;
  **third-party-risk panels require `security.view`**; licensing panels that compose Insurance licensing
  require `insurance.licensing.read` internally and fail closed otherwise. A principal lacking the panel
  capability receives a `restricted` panel with `value = None` — never leaked.
- All composed reads inherit the record scope + capability checks of their authoritative owner.

## AI Assist boundary

AI Assist may **summarize** vendor counts (vendor health, lifecycle status, licensing, renewals, operational
dependencies) — fact class `DERIVED`, counts only, deep links only. It **never** approves purchases, renews
contracts, terminates vendors, alters licensing, or modifies subscriptions — every fact comes from a composed
section/summary.

## Enforcement

`tests/test_vendor_management.py` exercises the registries, explainable composition, authorization (`None` +
restricted, risk panels require `security.view`), gate/policy behavior, the analytics-counter reuse,
diagnostics, the routes (registered + capability-gated), AI summarize-only, and the architecture invariants
(no second vendor/licensing/contract system, no duplicated inventories, no mutation, vendor reads composed
from `integration.connectors`, every dashboard deep-links). Route count, section registries, ADR count, and
the single migration head are guarded by `tests/test_platform_architecture.py`,
`tests/test_client360_workspace.py`, `tests/test_household360_workspace.py`,
`tests/test_architecture_decision_records.py`, and the manifest.

See [VENDOR_MANAGEMENT.md](VENDOR_MANAGEMENT.md), [VENDOR_REGISTRY.md](VENDOR_REGISTRY.md),
[TECHNOLOGY_LIFECYCLE_REGISTRY.md](TECHNOLOGY_LIFECYCLE_REGISTRY.md), and
[ADR-061](adr/ADR-061-vendor-management.md).
