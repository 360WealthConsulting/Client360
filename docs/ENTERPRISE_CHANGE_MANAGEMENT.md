# Enterprise Change Management, Release Governance & Configuration Intelligence (Phase D.63)

`app/services/change_management/` is a governed, **read-only composition** that provides a unified, governed
view of the firm's change posture — change-domain inventory, release readiness, CI-evidence verification,
configuration governance, migration readiness, deployment evidence, rollback readiness, and executive change
posture. It is **not** a second ITSM, change-management, deployment, CI/CD, Git, CMDB, feature-flag,
release-approval, incident, or maintenance-scheduling platform: **no new capability, no new metric, no
persistence, no mutation, no duplicated change data, no migration** (single Alembic head `n5s6u7p8v9w0`).

> These are **operational-readiness summaries**, never approval, certification, deployment success, or
> production safety. **A green build is not production certification, a merged pull request is not deployment,
> a version tag does not prove rollout, a clean migration check does not prove application health, and an
> absent incident is not change success.**

> **No secrets, ever.** No credential, secret, token, environment variable, connection string, private key,
> deployment payload, protected infrastructure detail, sensitive configuration value, private incident
> narrative, or repository credential is ever carried in a panel — counts, status, identifiers, hashes,
> timestamps, coverage, and verification results only.

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| Declared release line / migration head / route + capability counts | Architecture manifest | `platform_architecture_manifest.yaml` meta | `observability.view` |
| **Live** migration head (declared-vs-live drift) | Observability health (D.26) | `observability.health._expected_head()` | `observability.view` |
| **Live** route count (declared-vs-live drift) | App router | `len(app.routes)` | `observability.view` |
| **Live** ADR count (sequential-drift) | ADR corpus | `docs/adr/ADR-*.md` glob | `observability.view` |
| **Live** Client 360 section / Executive dashboard counts | Client 360 / Executive registries | `SECTIONS` / `DASHBOARD_REGISTRY` | `observability.view` |
| CI build / E2E / regression / code-quality / architecture-guard / doc-advisory evidence | Continuous integration | referenced per-commit (not live-read) | `observability.view` |
| Feature-flag adoption / runtime-gate coverage | Runtime Engine (D.31) | `runtime.consumption.adoption_stats` | `observability.view` |
| Policy-engine coverage | Policy Engine | `policy.registry.coverage` | `observability.view` |
| Deployment references / environment profiles | Observability catalog | `list_deployment_references` / `list_environment_profiles` | `observability.view` |
| Maintenance windows | Observability alerts | `alerts.metrics` | `observability.view` |
| Change-related operational incidents | Observability incidents | `incidents.metrics` | `observability.view` |
| Change-related security findings | Security incidents | `security.incidents.metrics` | `security.view` |
| Change-related compliance exceptions | Compliance Intelligence (D.47) | `supervisory_dashboard` | `compliance.supervise` |
| Documentation status | Knowledge Management (D.62) | `knowledge_summary` | `documents.view` |
| Record-scoped client/household change impact | Integration Hub / MDM lineage | `client_integrations` / `household_integrations` | (record scope) |

## The not_configured domains (reported honestly)

The D.63 audit confirmed several domains have **no authoritative owner** and are declared `not_configured`,
never fabricated: **live git / pull-request / branch / merge-commit / version-tag readers, live CI-status,
deployment execution / status, rollback readiness / rollback-test, production verification / production
sign-off, change calendar, and post-change review.** CI produces build / test / guard evidence per-commit — it
is **referenced**, not live-read — and every CI panel explicitly disclaims that a green build certifies
production. The Observability catalog owns declared **deployment references** (configured), but deployment
**execution / live status** has no owner (`not_configured`).

## Registries, panels, dashboards

Four declarative registries — Change Domains (15) + Release Entries (15) + Configuration Entries (13) + Change
Evidence (20) — plus 35 panels and 8 dashboards (change_overview, release_readiness, ci_verification,
configuration_governance, migration_readiness, deployment_evidence, rollback_readiness,
executive_change_posture). See `CHANGE_DOMAIN_REGISTRY.md`, `RELEASE_REGISTRY.md`, `CONFIGURATION_REGISTRY.md`,
and `CHANGE_EVIDENCE_REGISTRY.md`.

