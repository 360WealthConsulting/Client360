# ADR-069 — Enterprise Environment Management, Deployment Topology & Platform Lifecycle Intelligence: A Read-Only Composition, Not a Second CMDB / Infrastructure Platform / Deployment Orchestrator

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Environment Management / Deployment Topology / Platform Lifecycle);
Operations; Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.64 audit inventoried every environment / infrastructure / topology / lifecycle / dependency
owner the platform actually has, and the ones it does **not**:

* **Observability catalog (D.26)** — the authoritative owner of environment profiles
  (`list_environment_profiles`: code, name, environment [production / staging / development / test], region,
  active), deployment references (`list_deployment_references`: code, version, migration_head,
  environment_profile_id, released_at), the service inventory (`list_services`: service_type, criticality,
  status, owner_user_id, reference_type / id), and the service dependency graph (`list_dependencies`:
  dependency_type [hard / soft / runtime]). Mutations (`create_environment_profile`,
  `create_deployment_reference`, `create_service`, `set_service_status`, `add_dependency`) are the prohibited
  surface.
* **Observability health (D.26)** — runtime snapshots (`list_runtime_snapshots`: database_ok,
  scheduler_running, migration_head, migration_in_sync, environment_profile_id, summary [ready / not_ready])
  and the **live** Alembic script head (`_expected_head`). Mutation: `capture_runtime_snapshot`.
* **Observability service (D.26)** — `overview_metrics` (operational / degraded services, failed health
  checks). **Runtime engine (D.31)** — `adoption_stats` / `edition` (configuration coverage). **Integration
  platform (D.24)** — `overview_metrics` (providers / connected connectors = integration dependencies).
* **Genuinely absent (not_configured):** there is **no cloud-resource / server / container / VM inventory, no
  host / network topology owner, no formal lifecycle-state record (planned → active → deprecated → retired —
  services have an operational `status` and environments an `active` flag, but no lifecycle record), no
  deprecation / retirement / decommission-schedule owner, and no live deployment-execution / rollout owner**
  (deployment references are declared metadata, not live execution). There is also **no authoritative owner
  that maps a client / household RECORD to a platform / environment / infrastructure dependency.**

No `environment_management.enabled` / `platform_lifecycle.enabled` / `deployment_topology.enabled` /
`environment_ai_summary.enabled` gate existed. There was **no environment-management composition layer**
unifying these into named, firm-wide views of environment inventory, deployment topology, runtime landscape,
platform ownership, lifecycle state, and infrastructure dependencies. Building a second CMDB,
infrastructure-management platform, cloud-management platform, deployment orchestrator, asset inventory,
configuration database, environment manager, or monitoring platform would violate the "no second system"
invariant and duplicate governed infrastructure — and would invite fabricated environments, deployments, or
lifecycle state the platform cannot truthfully assert.

## Decision
Phase D.64 adds a **governed, read-only environment-management composition layer**
(`app/services/environment_management/`) with NO new capability, NO new metric, NO persistence, and NO
mutation:

1. Five declarative **registries** (`registry.py`): `ENVIRONMENT_REGISTRY` (8 — cloud provisioning
   not_configured), `PLATFORM_REGISTRY` (9 — cloud resources / servers / containers / VMs not_configured),
   `DEPLOYMENT_TOPOLOGY_REGISTRY` (7 — deployment execution / rollout not_configured), `LIFECYCLE_REGISTRY`
   (8 — formal lifecycle state / deprecation / retirement / decommission not_configured), and
   `INFRASTRUCTURE_DEPENDENCY_REGISTRY` (7 — host metadata / network topology / cloud-resource dependencies
   not_configured), each naming authoritative owner + read surface + **prohibited mutation surface** + evidence
   source + capabilities + runtime gate + environment scope + deep links + config status. Plus `PANEL_REGISTRY`
   (35) and `ENVIRONMENT_DASHBOARDS` (8).
2. Normalized read-models (`model.py`): `PanelResult` + `EnvironmentDashboard`, each explainable (a hard emit
   gate), carrying `derived` / `config_status`; **counts, status, identifiers, coverage, and verification only
   — never a credential, secret, token, environment variable, connection string, private key, deployment
   payload, protected infrastructure detail, private topology, or sensitive configuration value.**
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Observability catalog / health / service owners, the Runtime engine, the Integration platform);
   fail-closed; every panel self-restricts. Cloud resources / servers / containers / VMs / formal lifecycle /
   retirement / decommission / host & network topology / live deployment execution panels are emitted
   `available=False` with `config_status='not_configured'` — honest, never fabricated. `executive_platform_posture`,
   `lifecycle_readiness`, `runtime_landscape_summary`, and the alignment / coverage panels are **DERIVED**
   operational-visibility summaries (labeled `derived`).
