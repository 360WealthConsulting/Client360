# ADR-033 — Runtime Configuration Engine: deterministic evaluation over D.27 metadata, immutable snapshots, in-process cache; never edits metadata, never blocks startup

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Runtime/Configuration); Security / Authorization (RBAC ownership);
Reliability / Operations (startup safety); Business Operations Owner (Michael Shelton — runtime feature
activation requirements). Authorized compliance reviewer: Not yet designated.

## Context
Phase D.27 introduced the Enterprise Configuration domain: it owns configuration governance
**metadata** (categories/sets/items/versions, environment overrides, tenant/org/user preferences,
feature flags/rollouts, editions/capabilities/license-policies/assignments, platform options,
runtime-setting references). By deliberate decision (ADR-032) D.27 is **descriptive** — editing an
item or a flag records governance intent and changes no runtime behavior; there is no runtime
feature-toggle evaluator, and the running process still reads `app/config.py` env functions.

There was **no** runtime execution layer that safely turns that metadata into *effective* runtime
configuration and *evaluated* features. The risks of adding one are: it re-reads env / replaces the
runtime config; it mutates D.27 metadata from the request path; it resolves configuration repeatedly
per request; a bad configuration crashes application startup; or runtime evaluation bypasses RBAC /
record scope.

## Decision
Phase D.28 adds a **Runtime Configuration Engine** (`app/services/runtime/`) that owns **runtime
evaluation only**. D.27 remains the sole owner and mutator of configuration metadata; the engine
**reads** that metadata (through a single metadata reader) and **never writes it**. The engine writes
only its own tables: immutable `runtime_config_snapshots` and the append-only `runtime_events` ledger.

**Deterministic resolution precedence** (highest first), computed purely from the metadata snapshot:
1. runtime emergency override (in-process break-glass, engine-held) →
2. environment override → 3. tenant override → 4. organization override → 5. user preference →
6. configuration item value → 7. configuration default.
(Feature evaluation additionally applies: edition gate → target orgs → target roles → staged/
percentage rollout → feature default; rollout is a deterministic sha256 hash bucket so a subject
always lands in the same bucket.) Every resolution is pure and reproducible given the metadata.

**Immutable snapshots.** An effective-configuration snapshot (effective config + active features +
edition/license + `config_hash`) is composed deterministically and persisted immutably (trigger-
blocked) with a monotonic version. Major snapshots (startup / manual / refresh / scheduler) are
persisted; per-request context references the current snapshot rather than writing a row per request.
Snapshots support comparison and stale/drift detection (fresh hash vs stored hash).

**In-process cache.** A versioned, self-invalidating, TTL-expiring cache holds the current snapshot
and evaluation inputs so a request never re-resolves configuration. It exposes hit/miss/eval counters
(readable by an in-process Analytics `Metric` and surfaced to Observability). Safe refresh =
invalidate (bump version + clear) → rebuild snapshot → fall back to the last-known snapshot on
failure.

**Startup never blocks.** Hydration runs in the lifespan **guarded** (`try/except` + log) after
`start_scheduler()`; any configuration failure is swallowed and the engine serves defaults / the
last-known snapshot. This mirrors the failure-isolation already used by the scheduler workers.

**Per-request immutable context.** `RuntimeContextMiddleware` (registered before
`AuthenticationMiddleware`, so it runs after auth) attaches a lazily-built, cached `RuntimeContext`
(effective config, active features, edition, license, snapshot id) to `request.state`; a
`current_runtime_context` dependency exposes it. Building the context never raises into the request
and never mutates metadata.

**Reuse, never replace.** The env loaders (`app/config.py`), startup lifecycle, auth/session
middleware, RBAC, scheduler, observability, and analytics are reused as-is. A gated `runtime-refresh`
scheduler job (OFF by default, mirroring `automation_enabled()`) and a `runtime_refresh` Automation
dispatch job trigger a safe refresh. Analytics gains runtime metrics (active snapshots, cache hit
ratio, configuration resolutions, edition/feature utilization). Observability instrumentation is
in-process counters + the `runtime_events` ledger.

**Security.** Capabilities `runtime.view/manage/execute/audit*/admin*` (`*` = sensitive), gated
**in-route** (`/runtime` matches no middleware RULE). Emergency overrides require `runtime.admin`; the
safety report / audit history require `runtime.audit`. Runtime evaluation **never bypasses** RBAC,
capabilities, or record scope — every surface is gated, and the engine performs no privileged access.

**Safety detectors** (never raise): invalid configuration, circular feature-replacement dependency,
conflicting override, rollout conflict, invalid edition, orphan capability, stale snapshot —
`validate()` returns a structured report.

