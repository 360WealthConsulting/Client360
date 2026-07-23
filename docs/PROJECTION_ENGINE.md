# Projection Engine (Phase D.36)

`app/services/projections/engine.py` is the deterministic read-model runtime. It reads the ordered
outbox event log (`outbox_events` — it never mutates it) and applies events to the disposable read-model
tables. It never touches an authoritative table and never affects a business transaction.

## A projection definition declares

- **projection id / name / category / owner**
- **read table** — the disposable `rm_*` table it owns
- **subscribed event types** — the domain events it consumes (`*` = all, the Activity Feed)
- **schema version** — the read-model schema version
- **rebuild strategy** — `full` (truncate + replay)
- **dependencies** — other projections it depends on (the dependency graph)
- **status** — active / deprecated / retired
- and an **`apply(conn, event)`** handler (in code) that copies references/statuses/timestamps from the
  (references-only) event payload into a read row. No business logic; touches only the read table.

## Runtime state (per projection)

`health`, `last_processed_event_id` (the outbox checkpoint), `last_processed_at`, `events_processed`,
`failed_events`, `rebuild_count`, `replay_count`, `last_rebuild/replay_at` + `duration_ms`,
`last_validation_ok`, `rebuild_history`, `lag` (computed).

## Operations

| Operation | Behavior |
|---|---|
| **`process(id, incremental=True)`** | Apply new outbox events (id > checkpoint) — or all events — to the read model. Per-event failure isolation (a savepoint per event); failures counted, never fatal. Advances the checkpoint. |
| **`rebuild(id)`** | Full rebuild: `DELETE FROM` the read table, reset the checkpoint, replay every matching event. Deterministic. Records rebuild count/duration/history. |
| **`incremental` (via tick)** | The scheduler tick (`tick()`) runs `process(incremental=True)` for every active projection — the dark-launched background path. |
| **`reset(id)`** | Delete the read model + reset the checkpoint to unbuilt. Events untouched. |
| **`replay(id)`** | Deterministic rebuild, tracked as a replay. |
| **`validate(id)`** | Rebuild twice + compare content signatures (excluding the surrogate id + volatile `updated_at`) — proves the read model is deterministic given the events. Records `last_validation_ok`. |
| **`lag(id)`** | Count of matching outbox events after the checkpoint (how far behind). |
| **`size(id)`** | Read-model row count. |
| **`health(id)`** | `unbuilt` / `healthy` / `lagging` / `failed` / `building`. |

## Determinism

Applying the ordered events is a pure function, and the apply handlers are idempotent upserts keyed on
the natural id (replaying the same events yields the same rows). `validate` proves this by rebuilding
twice and comparing a content hash. Recovery is always: `reset` + `replay` (or just `rebuild`).

## Failure isolation

Each event is applied inside a savepoint. A malformed event (e.g. a missing reference) rolls back only
that event, increments `failed_events`, and processing continues. A projection error never propagates to
a business transaction (projections run in their own connections and are dark-launched).

## Routes

`GET /projections` (dashboard) · `/health` · `/{id}` (`observability.view`); `/diagnostics` ·
`/governance` (`observability.audit`); `POST /rebuild` · `/reset` · `/replay` · `/governance/validate`
(`observability.execute`). All reuse the D.26 `observability.*` capabilities.
