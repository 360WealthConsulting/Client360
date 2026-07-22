# ADR-034 — Distributed Runtime Coordination: cluster-safe convergence over the transactional outbox; the outbox is the sole coordination bus, the persisted generation the single source of truth

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Runtime/Configuration); Reliability / Operations (cluster
convergence, startup safety); Security / Authorization (RBAC ownership); Business Operations Owner
(Michael Shelton — cluster deployment requirements). Authorized compliance reviewer: Not yet
designated.

## Context
Phase D.28 built the Runtime Configuration Engine — deterministic evaluation over the D.27 metadata,
immutable snapshots, and an **in-process** cache. ADR-033 explicitly recorded the limitation that the
cache is per-process: in a multi-process/multi-node deployment each worker has an independent cache, a
refresh in one worker does not invalidate another's, and `RuntimeCache.version` is a purely in-process
monotonic counter that cannot be compared across workers. There was no mechanism to make the cluster
converge on one runtime version, no worker registry, and no cross-process cache invalidation.

The platform already has exactly one asynchronous coordination mechanism: the **transactional outbox**
(`app/platform/outbox.py` — `publish_event`, a `subscribe(name, handler)` registry, `dispatch_pending`,
and `outbox_processed_events` for per-(event, consumer) idempotency). The risks of adding cluster
coordination are: introducing a second messaging system; letting a runtime consumer coordinate
refreshes independently; a coordination failure blocking startup; comparing the non-portable
in-process cache version across workers; or the runtime engine mutating configuration metadata.

## Decision
Phase D.29 adds a **distributed runtime coordination layer** inside the runtime domain
(`app/services/runtime/{coordination,generations,events,cluster,coordination_common}.py`). It makes the
D.28 engine cluster-safe. **The runtime engine remains the sole evaluator; Configuration remains the
sole metadata owner; the transactional outbox is the sole coordination bus.** The layer owns only its
coordination metadata — a worker registry, a runtime version/generation history, and an append-only
coordination ledger — and performs no evaluation and no metadata edits.

**The persisted generation is the single source of truth.** A *runtime generation*
(`runtime_generations`) is an activated runtime version: a specific immutable snapshot (by
`config_hash`), monotonic, **deduplicated by `config_hash`** — activating a snapshot whose effective
config is identical to the current generation is a no-op (reuse), which enforces **"only one refresh
operation per runtime version."** Convergence is computed from the worker registry (a generation is
*converged* once every active worker's `runtime_version` ≥ its version). The in-process
`RuntimeCache.version` is used only for local observability, never for cross-process comparison.

**Coordination flows through the outbox, and only the outbox.** `engine.refresh()` (every refresh
path — manual, scheduled, automation, startup) now publishes `runtime.cache.rebuilt` +
`runtime.snapshot.activated` on the outbox **within its existing transaction** (atomic with the
snapshot/ledger write), then activates a generation. Runtime event types:
`runtime.snapshot.created/activated/invalidated`, `runtime.refresh.requested/completed`,
`runtime.cache.invalidated/rebuilt`, `runtime.override.changed`. A dark-launched consumer
(`on_runtime_event`, registered **only** from the gated `outbox_dispatcher_enabled()` block, exactly
like the notification/workflow consumers) reacts by converging the local worker.

**Convergence is pull-based and idempotent** — robust to any delivery gap. A worker converges by
`converge_worker()`: if its `runtime_version` is behind the current generation, it invalidates its
local cache, warms from the current snapshot, and records its new version; if already at the version
it is a no-op. This is the domain-level **replay protection** (reprocessing a stale coordination event,
or a heartbeat that already converged, does nothing) layered on top of the outbox's
`outbox_processed_events` (per event+consumer) idempotency. Every worker therefore converges on the
same persisted generation whether notified by the outbox **push** path or by the heartbeat **pull**
path — so a delivery failure degrades to eventual convergence, never divergence.

**Worker registry & lifecycle.** `runtime_workers` (keyed by a stable per-process `worker_uid` =
env `RUNTIME_WORKER_ID` or `hostname:pid`) + `runtime_worker_heartbeats`. Workers register at startup,
heartbeat on a cadence (and converge), and are **automatically expired** to `stale` when no heartbeat
arrives within the TTL. Individual heartbeats are **never** recorded as coordination events — only
major lifecycle events are (`cluster_initialized`, `worker_joined`, `worker_removed`,
`refresh_requested/completed`, `convergence_achieved`, `emergency_synchronization`, ...) to the
append-only `runtime_coordination_events` ledger.

**Startup / scheduler.** Startup registers the worker + joins the cluster **guarded** (after the
guarded hydrate) — a coordination failure never prevents boot. Gated scheduler jobs (OFF by default,
`runtime_coordination_enabled()`): `runtime-heartbeat` (heartbeat + converge) and
`runtime-stale-cleanup` (expire stale + recompute convergence). Automation gains a `runtime_coordination`
dispatch job (the sweep). The consumer registration is dark-launched from the gated outbox block.

**Security / Analytics / Observability.** Reuses the D.28 `runtime.*` capabilities (no new
capabilities): overview/workers/versions/convergence → `runtime.view`; coordinated refresh →
`runtime.execute`; diagnostics/event-history → `runtime.audit`; worker administration / emergency
synchronization → `runtime.admin`. `/runtime/cluster` routes gate every surface in-route. Analytics
gains cluster metrics (active workers, convergence %, stale workers, generations). Observability is
in-process + the coordination ledger + `diagnostics()` (convergence, cache drift, propagation latency,
stale workers).