4. The **environment-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `environment_summary`, plus `client_platform_dependencies` / `household_platform_dependencies` — which
   report `not_configured` (available=False) HONESTLY, because **no authoritative owner maps a record to a
   platform / environment / infrastructure dependency; platform impact is never inferred at record scope, and
   internal infrastructure / topology / unrelated environment metadata is never exposed.** Dashboard-level
   authorization admits **operations OR an executive** (`observability.view` / `analytics.executive`, via
   `require_any_capability`).
5. **Runtime gates** (`environment_management.enabled`, `platform_lifecycle.enabled`,
   `deployment_topology.enabled`, `environment_ai_summary.enabled` — all distinct, no reused/unrelated gate,
   no environment-variable fallback) + the runtime gate of every composed source, **policy composition**,
   **analytics reuse** (four operational counters into the ONE Analytics Registry — no second registry),
   internal **diagnostics** (`observability.audit`), and a read-only **governance** checker that forbids
   mutation, persistence, any environment / deployment / provisioning / topology / lifecycle mutation
   (`create_environment_profile`, `create_deployment_reference`, `create_service`, `set_service_status`,
   `add_dependency`, `capture_runtime_snapshot`, `set_flag`, `provision`, `deploy`, `decommission`, `retire`,
   …), a second metrics registry, and a fabricated environment / deployment / infrastructure. AI Assist may
   summarize environment coverage / lifecycle readiness / platform ownership / dependency coverage / topology
   visibility but never invents environments, fabricates infrastructure, infers deployments, certifies platform
   health, modifies topology, or provisions resources.

