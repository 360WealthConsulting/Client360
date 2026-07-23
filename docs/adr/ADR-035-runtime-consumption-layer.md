# ADR-035 — Runtime Consumption Layer: application behavior consumes the runtime engine through a standardized, behavior-preserving API; infrastructure stays a startup concern

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Runtime/Configuration); Reliability / Operations (startup
boundary); Security / Authorization (RBAC ownership); Business Operations Owner (Michael Shelton —
behavioral configuration requirements). Authorized compliance reviewer: Not yet designated.

## Context
Phases D.27–D.29 built the dynamic-configuration stack: D.27 owns configuration **metadata**, D.28 is
the runtime **evaluation** engine (deterministic resolution, immutable snapshots, an in-process cache,
`RuntimeContext`), and D.29 makes the engine **cluster-safe** (worker convergence over the transactional
outbox). ADR-033 recorded the standing limitation that the engine was not yet *consumed* — the running
process still read `app/config.py` env functions for behavior, so the engine served a `/runtime`
surface and a per-request context but drove no real in-app decisions.

The platform is deliberately deterministic/data-driven — a behavioral audit (the D.30 migration
inventory) found very few genuine behavioral switches. The risks of an adoption phase are: changing
behavior while "migrating"; scattering ad-hoc runtime lookups; bypassing the engine (a second
evaluation path); bypassing RBAC; or migrating **infrastructure** configuration (DB, secrets, OAuth,
crypto, logging, scheduler registration, M365 credentials) that must remain a boot-time concern.

## Decision
Phase D.30 adds a **Runtime Consumption Layer** — a single, standardized, behavior-preserving API
through which application behavior consumes the D.28 engine — and migrates the identified behavioral
switches to it. **The runtime engine remains the sole evaluator; the D.29 coordination layer remains
the sole synchronization mechanism; D.27 remains the sole metadata owner.**

**Standardized consumption API.** `RuntimeContext` exposes `config(key, default)`,
`feature_enabled(code, default)`, `edition()`, `license()`, and `capabilities()` (the edition-gating
capability projection). The module `app/services/runtime/consumption.py` is the single behavioral
entry point for non-request and request callers alike: `feature_enabled(...)`, `config_value(...)`,
`edition()/license_code()/capabilities()`. Request handlers use the per-request `RuntimeContext`
attached by `RuntimeContextMiddleware` (no duplicate lookups); non-request callers (scheduler,
automation, detectors) obtain a cheap cached-snapshot-backed context via `runtime_context()`.

**Behavior-preserving legacy fallback.** Every consumption entry preserves current behavior: if the
engine has a definition for the feature/config key, its evaluation is used (a *runtime decision*);
otherwise the caller's `default` — the legacy value — is returned (a *legacy fallback*). Because no
runtime flags/config items are defined by default, migrated call sites behave **identically** until an
operator defines one. This makes the migration a pure adoption of the decision path, not a behavior
change.

**Migrated behaviors** (each behavior-preserving; legacy default = prior behavior):
- **Automation** — `execute_dispatch` consults `automation.job.<type>` (per-job-type enablement).
- **Analytics** — `compute_metric` consults `analytics.executive_metrics` for executive metrics (the
  `analytics.executive` **capability is still required** — RBAC is never bypassed).
- **Benefits** — the detector day-windows (`new_hire`/`open_enrollment`/`census`/`document`/`renewal`)
  resolve via `config_value("benefits.<key>", default=app.config.<fn>())`, once per detector.
- **Reporting** — `list_definitions` filters optional report modules via `reporting.module.<id>`.
- **Microsoft 365** — the mail/calendar/document `sync_*` functions consult `microsoft365.sync`
  (enablement); the SharePoint scope resolves via `config_value("microsoft365.sharepoint_site_ids",
  default=<env>)`.

**Infrastructure boundary (NOT migrated).** DB connectivity, session/crypto secret keys, auth/OIDC
providers, Microsoft **credentials** (client id/secret/tenant), logging init, `ENVIRONMENT`, and the
**scheduler-registration gates** (`automation_enabled`/`outbox_dispatcher_enabled`/`runtime_refresh_
enabled`/`runtime_coordination_enabled` + the interval/TTL helpers) remain boot-time infrastructure in
`app/config.py` / `app/jobs/scheduler.py`. Provider initialization / OAuth / credential loading /
connector configuration are untouched — only behavioral *enablement* moved.

**Adoption tracking.** A durable **behavioral-migration registry** (`runtime_behaviors`, seeded)
catalogs each behavioral switch and its status (`migrated`/`legacy`/`retired`/`deterministic`).
Adoption percentage = (migrated + retired) / migratable; `deterministic` behaviors (notifications —
the F5.5 dispatch is a certified frozen module and channel enablement is provider-registry-driven;
operations; compliance; document platform; scheduler per-run — data/capability-driven, no switch) are
documented and excluded from the denominator. Current adoption: **6 migrated / 1 legacy / 4
deterministic → 85.7%**. Live in-process counters (feature/config
lookups, runtime decisions vs legacy fallbacks) feed Analytics/Observability. Major behavioral events
(`runtime_behavior_adopted`, `legacy_behavior_retired`, `migration_completed`) record to the D.28
`runtime_events` append-only ledger (entity_type `behavior`); **routine feature evaluations are never
recorded**.

