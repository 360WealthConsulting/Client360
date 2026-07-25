# Enterprise Environment Management, Deployment Topology & Platform Lifecycle Intelligence (Phase D.64)

`app/services/environment_management/` is a governed, **read-only composition** that provides a unified,
governed view of the firm's environment and platform landscape — environment inventory, deployment topology,
runtime topology, platform ownership, lifecycle state, infrastructure dependencies, runtime coverage, topology
health, lifecycle readiness, retirement readiness, environment gaps, and dependency visibility. It is **not** a
second CMDB, infrastructure-management platform, cloud-management platform, deployment orchestrator, asset
inventory, configuration database, environment manager, or monitoring platform: **no new capability, no new
metric, no persistence, no mutation, no duplicated environment / infrastructure data, no migration** (single
Alembic head `n5s6u7p8v9w0`).

> These are **operational-visibility summaries**, never a certified environment health, deployment status,
> provisioning outcome, or retirement decision. **Environment metadata is not live infrastructure, a deployment
> reference is not a deployment, an active flag is not a lifecycle guarantee, and a runtime snapshot is not
> continuous environment health.**

> **No secrets, ever.** No credential, secret, token, environment variable, connection string, private key,
> deployment payload, protected infrastructure detail, private topology, or sensitive configuration value is
> ever carried in a panel — counts, status, identifiers, coverage, and verification results only.

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| Environment profiles (production / staging / development / test, region, active) | Observability catalog (D.26) | `list_environment_profiles` | `observability.view` |
| Deployment references (version, migration head, env mapping, released_at) | Observability catalog | `list_deployment_references` | `observability.view` |
| Platform / service inventory (type, criticality, status, owner) | Observability catalog | `list_services` | `observability.view` |
| Service dependency graph (hard / soft / runtime) | Observability catalog | `list_dependencies` | `observability.view` |
| Runtime snapshots (ready / not_ready, migration in-sync) | Observability health (D.26) | `list_runtime_snapshots` | `observability.view` |
| Live migration head | Observability health | `_expected_head()` | `observability.view` |
| Environment health summary | Observability service | `overview_metrics` | `observability.view` |
| Runtime-configuration coverage | Runtime engine (D.31) | `adoption_stats` | `observability.view` |
| Integration dependencies | Integration platform (D.24) | `overview_metrics` | `integration.view` |

## The not_configured domains (reported honestly)

The D.64 audit confirmed several domains have **no authoritative owner** and are declared `not_configured`,
never fabricated: **cloud resources, servers / hosts, containers / VMs, formal lifecycle state (planned →
active → deprecated → retired), deprecation records, platform retirement records, environment decommission
schedule, infrastructure host metadata, network topology, cloud-resource dependencies, and live deployment
execution / rollout status.** Services carry an operational `status` (operational / degraded / down /
maintenance / unknown) and environments an `active` flag — surfaced as a lifecycle **proxy** — but there is no
formal lifecycle-state record. Deployment references are declared metadata, not live execution.

## Registries, panels, dashboards

Five declarative registries — Environment (8) + Platform (9) + Deployment Topology (7) + Lifecycle (8) +
Infrastructure Dependency (7) = 39 domain entries (26 configured, 13 not_configured) — plus 35 panels and 8
dashboards (environment_overview, deployment_topology, platform_lifecycle, infrastructure_dependencies,
runtime_landscape, environment_governance, executive_platform_landscape, lifecycle_readiness). See
`ENVIRONMENT_REGISTRY.md`, `PLATFORM_LIFECYCLE_REGISTRY.md`, and `DEPLOYMENT_TOPOLOGY_REGISTRY.md`.

Each panel is **explainable** (explanation + source + deep link — a hard emit gate) and self-restricts to its
authoritative-source capability. A principal lacking the panel capability sees `restricted` (never the value or
count). A panel whose owner is `not_configured` is emitted `available=False` with
`config_status='not_configured'` — fail closed. **Derived** panels (executive_platform_posture,
lifecycle_readiness, runtime_landscape_summary, operational_lifecycle_state, the alignment / coverage panels)
carry `derived=True` and describe operational visibility only.

## Engine + surfaces

`service.py` exposes `compose_dashboard`, `list_dashboards`, `get_panel`, `environment_summary`, and the
record-scoped `client_platform_dependencies` / `household_platform_dependencies`. Dashboard-level authorization
admits **operations OR an executive** (`observability.view` / `analytics.executive`, via
`require_any_capability`).

- **Advisor Workspace** — an Environment & Platform Status panel (`environment_summary` in
  `workspace/service.py`).
- **Client 360 / Household 360** — a Platform Dependencies section that reports `not_configured`
  (available=False) HONESTLY: **there is no authoritative owner that maps a client / household record to a
  platform / environment / infrastructure dependency, so platform impact is never inferred at record scope, and
  internal infrastructure / topology / unrelated environment metadata is never exposed.**
- **Executive** — an Enterprise Platform & Environment Landscape dashboard reusing existing widgets
  (operational_health + runtime_health; **no new widget**).
- **AI Assist** — summarize-only grounding: AI may summarize firm-level environment coverage / lifecycle
  readiness / platform ownership / dependency coverage / topology visibility, and the record-scoped section is
  surfaced as an explicit `not_configured` fact so AI never invents environments, fabricates infrastructure,
  infers deployments, certifies platform health, modifies topology, provisions resources, or infers platform
  impact at record scope.

## Runtime gates, policy, analytics, diagnostics

- **Runtime gates** (`gate.py`): `environment_management.enabled`, `platform_lifecycle.enabled`,
  `deployment_topology.enabled`, `environment_ai_summary.enabled` — all distinct (no reused/unrelated gate),
  evaluated through `runtime.consumption.feature_enabled` with **no environment-variable fallback**. The layer
  also respects the runtime gate of every composed source.
- **Policy** composition alongside RBAC (`policy_ok(area)`), never bypassing either.
- **Analytics** (`metrics.py` → `analytics/{sources,metrics}.py`): four low-cardinality operational counters
  (environment_dashboards_composed, environment_panels_composed, environment_panel_failures,
  environment_authorization_failures) registered into the ONE Analytics Registry — no second metrics store.
- **Diagnostics** (`diagnostics.py`): an `observability.audit`-only report (gate snapshot, registry coverage,
  panel availability, governance findings).
- **Governance** (`governance.py`): `validate_environment_management()` returns `{ok, issue_count, findings}`
  and never raises — see `ENVIRONMENT_GOVERNANCE.md`.

## What it never does

No environment creation / deletion, no deployment execution, no provisioning, no lifecycle mutation, no
configuration writes, no cloud operations, no topology modification, no persistence, no second metrics registry,
no fabricated environment / deployment / infrastructure / topology / lifecycle / retirement, and no exposure of
any credential, secret, token, environment variable, connection string, private key, deployment payload, or
private topology.

## References
- Code: `app/services/environment_management/*`, `app/routes/environment_management.py`,
  `app/templates/environment_management/home.html`
- Surfaces: `app/services/workspace/service.py`, `app/services/client360/{registry,sections}.py`,
  `app/services/client360/household.py`, `app/services/executive_intelligence/registry.py`,
  `app/services/ai_assist/context.py`, `app/services/analytics/{sources,metrics}.py`
- Tests: `tests/test_environment_management.py`; ADR-069; `docs/ENVIRONMENT_REGISTRY.md`,
  `docs/PLATFORM_LIFECYCLE_REGISTRY.md`, `docs/DEPLOYMENT_TOPOLOGY_REGISTRY.md`, `docs/ENVIRONMENT_GOVERNANCE.md`
