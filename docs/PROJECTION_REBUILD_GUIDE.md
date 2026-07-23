# Projection Rebuild Guide (Phase D.36)

Read models are **disposable** — the safe operation for almost any projection problem is: reset +
rebuild (or just rebuild) from the events. The outbox is authoritative; rebuilding cannot lose data.

## Operations

| Goal | Do |
|---|---|
| Build a projection for the first time | `POST /projections/rebuild {projection_id}` (or `engine.rebuild(id)`) |
| Recover a corrupt / drifted read model | `POST /projections/reset` then `POST /projections/rebuild` (or `engine.reset(id)` + `engine.rebuild(id)`) |
| Apply a schema change | Bump the definition `schema_version`, migrate the `rm_*` table, then rebuild (rebuild re-populates from events) |
| Catch up after downtime | `engine.process(id)` (incremental) — or let the tick do it — or rebuild |
| Prove determinism | `engine.validate(id)` (rebuilds twice, compares) |
| Inspect | `GET /projections/{id}` (health, lag, size, rebuild history) |

## Enabling the background tick

The incremental tick is dark-launched (`PROJECTIONS_ENABLED`, default off). Enable it to apply new
outbox events to the read models on a cadence (`PROJECTIONS_TICK_INTERVAL_SECONDS`). Until enabled (or an
on-demand rebuild), read models are `unbuilt` — nothing depends on them, so this is safe.

## Determinism guarantees

- Applying the ordered outbox events is a pure function; apply handlers are idempotent upserts keyed on
  the natural id. Replaying the same events yields the same rows.
- `validate` rebuilds twice and compares a content signature (excluding the surrogate `id` and volatile
  `updated_at`). A mismatch is a governance finding (`projection_replay_mismatch`).

## Adding a new read model

1. Add the `rm_*` table to `app/database/projection_tables.py` (+ the migration).
2. Add the definition to `app/database/projection_seed.py` and an `apply(conn, event)` handler in
   `app/services/projections/definitions.py` — the handler may touch **only** the read table + the event
   payload (references only), never an authoritative table.
3. Seed the definition + an initial `projection_state` row in the migration (single Alembic head).
4. Run `governance.validate()` (must be `ok: true`) and `engine.validate(id)` (must be deterministic).
   Add tests: rebuild, replay determinism, incremental, reset, governance.

## Safety invariants

Rebuilding/resetting a read model never touches an authoritative table or ledger. Projection failures
are isolated and never affect a business transaction. The outbox remains the sole event bus + log; read
models are always reconstructable from it.