## Alternatives considered
1. **Add a second messaging system (Redis pub/sub, a broker).** Rejected: the transactional outbox is
   the platform's one coordination mechanism; a second bus violates the single-mechanism invariant and
   adds infrastructure. The outbox carries the coordination events.
2. **Push-only invalidation (rely on exactly-once cross-process delivery).** Rejected as fragile: a
   missed/duplicated delivery could leave a worker divergent. Pull-based converge-if-behind off the
   persisted generation guarantees eventual convergence regardless of delivery.
3. **Compare the in-process `RuntimeCache.version` across workers.** Rejected: it is per-process and
   not portable. Convergence keys off the persisted `runtime_generations.version`/`config_hash`.
4. **Let each runtime consumer trigger its own refresh independently.** Rejected: refreshes are
   coordinated through the engine + generation (deduped by `config_hash`), so exactly one generation
   exists per runtime version.
5. **Block startup until the cluster converges.** Rejected: startup registration/join is guarded; a
   worker serves its last-known/local snapshot and converges asynchronously — availability first.

## Reasons for the decision
The cluster must converge on one runtime version deterministically, with cross-process cache
invalidation, without a second messaging system, without the engine mutating metadata, and without a
coordination failure ever blocking startup. A generation-based, pull-convergent design over the
existing outbox delivers this while preserving ADR-004 (RBAC/scope), ADR-009 (curated timeline — major
events only, never heartbeats), ADR-016 (linear migration), ADR-032 (D.27 owns metadata), and ADR-033
(D.28 sole evaluator; the per-process cache limitation this ADR resolves at the cluster level).

## Consequences
### Positive consequences
- Cluster-safe runtime configuration: every worker converges on the same persisted generation via the
  outbox push path and/or the heartbeat pull path; automatic stale-worker expiry; one refresh per
  version; full cluster observability + analytics.
- No second messaging system; the engine still never edits metadata; RBAC unchanged; startup never
  blocked.

### Negative consequences and tradeoffs
- Convergence is **eventual**, not instantaneous — a worker converges on the next delivered event or
  its next heartbeat; between a refresh and convergence, workers may briefly serve different versions
  (surfaced as `converging`/cache-drift in diagnostics).
- Cross-process delivery requires the **outbox dispatcher enabled** in each worker; when it is off,
  convergence relies solely on the heartbeat pull path (still correct, slower).
- Emergency overrides remain **in-process/per-worker** (ADR-033) — an emergency synchronization forces
  a coordinated refresh, but the override registry itself is not distributed (documented; a future
  change could persist it).
- The D.22 `JOB_TYPES` CHECK was widened again (`runtime_coordination`) — a documented, reversible
  cross-domain migration touch.

## Enforcement
- `app/services/runtime/{coordination,generations,events,cluster,coordination_common}.py`; engine hook
  in `app/services/runtime/engine.py` (`_publish_coordination`/`_activate_generation`). Table module
  `app/database/runtime_coordination_tables.py` (registered in `app/database/schema.py`; reflected in
  `app/db.py`). Migration `z2c3d4e5f6a7` (`runtime_workers`, `runtime_worker_heartbeats`,
  `runtime_generations`, append-only `runtime_coordination_events` + trigger + widened automation
  `JOB_TYPES`; reuses the D.28 `runtime.*` capabilities). Routes `app/routes/runtime_cluster.py`
  (`/runtime/cluster`, in-route `runtime.*` gating). Consumer dark-launched in
  `app/jobs/scheduler.py` (gated outbox block); gated heartbeat/stale-cleanup jobs; guarded cluster
  join in `app/main.py` lifespan. The transactional outbox, the runtime engine, the D.27 metadata, the
  RBAC middleware, and the D.5 golden are untouched. Coordination modules are registered in
  `source_producer_modules` (must not import composition layers). Tests:
  `tests/test_runtime_coordination.py`; manifest / platform-architecture / route-count guards updated.

## Exceptions
None currently approved. `administrator`/`record.read_all` scope bypass remains as defined by ADR-004.

## Revisit conditions
Persisting emergency overrides across the cluster, adding a real low-latency invalidation channel
(e.g. Postgres `LISTEN/NOTIFY` or Redis) alongside the outbox, introducing leader election / a
distributed lock for refreshes, or moving convergence off the pull model would each warrant a new or
superseding ADR.

## References
- `app/services/runtime/{coordination,generations,events,cluster,coordination_common}.py`,
  `app/routes/runtime_cluster.py`, `app/database/runtime_coordination_tables.py`, migration
  `migrations/versions/z2c3d4e5f6a7_runtime_coordination.py`
- Reused infra: `app/platform/outbox.py` (+ `events.py` Envelope), `app/services/runtime/` (D.28
  engine/cache/snapshots), `app/jobs/scheduler.py`, `app/main.py` lifespan, the Automation dispatch,
  the Analytics `Metric` registry
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_runtime_coordination.py`; relates to ADR-004, ADR-009, ADR-016, ADR-027, ADR-032,
  ADR-033
