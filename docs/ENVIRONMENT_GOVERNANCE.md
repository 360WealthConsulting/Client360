# Environment Governance (Phase D.64)

`app/services/environment_management/governance.py` is a read-only checker that verifies the
environment-management layer stays a **composition** over the authoritative environment / platform /
deployment-topology / lifecycle / infrastructure-dependency owners and never becomes a second CMDB /
infrastructure-management platform / cloud-management platform / deployment orchestrator / asset inventory /
configuration database / environment manager / monitoring platform. `validate_environment_management()` returns
`{ok, issue_count, findings}` and **never raises** into normal use (any internal error is captured as a single
finding).

## Invariants enforced

1. **No persistence / no writes.** No module defines a table, writes the DB (`.insert()` / `.update()` /
   `.delete()` / `sa.*`), publishes to the outbox (`publish_safe` / `publish_event`), or writes audit events
   (`write_audit`). The layer only composes reads — no shadow environment / platform / deployment /
   configuration store.
2. **No mutation / no duplicate engine.** The layer never calls an environment / deployment / provisioning /
   topology / lifecycle mutation. `_FORBIDDEN_CALLS` scans for `create_environment_profile(`,
   `create_deployment_reference(`, `create_service(`, `set_service_status(`, `add_dependency(`,
   `capture_runtime_snapshot(`, `set_flag(`, `create_connector(`, `provision(`, `deploy(`, `decommission(`,
   `retire(`, `write_audit(`, `publish_safe(`, `publish_event(`.
3. **No raw environment gating.** No `os.getenv` / `os.environ` — gates flow through the Runtime Engine only.
4. **No second metrics registry.** No `_DEFS =` / `class Metric` in the layer.
5. **Reuses authoritative reads.** `service.py` + `panels.py` must reference the authoritative owners
   (observability.catalog / observability.health / observability.service / runtime / integration) AND the
   environment owner (`list_environment_profiles` + `list_deployment_references`).
6. **Explainability enforced.** `is_explainable` present in both `model.py` and `panels.py` (a non-explainable
   panel is never emitted).
7. **Registry integrity.** Every registry key is unique **across all five registries**; every **configured**
   entry names an authoritative owner (a configured entry with `owner == not_configured` is a finding); every
   entry is complete (owner + capabilities + deep links + runtime gate); config_status is one of `configured` /
   `not_configured`.
8. **Panel / dashboard integrity.** Every panel names owner + source + deep link + explanation + permission;
   every dashboard names owner + audience + gate + navigation + panels + required capabilities + governing
   services, references only registered panels, and has a valid lifecycle.
9. **Derived labeling.** Any value computed by the layer (`source` starting `environment_management.compose`)
   must be labeled `derived` — an unlabeled derived summary is a finding.
10. **Governed gates present.** `gate.GATES` is non-empty.

## The honesty stance

Governance does not — and cannot — assert that an environment is healthy, a deployment ran, infrastructure was
provisioned, or a platform was retired. Those owners either do not exist (cloud resources, servers, containers,
VMs, formal lifecycle, retirement, decommission, host / network topology, live deployment execution) or own
only declared metadata (environment profiles, deployment references). So those registry entries are
`not_configured` and reported honestly. The checker's job is to keep the composition honest: **no fabricated
environment, deployment, infrastructure, topology, lifecycle state, environment health, platform ownership, or
retirement status**, no inferred platform impact at record scope, and no exposure of any credential, secret,
token, environment variable, connection string, private key, deployment payload, or private topology.

## Where it runs

- `tests/test_environment_management.py` asserts `validate_environment_management()["ok"]` is `True`.
- `app/services/environment_management/diagnostics.py` surfaces the governance report on the
  `observability.audit`-gated diagnostics route.

## References
- `app/services/environment_management/governance.py`, `diagnostics.py`
- `docs/ENTERPRISE_ENVIRONMENT_MANAGEMENT.md`, `docs/ENVIRONMENT_REGISTRY.md`,
  `docs/PLATFORM_LIFECYCLE_REGISTRY.md`, `docs/DEPLOYMENT_TOPOLOGY_REGISTRY.md`, ADR-069
