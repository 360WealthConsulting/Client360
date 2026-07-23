# Client 360 Workspace Governance (Phase D.40)

Read-only validation that the Client 360 Workspace stays a **composition surface** and never becomes a
second client engine or a source of truth. `client360.governance.validate_client360(principal=None)`
returns `{ok, issue_count, findings}` and **never raises**. Surfaced (with diagnostics) at
`GET /client/{id}/diagnostics` (`observability.audit`).

See [`CLIENT360_WORKSPACE.md`](CLIENT360_WORKSPACE.md), [`ADR-045`](adr/ADR-045-client360-workspace.md).

## Checks

| Finding type | What it detects |
|---|---|
| `missing_adapter` | a registered section with no builder |
| `quick_action_without_capability` | a quick action not capability-gated |
| `direct_projection_table_read` | an `rm_*` table referenced directly in a composition module |
| `direct_mutation` | `.insert()/.update()/.delete()` in a composition module |
| `outbox_or_audit_write_in_composition` | `publish_safe` / `write_audit_event` in a composition module |
| `shadow_client_record_table` | a `Table(...)` defined in a composition module (a shadow record) |
| `no_authoritative_service_delegation` | composition not importing from `app.services.*` |
| `record_scope_not_enforced` | `service.py` missing the boundary `record_in_scope` check |
| `duplicate_projection` | the read-model set changed (must stay the D.36 twelve; no `rm_client360`) |
| `no_quick_actions_composed` | runtime sample: a workspace composed with no quick actions |

## Why these checks

They encode the ADR-045 invariants as executable guards:

- **no second client engine / no shadow record** → `shadow_client_record_table`, `duplicate_projection`;
- **no duplicated business logic / authoritative services only** → `no_authoritative_service_delegation`,
  `direct_mutation`;
- **no direct mutation / outbox unchanged** → `direct_mutation`, `outbox_or_audit_write_in_composition`;
- **no duplicate projections** → `direct_projection_table_read`, `duplicate_projection`;
- **RBAC + record scope preserved** → `record_scope_not_enforced`, `quick_action_without_capability`
  (plus the per-section capability gating enforced in the registry).

The checks combine **static source scans** of the composition modules (`sections.py`, `service.py`,
`snapshot.py`, `diagnostics.py`) with **config checks** (every section has a builder; every quick action
is capability-gated; the read-model set is unchanged) and an optional **runtime sample** when a
principal is supplied.

## Diagnostics (companion)

`client360.diagnostics.client360_diagnostics(principal, person_id=/household_id=)` reports composition
timings (per section + total), sections built, suppressed capabilities, missing adapters, stale
(errored) sources, record-scope validation, and projection/fallback usage — noting that per-client
sections read authoritative tables directly (no projection-backed fast path on the per-client route).
Read-only.