No migration, no new table, no new capability, no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`. **Environment metadata is not live infrastructure, a deployment reference is not a deployment,
an active flag is not a lifecycle guarantee, and a runtime snapshot is not continuous environment health** —
every derived summary states this explicitly.

## Alternatives considered
- **A second CMDB / infrastructure-management platform / cloud-management platform / deployment orchestrator /
  asset inventory / configuration database / environment manager / monitoring platform.** Rejected: the
  Observability catalog / health / service owners, the Runtime engine, and the Integration platform are the
  authoritative owners; D.64 composes them. Governance forbids a second store and any environment / deployment
  / provisioning / topology / lifecycle mutation. Where no owner exists (cloud resources, servers, containers,
  VMs, formal lifecycle, retirement, decommission, host / network topology, live deployment execution), the
  entry declares `not_configured`.
- **An environment-scoring / topology-health engine that fabricates environment health.** Rejected: any figure
  comes from an authoritative source; the derived summaries are deterministic, labeled `derived`, keep
  configured / not_configured / not_ready visible, and are operational-visibility summaries — never a certified
  environment health, deployment status, provisioning outcome, or retirement decision.
- **A record-scoped platform-impact inference at Client 360 / Household 360.** Rejected: no authoritative owner
  maps a record to a platform / environment / infrastructure dependency, and inferring one would fabricate
  platform impact and risk exposing internal infrastructure at record scope. The section reports
  `not_configured` honestly.
- **Reusing an existing gate (e.g. `observability.enabled`).** Rejected: environment management is a distinct
  concern; D.64 uses its own distinct master gates so the layer can be governed and disabled independently.

## Reasons for the decision
Environment / platform / deployment / lifecycle governance needs one operational view; the Observability
catalog / health / service owners, the Runtime engine, and the Integration platform already own every signal
with the correct scoping. A read-only composition gives that view with full explainability (source + deep link)
while every environment profile, deployment reference, service, dependency, and runtime snapshot stays owned by
Observability, every flag by the Runtime engine, and every connector by the Integration platform. Emitting
counts / status / coverage only — and reporting the genuinely absent owners as `not_configured` — keeps
credentials, connection strings, private topology, and fabricated environment state out of the layer entirely.

## Rationale for avoiding a second CMDB, infrastructure platform, or deployment manager
A second CMDB / infrastructure platform / deployment manager would require duplicated environments, cloud
resources, servers, containers, VMs, deployment topology, lifecycle records, retirement records, and
infrastructure metadata, plus its own provisioning + deployment model — duplicating governed infrastructure and
creating reconciliation + drift + shadow-inventory risk, and tempting the system to assert environment health
or deployment status it cannot truthfully know. Composing over the single Observability catalog + health +
Runtime + Integration keeps one source of truth and zero fabricated environments.

## Consequences

### Positive consequences
- One firm-wide environment / platform / deployment / lifecycle surface with no second CMDB / infrastructure /
  cloud / deployment / asset / configuration / environment / monitoring platform.
- Record scope + capability inherited from composed owners; a restricted panel leaks no value or count;
  Client 360 / Household 360 sections report `not_configured` honestly and never infer platform impact or
  expose internal infrastructure.
- Zero schema change; Advisor Workspace Environment & Platform Status panel + Client 360 / Household 360
  Platform Dependencies sections + an Executive Enterprise Platform & Environment Landscape dashboard (reusing
  existing widgets) + AI summarize-only.
- Cloud resources / servers / containers / VMs / formal lifecycle / retirement / decommission / host & network
  topology / live deployment execution reported `not_configured` — honest; posture is an operational-visibility
  summary, never a certified environment health or retirement decision.

### Negative consequences and tradeoffs
- Dashboards recompute per request (no persistence); runtime snapshots are point-in-time probes, not continuous
  environment health.
- Coverage is bounded by the owners' read surface; a genuinely new environment / infrastructure signal (e.g. a
  real cloud-resource owner) is added to the owning domain first, then surfaces here, replacing a
  `not_configured` entry.
- Formal lifecycle state, retirement records, cloud / host / network topology, and live deployment execution
  stay `not_configured` until an authoritative owner exists — deliberately, to avoid fabricated environment
  state.

## Enforcement
`tests/test_environment_management.py` (five registries + integrity + duplicate-key prevention +
configured-owner validation + honest not_configured + distinct non-colliding master gates; explainable
composition; authorization — unauthorized → None, unentitled panel restricted; runtime + policy gates; the firm
summary + record-scoped client / household platform-dependency sections that report not_configured and never
infer platform impact; analytics reuse; diagnostics; routes registered + capability-gated operations OR
executive; AI summarize-only; the environment-metadata-is-not-live-infrastructure /
deployment-reference-is-not-a-deployment / active-flag-is-not-a-lifecycle-guarantee invariants; and the
architecture invariants — no second CMDB / infrastructure platform / deployment orchestrator, no persistence,
no mutation, no fabricated / unauthorized environment exposure).
`app/services/environment_management/governance.py` enforces the invariants at runtime. Route count, section
registries, ADR count, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` +
`tests/test_executive_reporting.py` + `tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate are exposed only within dashboards whose required capability
(`observability.view` / `analytics.executive`) the principal holds; each panel additionally self-restricts to
its authoritative-source capability (e.g. `integration.view` for integration dependencies). Client-scoped
sections report `not_configured` — no authoritative record-scoped platform / environment / infrastructure owner
exists, and platform impact is never inferred at record scope.

## Revisit conditions
Revisit when an authoritative cloud-resource / server / container / VM inventory, host / network-topology,
formal lifecycle-state, deprecation / retirement / decommission, or live deployment-execution owner is added
(compose it here, replacing the `not_configured` entries — never a second CMDB / infrastructure platform /
deployment orchestrator, never a fabricated state).

## References
- `app/services/environment_management/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/environment_management.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Environment & Platform Status panel in
  `app/services/workspace/service.py`; Executive dashboard in `app/services/executive_intelligence/registry.py`;
  AI grounding in `app/services/ai_assist/context.py`; analytics counters in
  `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/observability/{catalog,health,service}.py`, `app/services/runtime/consumption.py`,
  `app/services/integration/service.py`, the Runtime + Policy engines
- `docs/ENTERPRISE_ENVIRONMENT_MANAGEMENT.md`, `docs/ENVIRONMENT_REGISTRY.md`,
  `docs/PLATFORM_LIFECYCLE_REGISTRY.md`, `docs/DEPLOYMENT_TOPOLOGY_REGISTRY.md`,
  `docs/ENVIRONMENT_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_environment_management.py`; relates to ADR-024, ADR-026, ADR-031, ADR-060, ADR-068