**Security.** Reuses the D.28 `runtime.*` capabilities (no new capabilities). `/runtime/behavior`
routes gate every surface in-route; consumption never bypasses RBAC/capabilities/organization scope
(capability checks stay at the call site — e.g. analytics executive still requires
`analytics.executive`).

## Alternatives considered
1. **Migrate everything to runtime flags, including infrastructure.** Rejected: DB/secrets/OAuth/crypto/
   logging/scheduler-registration are boot concerns; routing them through a runtime that itself needs
   the DB is circular and unsafe. The infrastructure boundary is explicit.
2. **Change behavior during migration (make runtime authoritative immediately).** Rejected: adoption
   must be behavior-preserving. Legacy defaults keep behavior identical until an operator defines a
   runtime value; the legacy path is *retired* only deliberately (a registry `retired` status).
3. **Ad-hoc `engine.evaluate_features`/`effective_config` calls at each site.** Rejected: a single
   `consumption` API prevents a second evaluation path and duplicate lookups, and centralizes adoption
   instrumentation.
4. **Force 100% adoption by inventing switches for deterministic modules.** Rejected: the deterministic
   modules have no genuine switch; fabricating one adds complexity for no behavior. They are documented
   as deterministic and excluded from the migratable denominator.

## Reasons for the decision
Application behavior must consume the runtime engine uniformly, without changing behavior, without a
second evaluation path, without bypassing RBAC, and without migrating infrastructure. A standardized
consumption API with legacy-default fallback plus a durable adoption registry delivers this while
preserving ADR-004 (RBAC/scope), ADR-005 (server-side), ADR-009 (curated events), ADR-032 (D.27 owns
metadata), ADR-033 (D.28 sole evaluator), and ADR-034 (D.29 sole coordination).

## Consequences
### Positive consequences
- One standardized, behavior-preserving consumption path; the engine becomes the single behavioral
  decision source for the migrated switches; adoption is tracked durably + live. No behavior change,
  no duplicate lookups, no RBAC bypass, no infrastructure migration.

### Negative consequences and tradeoffs
- Adoption is **partial by design** — deterministic modules have no switch (documented), and the
  advisor-workspace section gate remains a `legacy` candidate for a future phase. Registry adoption is
  measured over the migratable set, not all modules.
- Migrated call sites now build/read a `RuntimeContext` (cheap, cached-snapshot-backed); a runtime
  outage degrades to the legacy default (behavior unchanged) but adds a guarded lookup.
- Runtime values only take effect when an operator defines them in D.27 metadata; until then behavior
  is identical to pre-D.30 (this is the intended behavior-preserving posture, not a limitation to fix).

## Enforcement
- `app/services/runtime/{consumption,behavior}.py` + `RuntimeContext` (context.py) methods; engine
  `context_for` populates `edition_capabilities`. Table module
  `app/database/runtime_behavior_tables.py` (registered in `schema.py`; reflected in `db.py`).
  Migration `z4e5f6a7b8c9` (`runtime_behaviors` + seed). Migrated call sites:
  `automation/dispatch.py`, `analytics/metrics.py`, `benefits_detectors.py`, `reporting/service.py`,
  `microsoft_{mail,calendar,document}_sync.py` (the certified-frozen `notification_dispatch.py` was
  deliberately NOT modified — notifications are deterministic). Routes
  `app/routes/runtime_behavior.py` (`/runtime/behavior`, in-route `runtime.*` gating). Analytics
  metrics + the `runtime_events` behavioral ledger. The infrastructure configuration, provider init,
  OAuth, crypto, logging, scheduler registration, and the D.5 golden are untouched. Consumption
  modules are registered in `source_producer_modules` (must not import composition layers). Tests:
  `tests/test_runtime_consumption.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved. `administrator`/`record.read_all` scope bypass remains as defined by ADR-004.

## Revisit conditions
Retiring a legacy fallback (making runtime authoritative for a behavior), migrating the advisor-
workspace section gate, or wiring the effective configuration back into the boot-time config loaders
would each warrant a new or superseding ADR.

## References
- `app/services/runtime/{consumption,behavior,context}.py`, `app/routes/runtime_behavior.py`,
  `app/database/runtime_behavior_tables.py`, migration
  `migrations/versions/z4e5f6a7b8c9_runtime_behavior.py`, `docs/RUNTIME_BEHAVIOR_MIGRATION.md`
- Migrated: `app/services/automation/dispatch.py`, `app/services/analytics/metrics.py`,
  `app/services/benefits_detectors.py`, `app/services/reporting/service.py`,
  `app/services/notification_dispatch.py`, `app/jobs/microsoft_{mail,calendar,document}_sync.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_runtime_consumption.py`; relates to ADR-004, ADR-005, ADR-009, ADR-032, ADR-033, ADR-034
