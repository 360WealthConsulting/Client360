# Runtime Behavior Migration Inventory (Phase D.30)

Application behavior consumes the D.28 Runtime Configuration Engine through the standardized
consumption API (`RuntimeContext` / `app/services/runtime/consumption.py`). Every migration is
**behavior-preserving**: with no runtime feature/config defined, the legacy default is used, so
behavior is identical to pre-D.30. The runtime engine remains the sole evaluator; D.29 remains the
sole coordination layer; D.27 remains the sole metadata owner. This document is the durable inventory
(the `runtime_behaviors` registry is the machine-readable source of truth).

## Adoption

- **Migrated behaviors:** 6
- **Legacy (migratable, not yet migrated):** 1
- **Deterministic (no switch — excluded from the denominator):** 4
- **Adoption percentage** (migrated+retired ÷ migratable): **85.7%**

## Migrated modules

| Module | Behavior code | Runtime key | Call site | Legacy default |
|---|---|---|---|---|
| Automation | `automation.job_dispatch` | `automation.job.<type>` (feature) | `automation/dispatch.py::execute_dispatch` | enabled |
| Analytics | `analytics.executive_metrics` | `analytics.executive_metrics` (feature) | `analytics/metrics.py::compute_metric` | enabled (capability still required) |
| Benefits | `benefits.detector_windows` | `benefits.<window>` (config) | `benefits_detectors.py` (5 detectors, once each) | `app.config.benefits_*_days()` |
| Reporting | `reporting.optional_modules` | `reporting.module.<id>` (feature) | `reporting/service.py::list_definitions` | included |
| Microsoft 365 | `microsoft365.sync` | `microsoft365.sync` (feature) | `microsoft_{mail,calendar,document}_sync.py` | enabled |
| Microsoft 365 | `microsoft365.sharepoint_scope` | `microsoft365.sharepoint_site_ids` (config) | `microsoft_document_sync.py::discover_drives` | `MICROSOFT_SHAREPOINT_SITE_IDS` env |

## Remaining legacy behavior (migratable — future work)

| Module | Behavior code | Note |
|---|---|---|
| Advisor workspace | `advisor_workspace.sections` | Sections are capability-gated today; a runtime section gate is a future migration candidate. |

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
