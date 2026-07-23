# ADR-036 — Runtime Authority: the engine is the authoritative source for migrated behavior; legacy fallbacks retired to documented compatibility shims; runtime metadata governed

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Runtime/Configuration); Reliability / Operations (governance);
Security / Authorization (RBAC ownership); Business Operations Owner (Michael Shelton — behavioral
configuration requirements). Authorized compliance reviewer: Not yet designated.

## Context
Phase D.30 (ADR-035) migrated application behavior to consume the D.28 runtime engine through a
standardized, behavior-preserving consumption API, but the migrated behaviors still had **no runtime
metadata** in D.27 — they ran on their consumption-side legacy defaults, so the engine was not yet the
authoritative source and behavior could not be changed through runtime metadata. There was also no
validation that runtime metadata was internally consistent (no orphan/missing/deprecated definitions,
no invalid edition mappings).

The risks of making the engine authoritative are: changing behavior while "activating"; removing a
legacy fallback for a behavior whose key space cannot be fully enumerated (breaking future instances);
or letting inconsistent runtime metadata silently change behavior.

## Decision
Phase D.31 makes the runtime engine the **authoritative** source for the migrated behaviors, retires
the fixed legacy fallbacks to documented compatibility shims, migrates the last legacy candidate
(advisor-workspace section gating), and adds **runtime governance** validation. **The runtime engine
remains the sole evaluator; D.29 coordination remains the sole synchronization mechanism; D.27 remains
the sole metadata owner.**

**Runtime default activation (behavior-preserving).** The migration seeds D.27 runtime metadata whose
values equal the legacy defaults, so behavior is identical while the engine becomes authoritative:
- Feature definitions (active + enabled + 100% rollout → evaluate enabled): `analytics.executive_metrics`,
  `microsoft365.sync`, and the three advisor-workspace section flags (`advisor_workspace.section.{work,
  tasks,exceptions}`).
- Configuration items (a seeded `runtime-defaults` set) equal to the current `app.config` defaults:
  the five `benefits.<window>_days` items and `microsoft365.sharepoint_site_ids`.

**Legacy retirement (to documented compatibility shims).** Behavior changes now occur **only through
runtime metadata**. The **fixed/enumerable** behaviors are marked `retired` and `authoritative` — the
engine drives them. Their consumption call sites keep the legacy `default=` as a **documented
compatibility shim** (`shim=True`): a safety net that serves only if the runtime definition is absent
(e.g. after a migration downgrade), and whose use is counted separately (`compatibility_fallbacks`) so
"a retired behavior serving its legacy default" is observable rather than silent. In normal operation
(definition present), a retired behavior never depends on the legacy fallback.

**Compatibility shims that must remain (per-instance, unbounded key spaces).** `automation.job.<type>`
(the dispatch registry is extensible and has an open-ended `custom` handler; `execute_dispatch` accepts
any job-type string) and `reporting.module.<id>` (report definitions are user-created rows) cannot be
fully pre-seeded, so they stay `migrated` compatibility shims — the `default=True` fallback is a
permanent, documented policy, not a temporary gap.

**Advisor-workspace section gating migrated.** The three capability-gated sections consult
`RuntimeContext.feature_enabled("advisor_workspace.section.<name>")` **alongside** the existing
`principal.can()` check (never replacing it — ADR-004). Behavior-preserving: the seeded section flags
are enabled, so behavior is unchanged; a section shows only when the principal holds the capability AND
the runtime section feature is enabled.

**Runtime governance.** `app/services/runtime/governance.py` validates the runtime metadata read-only
and returns a structured report: every authoritative behavior has its **complete definition present
and evaluating enabled** (definition coverage), no **missing/deprecated** definitions, no **orphan/
unused** definitions (excluding the legitimate per-instance prefixes), no **invalid edition mappings /
orphan capabilities** (edition capability codes validated against the RBAC `capabilities` catalog). It
never raises and never edits metadata.

**Adoption/authority/observability.** `/runtime/behavior` gains a governance report + validate action
and an authority/coverage dashboard. Analytics gains authority metrics (retired count, authority %,
definition coverage %, governance issue count, compatibility-shim count, compatibility fallbacks).
Major lifecycle events (`runtime_defaults_activated` at seed time, `legacy_behavior_retired`,
`governance_validation_completed`, `runtime_behavior_adopted`) record to the D.28 `runtime_events`
ledger; routine evaluations are never recorded.

**Security.** Reuses the D.28 `runtime.*` capabilities (**no RBAC change**). Governance actions require
`runtime.admin`; the report requires `runtime.audit`. Runtime consumption never bypasses RBAC (the
advisor-workspace and analytics gates keep their capability checks).

