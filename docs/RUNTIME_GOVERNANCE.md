# Runtime Configuration Governance & Authority (Phase D.31)

The D.28 runtime engine is the **authoritative** source for the migrated application behaviors. This
document describes runtime authority, the governance model that validates the runtime metadata, and
the compatibility policy. The runtime engine remains the sole evaluator; D.29 coordination remains the
sole synchronization mechanism; D.27 remains the sole metadata owner.

## Runtime authority

A behavior is **authoritative** when a D.27 runtime definition exists that drives it (seeded so its
value equals the legacy default — behavior-preserving). Behavior changes now occur **only through
runtime metadata**. Registry columns (`runtime_behaviors`): `authoritative` (bool), `compatibility_shim`
(bool), `runtime_default` (JSON).

- **Adoption** = (migrated + retired) ÷ migratable — every migratable behavior consumes the engine.
- **Runtime authority** = authoritative ÷ migratable — the engine is the authoritative source.
- **Definition coverage** = authoritative behaviors whose complete runtime definition is present and
  enabled ÷ authoritative.

Current: adoption 100%, authority 71.4% (5/7), definition coverage 100%, 0 governance issues.

## Governance model — `app/services/runtime/governance.py::validate()`

Read-only validation of the runtime metadata + the behavior registry (never edits, never raises). The
report (`GET /runtime/behavior/governance`, `runtime.audit`) contains `{ok, issue_count, findings,
coverage}` with these finding types:

| Finding | Meaning |
|---|---|
| `missing_definition` | An authoritative behavior's expected flag/config item is absent. |
| `missing_definition_spec` | An authoritative behavior has no declared definition spec (a code bug). |
| `authoritative_definition_disabled` | An authoritative feature flag evaluates **disabled** (would silently change behavior). |
| `deprecated_definition_reference` | An authoritative behavior references a `deprecated`/`archived` flag. |
| `unused_definition` | An active runtime flag not referenced by any authoritative behavior and not a legitimate per-instance prefix (`automation.job.`, `reporting.module.`). |
| `orphan_capability` | An `edition_capabilities.capability_code` not present in the RBAC `capabilities` catalog. |
| `invalid_edition_mapping` | An active edition assignment referencing a missing/`retired` edition. |

`POST /runtime/behavior/governance/validate` (`runtime.admin`) runs validation and records a firm-level
`governance_validation_completed` event to the `runtime_events` ledger (entity_type `behavior`/
`governance`). Routine feature evaluations are never recorded.

## Compatibility policy

- **Retired behaviors** keep the consumption `default=` as a **documented compatibility shim**
  (`shim=True`): a safety net served only if the runtime definition is absent (e.g. after a migration
  downgrade), counted separately as `compatibility_fallbacks` so it is observable. In normal operation
  the engine is authoritative and the shim is not taken.
- **Permanent compatibility shims** (`automation.job.<type>`, `reporting.module.<id>`) — the key spaces
  are unbounded (extensible dispatch registry + `custom`; user-created report rows), so a full
  definition set cannot be seeded. These stay `migrated` with `default=True` **by approved policy**
  (ADR-036 exception), not as a temporary gap.

## Infrastructure exclusions (never in the runtime domain)

DB connectivity, session/crypto secret keys, auth/OIDC providers, Microsoft credentials + provider
initialization / OAuth / connector configuration, logging init, `ENVIRONMENT`, and the
scheduler-registration gates + interval/TTL helpers remain boot-time infrastructure. See
`docs/RUNTIME_BEHAVIOR_MIGRATION.md` for the full exclusion list.
