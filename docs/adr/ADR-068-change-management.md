# ADR-068 — Enterprise Change Management, Release Governance & Configuration Intelligence: A Read-Only Composition, Not a Second ITSM / CI-CD / Deployment / Change Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Change Management / Release Governance / Configuration Intelligence);
Operations; Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.63 audit inventoried every change / release / configuration / deployment / evidence owner the
platform actually has, and — just as importantly — the ones it does **not**:

* **Architecture manifest (`docs/platform_architecture_manifest.yaml`)** — the declared release line, migration
  head, route count, and production capability count; the self-verifiable release evidence of record.
* **Observability health (D.26)** — `observability.health._expected_head()` is the **live Alembic script head**,
  and the live route count is `len(app.routes)`; the live ADR count is `docs/adr/ADR-*.md`, and the live Client
  360 / Executive dashboard counts are their registries. These are **self-verifiable** — the app can prove
  declared-vs-live drift itself.
* **Runtime Engine (D.31) + Policy Engine** — feature-flag adoption (`runtime.consumption.adoption_stats`) and
  policy coverage (`policy.registry.coverage`); the configuration owners of record. **Observability catalog** —
  `list_deployment_references` / `list_environment_profiles` (declared deployment metadata). **Observability
  alerts / incidents** and **Security incidents** — maintenance-window + operational + security signals.
  **Compliance Intelligence (D.47)** — open change-related exceptions.
* **Continuous integration** — CI produces build / E2E / regression / code-quality / architecture-guard /
  documentation-advisory evidence **per-commit**; it is REFERENCED, not live-read from the app.
* **Genuinely absent (not_configured):** there is **no live git / pull-request / branch / merge-commit /
  version-tag reader, no live CI-status reader, no deployment-execution / deployment-status owner, no rollback
  readiness / rollback-test owner, no production-verification / production-signoff owner, no change-calendar,
  and no post-change-review owner.** No `change_management.enabled` / `release_governance.enabled` /
  `configuration_intelligence.enabled` / `deployment_evidence.enabled` / `change_ai_summary.enabled` gate
  existed (Enterprise Risk declares change management as a *not_configured control*, not a gate — no collision).

There was **no change-management composition layer** unifying these into named, firm-wide views of change-domain
inventory, release readiness, CI-evidence verification, configuration governance, migration readiness,
deployment evidence, rollback readiness, and executive change posture. Building a second ITSM,
change-management, deployment, CI/CD, Git, CMDB, feature-flag, release-approval, incident, or
maintenance-scheduling platform would violate the "no second system" invariant and duplicate governed
infrastructure — and, worse, would invite fabricated deployment status, release approvals, or production
verification the platform cannot truthfully assert.

## Decision
Phase D.63 adds a **governed, read-only change-management composition layer**
(`app/services/change_management/`) with NO new capability, NO new metric, NO persistence, and NO mutation:

1. Four declarative **registries** (`registry.py`): `CHANGE_DOMAIN_REGISTRY` (15 change domains, each naming
   authoritative owner + read surface + **prohibited mutation surface** + evidence source + capabilities +
   runtime gate + deep links), `RELEASE_REGISTRY` (15 — 7 self-verifiable configured; branch / pull_request /
   merge_commit / version_tag / ci_status / deployment_status / rollback_artifact / production_verification
   **not_configured**), `CONFIGURATION_REGISTRY` (13, each with a sensitivity classification), and
   `CHANGE_EVIDENCE_REGISTRY` (20 — CI + self-verified + composed configured; PR approval / deployment
   verification / smoke test / rollback test / production sign-off / release notes / post-change review
   **not_configured**). Plus `PANEL_REGISTRY` (35) and `CHANGE_DASHBOARDS` (8).
2. Normalized read-models (`model.py`): `PanelResult` + `ChangeDashboard`, each explainable (a hard emit gate),
   carrying `derived` / `config_status`; **counts, status, identifiers, hashes, timestamps, coverage, and
   verification only — never a credential, secret, token, environment variable, connection string, private
   key, deployment payload, protected infrastructure detail, sensitive configuration value, or private
   incident narrative.**
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner.
   Self-verification panels read the manifest, `observability.health._expected_head()`, `len(app.routes)`, the
   ADR glob, and the live section / dashboard registries, computing **declared-vs-live drift**. CI-evidence
   panels reference per-commit CI evidence and explicitly disclaim that a green build certifies production.
   Live git / PR / CI status, deployment execution / status, rollback readiness, production verification, and
   post-change review panels are emitted `available=False` with `config_status='not_configured'` — honest,
   never a fabricated change / deployment / release. `executive_change_posture` and
   `derived_change_readiness_coverage` are **DERIVED** operational-readiness summaries (labeled `derived`).