Each panel is **explainable** (explanation + source + deep link — a hard emit gate) and self-restricts to its
authoritative-source capability. A principal lacking the panel capability sees `restricted` (never the value or
count). A panel whose owner is `not_configured` is emitted `available=False` with
`config_status='not_configured'` — fail closed. **Derived** panels (executive_change_posture,
derived_change_readiness_coverage, the governance / route / ADR / section / dashboard verification panels) carry
`derived=True` and describe operational readiness only.

## Self-verification (the honest core)

The layer's configured core is **self-verifiable**: the app compares the manifest-**declared** route count /
migration head against the **live** `len(app.routes)` / `observability.health._expected_head()`, checks the ADR
corpus is sequential, and reads the live Client 360 section + Executive dashboard counts. This is how the layer
tells the truth without a live git / CI / deployment reader — it verifies what it can actually observe and
declares the rest `not_configured`.

## Engine + surfaces

`service.py` exposes `compose_dashboard`, `list_dashboards`, `get_panel`, `change_summary`, and the
record-scoped `client_change_impact` / `household_change_impact`. Dashboard-level authorization admits
**operations OR an executive** (`observability.view` / `analytics.executive`, via `require_any_capability`).

- **Advisor Workspace** — a Change & Release Status panel (`change_summary` in `workspace/service.py`).
- **Client 360 / Household 360** — a Change Impact section composing ONLY the record-scoped affected-integration
  surface (the external systems whose configuration changes could touch this record's data). **Firm-wide change
  / release / deployment / CI status is never exposed at client/household scope.**
- **Executive** — an Enterprise Change & Release Governance dashboard reusing existing widgets
  (compliance_workload + operational_health + runtime_health; **no new widget**).
- **AI Assist** — summarize-only grounding: AI summarizes change / release / configuration readiness and a
  record-scoped affected-integration count, but never creates a branch, merges, deploys, runs a migration,
  changes a flag, approves a change, certifies production, implies a deployment, or exposes firm-wide change
  status at client scope.

## Runtime gates, policy, analytics, diagnostics

- **Runtime gates** (`gate.py`): `change_management.enabled`, `release_governance.enabled`,
  `configuration_intelligence.enabled`, `deployment_evidence.enabled`, `change_ai_summary.enabled` — all
  distinct (no reused/unrelated gate), evaluated through `runtime.consumption.feature_enabled` with **no
  environment-variable fallback**. The layer also respects the runtime gate of every composed source.
- **Policy** composition alongside RBAC (`policy_ok(area)`), never bypassing either.
- **Analytics** (`metrics.py` → `analytics/{sources,metrics}.py`): four low-cardinality operational counters
  (change_dashboards_composed, change_panels_composed, change_panel_failures, change_authorization_failures)
  registered into the ONE Analytics Registry — no second metrics store.
- **Diagnostics** (`diagnostics.py`): an `observability.audit`-only report (gate snapshot, registry coverage,
  panel availability, governance findings).
- **Governance** (`governance.py`): `validate_change_management()` returns `{ok, issue_count, findings}` and
  never raises — see `CHANGE_GOVERNANCE.md`.

## What it never does

No branch / merge / commit / tag mutation, no deployment execution, no migration execution, no runtime-flag
mutation, no release approval, no rollback execution, no maintenance scheduling, no configuration writes, no
incident acknowledgement, no persistence, no second metrics registry, no fabricated change / deployment /
release / rollback / production verification, and no exposure of any credential, secret, token, environment
variable, connection string, private key, deployment payload, or sensitive configuration value.

## References
- Code: `app/services/change_management/*`, `app/routes/change_management.py`,
  `app/templates/change_management/home.html`
- Surfaces: `app/services/workspace/service.py`, `app/services/client360/{registry,sections}.py`,
  `app/services/client360/household.py`, `app/services/executive_intelligence/registry.py`,
  `app/services/ai_assist/context.py`, `app/services/analytics/{sources,metrics}.py`
- Tests: `tests/test_change_management.py`; ADR-068; `docs/CHANGE_DOMAIN_REGISTRY.md`,
  `docs/RELEASE_REGISTRY.md`, `docs/CONFIGURATION_REGISTRY.md`, `docs/CHANGE_EVIDENCE_REGISTRY.md`,
  `docs/CHANGE_GOVERNANCE.md`
