# Projection Governance (Phase D.36)

`app/services/projections/governance.py::validate()` is a **read-only** validator of the read-model
registry. It reads the projection registries, the in-code definitions, the domain-event contract
registry, and the projection source read-only, and returns `{ok, issue_count, findings, coverage}`. It
never raises and never edits. It ensures projections honour the read-model invariants — most importantly
that a projection never becomes a shadow source of truth.

## Finding types

| Finding | Meaning |
|---|---|
| `projection_without_owner` | An active projection with no owner. |
| `projection_without_subscriber` | An active projection that subscribes to no events (nothing to build from). |
| `subscriber_without_projection` | A subscribed event type with no registered domain-event contract (a dangling subscription). |
| `projection_schema_drift` | The registry `schema_version` differs from the in-code definition. |
| `projection_version_drift` | The built read-model schema version (state) differs from the current definition — a rebuild is needed. |
| `projection_lag` | An established (rebuilt) projection has fallen behind the outbox beyond the lag threshold. |
| `projection_replay_mismatch` | A recorded validation found the projection non-deterministic (rebuild twice → different content). |
| `projection_dependency_cycle` | The `depends_on` graph contains a cycle. |
| `duplicate_projection` | Two projections share an id or a read table. |
| `projection_reading_authoritative_tables` | A projection handler touches a table that is not its read-model table or the outbox — it would become a shadow read of authoritative data. |
| `governance_check_error` | The validator caught an unexpected error (never raised). |

## The authoritative-read check

The most important invariant: a projection may build **only** from events + its own read-model table. A
static scan of `definitions.py` extracts every `_tbl("…")` reference and flags any that is not a `rm_*`
read table or `outbox_events`/`projection_*`. This prevents a projection from re-querying authoritative
tables (which would couple read models to the write side and risk drift). Existing read surfaces are not
switched onto projections in this phase; once a surface adopts a projection, it must stop querying the
authoritative table for that data — this check guards the projection side.

## Coverage

`registry.coverage()` reports projection count, health breakdown (healthy/lagging/failed/unbuilt),
category coverage, and **event coverage** (domain-event contracts consumed by a projection ÷ total — the
Activity Feed consumes every event, so **100%**).

## Recording

`governance.record_validation()` runs validation and records a `projection.governance_validated` event
to the shared audit hash-chain.

## Routes

`GET /projections/governance` (`observability.audit`) — the report.
`POST /projections/governance/validate` (`observability.execute`) — run + record.

## Current state

0 governance issues · 12 projections · 100% event coverage · no projection reads an authoritative table.