4. The **change-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `change_summary`, plus `client_change_impact` / `household_change_impact` — composed from ONLY the
   record-scoped person-lineage read (Integration Hub `client_integrations`, over `governance.mdm.person_lineage`)
   for the external systems whose configuration changes could touch a client's data. **Firm-wide change /
   release / deployment / CI status is never exposed at client/household scope.** Dashboard-level authorization
   admits **operations OR an executive** (`observability.view` / `analytics.executive`, via
   `require_any_capability`).
5. **Runtime gates** (`change_management.enabled`, `release_governance.enabled`,
   `configuration_intelligence.enabled`, `deployment_evidence.enabled`, `change_ai_summary.enabled` — all
   distinct, no reused/unrelated gate, no environment-variable fallback) + the runtime gate of every composed
   source, **policy composition**, **analytics reuse** (four operational counters into the ONE Analytics
   Registry — no second registry), internal **diagnostics** (`observability.audit`), and a read-only
   **governance** checker that forbids mutation, persistence, any change / deployment / migration / flag
   mutation (`set_flag`, `upgrade`, `merge`, `deploy`, `rollback`, `approve`, `schedule_maintenance`,
   `acknowledge_incident`, `create_environment_profile`, …), a second metrics registry, and a fabricated
   change / deployment / release. AI Assist may summarize change / release / configuration readiness but never
   creates a branch, merges, deploys, runs a migration, changes a flag, approves a change, certifies
   production, implies a deployment, or exposes firm-wide change status at client scope.

