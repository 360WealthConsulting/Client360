# Change Governance (Phase D.63)

`app/services/change_management/governance.py` is a read-only checker that verifies the change-management layer
stays a **composition** over the authoritative change / release / configuration / evidence owners and never
becomes a second ITSM / change-management / deployment / CI-CD / Git / CMDB / feature-flag / release-approval /
incident / maintenance-scheduling platform. `validate_change_management()` returns `{ok, issue_count,
findings}` and **never raises** into normal use (any internal error is captured as a single finding).

## Invariants enforced

1. **No persistence / no writes.** No module defines a table, writes the DB (`.insert()` / `.update()` /
   `.delete()` / `sa.*`), publishes to the outbox (`publish_safe` / `publish_event`), or writes audit events
   (`write_audit`). The layer only composes reads — no shadow change / release / deployment / configuration
   store.
2. **No mutation / no duplicate engine.** The layer never calls a change / deployment / migration / flag
   mutation. `_FORBIDDEN_CALLS` scans for `set_flag(`, `upgrade(`, `downgrade(`, `merge(`, `deploy(`,
   `rollback(`, `approve(`, `schedule_maintenance(`, `acknowledge_incident(`,
   `create_environment_profile(`, `create_deployment_reference(`, `create_connector(`, `run_sync(`,
   `create_document(`, `write_audit(`, `publish_safe(`, `publish_event(`.
3. **No raw environment gating.** No `os.getenv` / `os.environ` — gates flow through the Runtime Engine only.
4. **No second metrics registry.** No `_DEFS =` / `class Metric` in the layer.
5. **Reuses authoritative reads.** `service.py` + `panels.py` must reference the authoritative owners
   (observability / runtime / policy / continuous_integration / architecture_manifest / security /
   compliance_intelligence / integration_hub) AND the self-verification owner (`_expected_head` +
   `app.routes`).
6. **Explainability enforced.** `is_explainable` present in both `model.py` and `panels.py` (a non-explainable
   panel is never emitted).
7. **Registry integrity.** Every registry key is unique; every **configured** entry names an authoritative
   owner (a configured entry with `owner == not_configured` is a finding); every entry is complete
   (owner + capabilities + deep link(s) + runtime gate); config_status is one of `configured` /
   `not_configured`.
8. **Panel / dashboard integrity.** Every panel names owner + source + deep link + explanation + permission;
   every dashboard names owner + audience + gate + navigation + panels + required capabilities + governing
   services, references only registered panels, and has a valid lifecycle.
9. **Derived labeling.** Any value computed by the layer (`source` starting `change_management.compose`) must
   be labeled `derived` — an unlabeled derived summary is a finding.
10. **Governed gates present.** `gate.GATES` is non-empty.

## The honesty stance

Governance does not — and cannot — assert that a change was deployed, a release was approved, a rollback is
ready, or production was verified. Those owners do not exist in the platform, so their registry entries are
`not_configured` and reported honestly. The checker's job is to keep the composition honest: **no fabricated
change request, deployment status, release approval, rollback readiness, configuration state, production
verification, environment health, or change success**, and no exposure of any credential, secret, token,
environment variable, connection string, private key, deployment payload, or sensitive configuration value.

## Where it runs

- `tests/test_change_management.py` asserts `validate_change_management()["ok"]` is `True`.
- `app/services/change_management/diagnostics.py` surfaces the governance report on the
  `observability.audit`-gated diagnostics route.

## References
- `app/services/change_management/governance.py`, `diagnostics.py`
- `docs/ENTERPRISE_CHANGE_MANAGEMENT.md`, `docs/CHANGE_DOMAIN_REGISTRY.md`, `docs/RELEASE_REGISTRY.md`,
  `docs/CONFIGURATION_REGISTRY.md`, `docs/CHANGE_EVIDENCE_REGISTRY.md`, ADR-068
