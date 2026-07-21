# ADR-017 — Architecture manifest and enforcement tests

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture.

## Context
Architecture documentation drifts from code over time: route counts change, migrations advance,
capabilities are added, imports creep upward. Prose alone cannot prevent this. Phase D.12A added a
machine-readable manifest and tests so the authoritative architecture reference stays true.

## Decision
The platform **must** keep a machine-readable architecture manifest and enforcement tests
alongside the authoritative document:
- `docs/PLATFORM_ARCHITECTURE.md` is **authoritative for explanation** (what/why in prose).
- `docs/platform_architecture_manifest.yaml` provides **testable architecture metadata** (route
  count, migration head, capabilities, composition modules, import-direction targets, declared-
  schema registrations, not-currently-modeled data). It **is not runtime configuration** — nothing
  in `app/` imports it.
- `tests/test_platform_architecture.py` (and `tests/test_architecture_decision_records.py`)
  **verify hard facts** against live code: route count, single migration head, capability
  existence, module existence, import direction, single timeline table, schema registration, and
  required document/ADR structure.
- Tests **must** assert **explicit metadata/structural markers**, not full-document string
  snapshots, so harmless wording changes do not break them.
- Future phases **must** update the manifest (and tests) whenever a hard fact changes.

## Alternatives considered
1. **Documentation only, no tests.** Rejected: guarantees eventual drift; D.12A exists precisely to
   prevent that.
2. **Snapshot the whole document in a test.** Rejected: brittle — every wording change breaks CI;
   metadata/structure assertions are durable instead.

## Reasons for the decision
A small manifest plus fact-checking tests turns architecture invariants into CI-enforced guarantees
without coupling to prose, keeping the reference trustworthy as the code evolves.

## Consequences
### Positive consequences
- The architecture reference cannot silently drift; violations fail CI.
- Contributors get immediate feedback when a hard fact changes.

### Negative consequences and tradeoffs
- The manifest must be kept in sync with code (that is the point) — a small maintenance cost.
- Over-specific tests could become brittle, so they deliberately target metadata/structure only.

## Enforcement
- `docs/platform_architecture_manifest.yaml`; `tests/test_platform_architecture.py` (12 checks);
  `tests/test_architecture_decision_records.py` (ADR structure/index/sequence).
- The manifest is not imported by `app/` (architecture metadata, not config).

## Exceptions
None currently approved.

## Revisit conditions
If the manifest grows toward runtime behavior, split metadata from any runtime concern; if a test
proves brittle on wording, relax it to a structural marker (never delete the guarantee).

## References
- `docs/PLATFORM_ARCHITECTURE.md` (whole), `docs/platform_architecture_manifest.yaml`
- `tests/test_platform_architecture.py`, `tests/test_architecture_decision_records.py`
- `docs/PLATFORM_ARCHITECTURE.md` §22 (Testing and architectural enforcement)