No migration, no new table, no new capability, no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`. **A green build is not production certification, a merged pull request is not deployment, a
version tag does not prove rollout, a clean migration check does not prove application health, and an absent
incident does not prove a successful change** — every derived summary states this explicitly.

## Alternatives considered
- **A second ITSM / change-management / deployment / CI-CD / Git / CMDB / feature-flag / release-approval /
  incident / maintenance-scheduling platform.** Rejected: the architecture manifest, Observability health /
  catalog / alerts / incidents, the Runtime + Policy engines, Security incidents, Compliance Intelligence, and
  the CI pipeline are the authoritative owners; D.63 composes them. Governance forbids a second store and any
  change / deployment / migration / flag mutation. Where no owner exists (live git / PR / CI status, deployment
  execution, rollback, production verification, change calendar, post-change review), the entry declares
  `not_configured`.
- **A release-scoring / deployment-status engine that fabricates change success.** Rejected: any figure comes
  from an authoritative source (or is self-verified by the app); the derived summaries are deterministic,
  labeled `derived`, keep configured/not_configured/failed/stale visible, and are operational-readiness
  summaries — never approval, certification, deployment success, or production safety.
- **Live git / GitHub / CI polling from the request path.** Rejected: the app has no authoritative live git / CI
  reader, credentials must never enter the layer, and a live poll would couple request latency to an external
  service. CI evidence is referenced (produced per-commit); live git remains honestly `not_configured`.
- **Reusing an existing gate (e.g. `observability.enabled`).** Rejected: change management is a distinct
  concern; D.63 uses its own distinct master gates so the layer can be governed and disabled independently.

## Reasons for the decision
Change / release / configuration governance needs one operational view; the architecture manifest, Observability
health, the Runtime + Policy engines, and the CI pipeline already own or can self-verify every signal. A
read-only composition gives that view with full explainability (source + deep link) while every deployment stays
owned by its pipeline, every migration by Alembic, every flag by the Runtime Engine, and every incident by
Observability / Security. Emitting counts / status / verification only — and reporting the genuinely absent
owners as `not_configured` — keeps credentials, deployment payloads, and fabricated change status out of the
layer entirely, and keeps the honest distinction between *merged*, *deployed*, and *verified in production*.

## Rationale for avoiding a second change, deployment, or CI/CD platform
A second ITSM / deployment / CI-CD platform would require duplicated change requests, releases, deployments,
approvals, rollback artifacts, configuration state, and production-verification records, plus its own execution
model — duplicating governed infrastructure and creating reconciliation + drift + shadow-change risk, and
tempting the system to assert deployment status or production verification it cannot truthfully know. Composing
over the single manifest + Observability + Runtime + CI evidence keeps one source of truth and zero fabricated
change.

## Consequences

### Positive consequences
- One firm-wide change / release / configuration surface with no second ITSM / CI-CD / deployment / Git / CMDB /
  feature-flag / approval platform, and live self-verification of declared-vs-live route / migration / ADR /
  section / dashboard drift.
- Record scope + capability inherited from composed owners; a restricted panel leaks no value or count;
  client/household sections expose only the record-scoped affected-integration surface, never firm-wide change
  status.
- Zero schema change; Advisor Workspace Change & Release Status panel + Client 360 / Household 360 Change Impact
  sections + an Executive Enterprise Change & Release Governance dashboard (reusing existing widgets) + AI
  summarize-only.
- Live git / PR / CI status, deployment execution, rollback, production verification, and post-change review
  reported `not_configured` — honest; posture is an operational-readiness summary, never a deployment or
  production certification.

### Negative consequences and tradeoffs
- Dashboards recompute per request (no persistence); CI evidence is referenced per-commit, not live-read, so a
  panel reflects the pipeline's last recorded evidence, not a live build.
- Coverage is bounded by the owners' read surface; a genuinely new change signal (e.g. a real deployment owner)
  is added to the owning domain first, then surfaces here, replacing a `not_configured` entry.
- Live git / PR / CI status, deployment, rollback, and production verification stay `not_configured` until an
  authoritative owner exists — deliberately, to avoid fabricated change status.

## Enforcement
`tests/test_change_management.py` (four registries + integrity + duplicate-key prevention + configured-owner
validation + honest not_configured + distinct non-colliding master gates; explainable composition;
authorization — unauthorized → None, unentitled panel restricted; runtime + policy gates; the firm summary +
record-scoped client/household change-impact rollups that hide firm-wide change status; self-verification
drift panels; analytics reuse; diagnostics; routes registered + capability-gated operations OR executive; AI
summarize-only; the green-CI-is-not-production / merged-is-not-deployed / absent-incident-is-not-success
invariants; and the architecture invariants — no second ITSM/CI-CD/deployment platform, no persistence, no
mutation, no credential/deployment-payload exposure). `app/services/change_management/governance.py` enforces
the invariants at runtime. Route count, section registries, ADR count, and migration head are guarded by
`tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` +
`tests/test_household360_workspace.py` + `tests/test_executive_reporting.py` +
`tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate are exposed only within dashboards whose required capability
(`observability.view` / `analytics.executive`) the principal holds; each panel additionally self-restricts to
its authoritative-source capability (e.g. `security.view` for security findings, `compliance.supervise` for
change-related exceptions). Client-scoped sections compose ONLY the record-scoped person-lineage read —
firm-wide change / release / deployment / CI status is never exposed at client/household scope.

## Revisit conditions
Revisit when an authoritative live-git / pull-request / CI-status / deployment-execution / rollback /
production-verification / change-calendar / post-change-review owner is added (compose it here, replacing the
`not_configured` entries — never a second change/deployment/CI platform, never a fabricated status).

## References
- `app/services/change_management/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/change_management.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Change & Release Status panel in
  `app/services/workspace/service.py`; Executive dashboard in `app/services/executive_intelligence/registry.py`;
  AI grounding in `app/services/ai_assist/context.py`; analytics counters in
  `app/services/analytics/{sources,metrics}.py`
- Composes `docs/platform_architecture_manifest.yaml`, `app/services/observability/{health,catalog,alerts,incidents}.py`,
  `app/services/runtime/consumption.py`, `app/services/policy/*`, `app/services/security/incidents.py`,
  `app/services/compliance_intelligence/*`, `app/services/integration_hub/*`, the CI pipeline evidence
- `docs/ENTERPRISE_CHANGE_MANAGEMENT.md`, `docs/CHANGE_DOMAIN_REGISTRY.md`, `docs/RELEASE_REGISTRY.md`,
  `docs/CONFIGURATION_REGISTRY.md`, `docs/CHANGE_EVIDENCE_REGISTRY.md`, `docs/CHANGE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_change_management.py`; relates to ADR-026, ADR-031, ADR-047, ADR-060, ADR-066, ADR-067