## Alternatives considered
1. **Remove the legacy `default=` entirely on retirement.** Rejected: an authoritative behavior whose
   definition is momentarily absent (a downgrade, a mis-seed) would flip to `False`/`None` and silently
   change behavior. A documented, observable compatibility shim is safer than a hard dependency on the
   definition always being present.
2. **Seed a flag per automation job type / report definition to retire those too.** Rejected: the key
   spaces are unbounded (extensible registry + `custom`; user-created report rows). Any new instance
   would default off, breaking the behavior-preserving guarantee. They remain compatibility shims by
   necessity.
3. **Change behavior during activation (make runtime values differ from the legacy defaults).**
   Rejected: activation must be behavior-preserving. Behavior changes only through a subsequent,
   deliberate runtime-metadata edit.
4. **Persist governance findings in a table.** Rejected for now: governance is computed on-demand
   (always fresh, cheap); only the *completion* of a validation is recorded to the ledger.

## Reasons for the decision
The engine must be the authoritative source for migrated behavior, changes must flow only through
runtime metadata, no retired behavior may silently depend on a legacy fallback, and runtime metadata
must be validated — all without changing current behavior or bypassing RBAC. Seed-to-parity +
retire-to-shim + governance delivers this while preserving ADR-004/005/009/032/033/034/035.

## Consequences
### Positive consequences
- The runtime engine is authoritative for 5 of 7 migratable behaviors (71.4%); adoption is 100% (all
  migratable behaviors consume the engine); governance passes with 100% definition coverage. Behavior
  is unchanged. Analytics/observability expose authority + governance; the timeline records only major
  lifecycle events.

### Negative consequences and tradeoffs
- Two **permanent compatibility shims** remain (`automation.job.<type>`, `reporting.module.<id>`) — by
  necessity (unbounded key spaces), documented, not a temporary gap.
- The retired behaviors keep a legacy `default=` as a compatibility shim (observable via
  `compatibility_fallbacks`); a downgrade removes the seeded metadata and the shims transparently serve
  the legacy defaults (behavior unchanged, but the engine is no longer authoritative until re-seeded).
- Runtime metadata now drives behavior — a mis-edit (e.g. disabling `microsoft365.sync`) would change
  behavior; governance validation surfaces such issues (e.g. `authoritative_definition_disabled`).

## Enforcement
- `app/services/runtime/governance.py` (validator + `record_validation`); `behavior.py` coverage adds
  `authoritative`/`authority_pct`/`compatibility_shims`; `consumption.py` adds the
  `compatibility_fallbacks` counter + `shim=` flag; `context.py`/`engine.py` unchanged evaluator.
  Table columns via migration `z8a9b0c1d2e3` (`runtime_behaviors.authoritative/compatibility_shim/
  runtime_default`) + **seeded D.27 metadata** (feature flags + `runtime-defaults` config set/items) +
  registry retirement/migration updates. Migrated call sites marked `shim=True`. Advisor-workspace gate
  in `advisor_workspace.py::get_daily_dashboard` (runtime consulted alongside the capability). Routes
  `app/routes/runtime_behavior.py` (`/runtime/behavior/governance` + validate). Analytics metrics. The
  runtime engine, D.29 coordination, RBAC, the certified-frozen notification module, infrastructure
  config, and the D.5 golden are untouched. Governance module registered in `source_producer_modules`.
  Tests: `tests/test_runtime_authority.py`; manifest / platform-architecture / route-count guards
  updated.

## Exceptions
The two per-instance compatibility shims (`automation.job.<type>`, `reporting.module.<id>`) are an
approved, documented permanent policy. `administrator`/`record.read_all` scope bypass remains as
defined by ADR-004.

## Revisit conditions
Removing a compatibility shim (only if a behavior's key space becomes bounded), persisting governance
findings, or wiring the effective runtime configuration back into the boot-time config loaders would
each warrant a new or superseding ADR.

## References
- `app/services/runtime/{governance,behavior,consumption,context}.py`,
  `app/services/advisor_workspace.py`, `app/routes/runtime_behavior.py`,
  `app/database/runtime_behavior_tables.py`, migration
  `migrations/versions/z8a9b0c1d2e3_runtime_authority.py`, `docs/RUNTIME_BEHAVIOR_MIGRATION.md`,
  `docs/RUNTIME_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_runtime_authority.py`; relates to ADR-004, ADR-005, ADR-009, ADR-032, ADR-033, ADR-034,
  ADR-035