## Alternatives considered
1. **Make D.27 evaluate at runtime.** Rejected: ADR-032 keeps D.27 descriptive; mixing metadata
   authorship and runtime evaluation in one domain breaks the separation and the audit boundary.
2. **Resolve configuration per call, no cache/snapshot.** Rejected: non-deterministic within a
   request, repeated DB work, and no comparable/auditable record. Snapshots + per-request context
   give one deterministic resolution per request.
3. **Hydrate un-guarded in the lifespan.** Rejected: a bad configuration would crash startup. Hydration
   is self-guarded and falls back to defaults / last-known.
4. **A second middleware that fully resolves for every request (incl. static/health).** Rejected as
   wasteful; the context is lazily resolved only when a route reads it, and it reads from the cached
   snapshot (in-memory).
5. **Let the engine grant capabilities from editions.** Rejected: RBAC remains the sole access
   authority; edition capabilities are a read-only view used only to edition-gate features.

## Reasons for the decision
The platform needs to *safely consume* the D.27 metadata at runtime — deterministically, once per
request, without ever editing metadata, without blocking startup, and without bypassing RBAC. A
dedicated evaluation engine with immutable snapshots and an in-process cache delivers this while
preserving ADR-004 (server-side authz/scope), ADR-005 (sensitive data server-side), ADR-009 (curated
timeline — only major lifecycle events, never per-evaluation), ADR-016 (linear migration), and ADR-032
(D.27 owns metadata).

## Consequences
### Positive consequences
- One deterministic runtime evaluation layer with immutable, comparable snapshots and an in-process
  cache; a single resolution per request; safe refresh; guarded startup hydration.
- Strict separation preserved: the engine never edits metadata; the metadata domain never evaluates.
  Analytics gains runtime metrics; Observability gains runtime instrumentation; Automation can trigger
  a safe refresh.

### Negative consequences and tradeoffs
- The cache is **per-process** (in-memory). A multi-process deployment has independent caches; a
  refresh in one worker does not invalidate another's until its TTL/own refresh. Snapshots (persisted)
  are the shared, authoritative record; the cache is a per-process accelerator.
- Emergency overrides are **in-process** break-glass (not persisted) — intentional (fast, local,
  audited), but they do not survive a restart and are per-worker.
- Snapshots are point-in-time; between refreshes the effective config can drift from the metadata
  (detected by the stale-snapshot check, resolved by a refresh).
- The engine reads the D.27 `configuration_*` tables directly (it is the single trusted reader);
  startup has no principal, so it bypasses the D.27 service layer's principal-scoped sensitive
  stripping — acceptable because the engine only *reads* and only serves through RBAC-gated routes.

## Enforcement
- `app/services/runtime/` (`common, metadata_reader, resolution, features, editions, cache, snapshots,
  safety, context, engine, middleware, service`). Table module `app/database/runtime_tables.py`
  (registered in `app/database/schema.py`; reflected in `app/db.py`). Migration `z0a1b2c3d4e5`
  (`runtime_config_snapshots` immutable + `runtime_events` append-only, both trigger-blocked + 5
  `runtime.*` capabilities + widened automation `JOB_TYPES`). Routes `app/routes/runtime.py`
  (in-route `runtime.*` gating; `/runtime` matches no middleware RULE). Startup hydration in
  `app/main.py` lifespan (guarded); `RuntimeContextMiddleware` registered before auth. Scheduler
  `run_runtime_refresh` (gated `runtime_refresh_enabled()`); Automation `runtime_refresh` dispatch.
  The env loaders, RBAC, auth/session middleware, and the D.5 golden are untouched. Runtime service
  modules are registered in `source_producer_modules` (must not import composition layers). Tests:
  `tests/test_runtime_engine.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved. `administrator`/`record.read_all` scope bypass remains as defined by ADR-004.

## Revisit conditions
Adding a shared/distributed cache (e.g. Redis) with cross-process invalidation, persisting emergency
overrides, wiring the effective configuration back into the runtime config loaders that the process
reads at boot, or enforcing edition capabilities at the access layer would each warrant a new or
superseding ADR.

## References
- `app/services/runtime/`, `app/routes/runtime.py`, `app/database/runtime_tables.py`, migration
  `migrations/versions/z0a1b2c3d4e5_runtime_engine.py`
- Reused infra: `app/config.py`, `app/main.py` (lifespan/middleware), `app/jobs/scheduler.py`,
  `app/security/{middleware,dependencies}.py`, the Automation dispatch, the Analytics `Metric`
  registry, and the D.27 `configuration_*` read APIs
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_runtime_engine.py`; relates to ADR-004, ADR-005, ADR-009, ADR-016, ADR-027, ADR-031,
  ADR-032
