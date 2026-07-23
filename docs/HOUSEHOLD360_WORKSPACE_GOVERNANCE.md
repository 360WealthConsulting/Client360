# Household 360 Workspace Governance (Phase D.41)

Read-only validation that the Household 360 Workspace stays a **composition surface** and never becomes a
second household engine or a source of truth. `client360.governance.validate_household360(principal=None)`
returns `{ok, issue_count, findings}` and **never raises**. Surfaced (with diagnostics) at
`GET /client/household/{id}/diagnostics` (`observability.audit`).

See [`HOUSEHOLD360_WORKSPACE.md`](HOUSEHOLD360_WORKSPACE.md), [`ADR-046`](adr/ADR-046-household360-workspace.md).

## Checks

| Finding type | What it detects |
|---|---|
| `missing_adapter` | a registered household section with no builder |
| `direct_projection_table_read` | an `rm_*` table referenced directly in `household.py` |
| `direct_mutation` | `.insert()/.update()/.delete()` in the household composition |
| `outbox_or_audit_write_in_composition` | `publish_safe` / `write_audit_event` in the composition |
| `shadow_household_or_person_table` | a `Table(...)` defined in the household composition (a shadow record) |
| `duplicate_portfolio_aggregation` | `aggregate_portfolio` re-called (must reuse `get_household_portfolio`) |
| `household_portfolio_not_reused` | the household total not sourced from `get_household_portfolio` |
| `net_worth_not_marked_untracked` | net worth not surfaced as a "not tracked" marker (i.e. computed) |
| `tax_inference_guard_missing` | the tax section missing its no-inferred-filing/dependency marker |
| `record_scope_not_enforced` | the household boundary `record_in_scope` check absent |
| `work_not_reusing_unified_queue` | household work not routed through D.39 `compose_queue` |
| `duplicate_projection` | the read-model set changed (must stay the D.36 twelve; no `rm_household360`) |
| `no_quick_actions_composed` / `quick_action_without_deep_link` | runtime sample: a quick action missing its deep link |

## Why these checks

They encode the ADR-046 invariants as executable guards:

- **no direct DB mutation / no authoritative writes / no outbox / no audit** → `direct_mutation`,
  `outbox_or_audit_write_in_composition`;
- **no shadow household model / no duplicate person model** → `shadow_household_or_person_table`;
- **no direct `rm_*` access / no duplicate projection** → `direct_projection_table_read`,
  `duplicate_projection`;
- **no duplicate portfolio aggregation** → `duplicate_portfolio_aggregation`,
  `household_portfolio_not_reused`;
- **no fabricated net worth / no inferred filing/dependency** → `net_worth_not_marked_untracked`,
  `tax_inference_guard_missing`;
- **every section capability-gated / every quick action deep-links / record scope present / D.39 reuse**
  → `missing_adapter`, `quick_action_without_deep_link`, `record_scope_not_enforced`,
  `work_not_reusing_unified_queue`.

The checks combine **static source scans** of `household.py` with **config checks** (every section has a
builder; the read-model set is unchanged) and an optional **runtime sample** when a principal is supplied
(quick actions compose with deep links). Governance returns a structured report and never raises into the
workspace.

## Diagnostics (companion)

`client360.diagnostics.household_diagnostics(principal, household_id=)` reports total + per-section
composition time, member count, scoped member count, suppressed members, sections built/suppressed,
failed adapters, stale sources, missing adapters, timeline dedup count, graph node/edge counts, cycle
protection, record-scope validation, projection/fallback usage (per-member reads are authoritative
composition), and quick-action availability. Read-only.
