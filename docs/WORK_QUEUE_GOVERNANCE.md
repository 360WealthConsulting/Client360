# Work Queue Governance (Phase D.39)

Read-only validation that the Unified Work Queue stays a **composition surface** and never becomes a
shadow engine or a source of truth. `work_queue.governance.validate_work_queue(principal=None)` returns
`{ok, issue_count, findings}` and **never raises**. Surfaced (with diagnostics) at
`GET /work/diagnostics` (`observability.audit`).

See [`UNIFIED_WORK_QUEUE.md`](UNIFIED_WORK_QUEUE.md), [`ADR-044`](adr/ADR-044-unified-work-queue.md).

## Checks

| Finding type | What it detects |
|---|---|
| `adapter_without_source` | a declared source domain with no adapter emitting it |
| `adapter_with_no_queue_usage` | a registered adapter producing no known source domain |
| `source_without_capability_mapping` | a source domain with no capability in `DOMAIN_CAPABILITY` |
| `source_without_dispatch_adapter` | a source domain with no dispatch adapter |
| `source_without_action_map` | a source domain missing from `dispatch.ALLOWED_ACTIONS` |
| `duplicate_source_adapter` | two adapters for the same domain |
| `action_without_capability` | a dispatch-able action with no capability floor |
| `dispatch_missing_delegate` | the dispatch layer not referencing a required authoritative service |
| `direct_authoritative_mutation` | `.insert()/.update()/.delete()` in a read-only queue module |
| `direct_projection_table_read` | an `rm_*` table referenced directly in a queue module |
| `unknown_filter_key` | a built-in view using a filter key the queue does not understand |
| `saved_view_unknown_filter` | a stored saved view carrying an unknown filter key |
| `stale_projection` | an adopted projection the queue counts rely on lagging beyond threshold |
| `dead_end_work_item` | a composed item with no deep link (runtime sample, if a principal is given) |
| `work_item_without_source_reference` | a composed item with no stable `work_item_key` |

The checks combine **static source scans** (queue modules must not mutate authoritative state or read
`rm_*` tables, dispatch must delegate to the authoritative services, actions must be capability-mapped)
with **config checks** (every source is adapted, capability-mapped, and dispatch-known; filter keys are
known) and an optional **runtime sample** (deep links + stable keys) when a principal is supplied.

## Why these checks

They encode the ADR-044 invariants as executable guards:

- **the queue is not a second engine** → `direct_authoritative_mutation`, `dispatch_missing_delegate`,
  `source_without_action_map`;
- **authoritative services own mutation** → dispatch delegates; the queue never mutates;
- **projections stay disposable, read via adoption** → `direct_projection_table_read`, `stale_projection`;
- **no dead ends, stable references** → `dead_end_work_item`, `work_item_without_source_reference`;
- **saved views are presentation state** → `unknown_filter_key`, `saved_view_unknown_filter`.

## Diagnostics (companion)

`work_queue.diagnostics.work_queue_diagnostics(principal)` reports: total visible + candidate totals,
counts by domain/status/SLA, overdue / SLA-breach / unassigned counts, per-adapter latency + errors,
items suppressed by capability, projection/fallback usage (from the D.37 adoption layer), saved-view
usage, action success/failure + bulk partial-failure counts, and page query latency. Read-only.
