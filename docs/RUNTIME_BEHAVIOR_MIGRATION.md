# Runtime Behavior Migration Inventory (Phase D.30)

Application behavior consumes the D.28 Runtime Configuration Engine through the standardized
consumption API (`RuntimeContext` / `app/services/runtime/consumption.py`). Every migration is
**behavior-preserving**: with no runtime feature/config defined, the legacy default is used, so
behavior is identical to pre-D.30. The runtime engine remains the sole evaluator; D.29 remains the
sole coordination layer; D.27 remains the sole metadata owner. This document is the durable inventory
(the `runtime_behaviors` registry is the machine-readable source of truth).

## Adoption &amp; runtime authority (updated D.31)

- **Adoption** (migrated+retired ÷ migratable): **100%** — every migratable behavior consumes the engine.
- **Runtime authority** (authoritative ÷ migratable): **71.4%** — the engine is the authoritative source (a D.27 definition exists) for 5 of 7 migratable behaviors.
- **Retired** (legacy fallback → documented compatibility shim; runtime authoritative): **4**
- **Migrated + authoritative** (fixed, seeded): **1** (advisor workspace)
- **Migrated + compatibility shim** (per-instance, unbounded — legacy default remains by policy): **2**
- **Deterministic** (no switch — excluded from the denominator): **4**
- **Governance:** definition coverage **100%**, **0** open issues.

## Authoritative / retired behaviors (runtime is the authoritative source)

| Module | Behavior code | Runtime definition seeded (D.31) | Status |
|---|---|---|---|
| Analytics | `analytics.executive_metrics` | feature flag `analytics.executive_metrics` (enabled) | **retired** |
| Microsoft 365 | `microsoft365.sync` | feature flag `microsoft365.sync` (enabled) | **retired** |
| Microsoft 365 | `microsoft365.sharepoint_scope` | config item `microsoft365.sharepoint_site_ids` (=env) | **retired** |
| Benefits | `benefits.detector_windows` | 5 config items `benefits.<window>_days` (=app.config defaults) | **retired** |
| Advisor workspace | `advisor_workspace.sections` | 3 flags `advisor_workspace.section.{work,tasks,exceptions}` (enabled) | **migrated + authoritative** |

Retired behaviors keep the consumption `default=` as a **documented compatibility shim** (`shim=True`):
served only if the runtime definition is absent (e.g. after a downgrade), and counted separately
(`compatibility_fallbacks`) so it is observable. In normal operation the engine is authoritative.

## Compatibility shims (per-instance, unbounded key spaces — permanent policy)

| Module | Behavior code | Why the legacy default remains |
|---|---|---|
| Automation | `automation.job_dispatch` (`automation.job.<type>`) | The dispatch registry is extensible and has an open-ended `custom` handler; `execute_dispatch` accepts any job-type string. A new type must default enabled → `default=True` shim stays. |
| Reporting | `reporting.optional_modules` (`reporting.module.<id>`) | Report definitions are user-created rows; the id space is unbounded → cannot pre-seed → `default=True` shim stays. |

## Deterministic / data-driven (no behavioral switch to migrate)

| Module | Behavior code | Why |
|---|---|---|
| Notifications | `notifications.channel_dispatch` | Channel enablement is data-driven via the F5.2 provider registry; the F5.5 `notification_dispatch.py` is a **certified frozen module** (a worktree-freeze test guards it), so there is no behavioral switch to migrate. |
| Operations | `operations.workspace` | Data-driven capacity/projects/tasks; no toggle. |
| Compliance | `compliance.workflow` | "No workflow, no automation" (documented); validation/scope guards only. |
| Document platform | `document_platform.behavior` | Deterministic CRUD/relationships; no toggle. |

Also deterministic (not registry rows): scheduler per-run wrappers, automation runner cycle.

## Infrastructure exclusions (remain startup concerns — NOT migrated)

- Database connectivity (`DATABASE_URL`), boot/CLI scripts (importers, matching).
- Session/crypto secret keys (`SESSION_SECRET`, `MICROSOFT_TOKEN_KEY`, the Fernet `*_KEY`s).
- Authentication providers (OIDC, dev-auth), security middleware.
- Microsoft 365 **credentials** (`MICROSOFT_CLIENT_ID/SECRET/TENANT_ID`, redirect uri) and provider
  initialization / OAuth / credential loading / connector configuration.
- Logging initialization (`LOG_LEVEL`/`LOG_FORMAT`), `ENVIRONMENT`/`IS_PRODUCTION`.
- **Scheduler-registration gates**: `automation_enabled()`, `outbox_dispatcher_enabled()`,
  `runtime_refresh_enabled()`, `runtime_coordination_enabled()`, and the interval/TTL helpers
  (`*_interval_seconds`, `runtime_worker_ttl_seconds`, `runtime_worker_id`) — used only at boot in
  `app/jobs/scheduler.py` to decide *whether a job is registered*. The benefits/insurance
  `*_scan_interval_minutes` helpers are likewise scheduler-registration only.

## How consumption works

- `RuntimeContext.feature_enabled(code, default)` / `.config(key, default)` — the per-request path
  (context attached by `RuntimeContextMiddleware`, no duplicate lookups).
- `consumption.feature_enabled(code, default=...)` / `consumption.config_value(key, default=...)` —
  the non-request path (scheduler/automation/detectors), builds a cheap cached-snapshot-backed
  context. Both return the runtime evaluation when defined, else the legacy `default`.
- Live counters (`consumption.adoption_stats()`) and the registry (`runtime_behaviors`) feed the
  Analytics runtime-adoption metrics and the `/runtime/behavior` surface. Major lifecycle events
  record to the `runtime_events` ledger (entity_type `behavior`); routine evaluations are never
  recorded.
