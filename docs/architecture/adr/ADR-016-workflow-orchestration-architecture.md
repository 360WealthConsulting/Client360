# ADR-016 — Workflow Orchestration Architecture

- **Status:** Accepted
- **Date:** 2026-07-18
- **Relates to:** [ADR-013](ADR-013-repository-reconciliation.md) (in-place reconciliation),
  [ADR-015](ADR-015-tamper-evident-audit-architecture.md) (tamper-evident audit),
  the approved Epic 4 Plan (E4.0), and the approved Workflow Architecture Decision Review (ADR-016.A).
- **Governs:** all Epic 4 (F4.x) implementation.

## Context
The repository already contains a compact, RC-validated workflow **execution engine**
(`app/services/workflow_automation.py` + `app/routes/workflows.py`, ≈260 LOC) backed by
nine `workflow_*`/`automation_*` tables and `work_approvals`, hardened by five DB trigger
functions (`prevent_workflow_event_mutation`, `protect_published_workflow_template`,
`protect_published_workflow_definition`, `validate_workflow_dependency` incl. recursive-CTE
cycle detection, `protect_published_workflow_dependency_delete`), an SoD check constraint
(`ck_work_approval_segregation`), and an 11-test acceptance suite. Cross-domain callers
(`tax_domain.py`, `tax_intake.py`, `portal/service.py`, `demo/seed.py`) depend on
`launch_workflow`/`complete_step`.

Epics 1–3 delivered a platform substrate built to be consumed by this engine but not yet
wired to it: F1.3 transactional outbox (ships OFF, designed to "complement… not replace"
the workflow tables), F1.4 event envelope, F1.5 workflow template registry (a parallel SOP
catalog), F2.x security, F3.x audit/evidence. Today the engine does **not** consume F1.5,
does **not** emit F1.4 over F1.3, audits **only** launch/transition, and links **no** F3.3
evidence. Seven of the nine tables and the key lifecycle columns exist only in migration
`e530f5b3d4e5` and are exposed at runtime via `metadata.reflect()`.

## Problem statement
Epic 4 must deliver a platform-level workflow orchestration foundation — instances driven
by F1.5 templates, transitions emitting F1.4 events over F1.3, governed by F2.x and recorded
by F3.x — **without** rebuilding validated execution logic, breaking cross-domain callers, or
violating ADR-013 ("reconcile in place; never a broad rewrite"). The gap is *integration*,
not *correctness*.

## Architectural drivers
ADR-013 (additive, in-place, preserve behavior); the platform objective (actually consume
F1.3/F1.4/F1.5); safety (preserve five DB invariants + SoD + cycle detection + 11 tests);
backward compatibility (public routes + cross-domain signatures); ADR-015/compliance (every
transition auditable and evidence-linkable; reference-only payloads); operational conservatism
(outbox ships OFF; new behavior must be opt-in and reversible).

## Decision
Adopt **Option B — a bounded hybrid**: preserve the existing execution engine and its DB
invariants unchanged, and add a **thin platform adapter layer** that (a) binds instance launch
to a published F1.5 registry `template_id@version`, (b) emits F1.4 `Envelope`s via F1.3
`publish_event` on transitions and adds idempotent, opt-in outbox subscribers for event-driven
advancement/automation, and (c) completes F3.1 audit coverage and adds F3.3 evidence linkage.
The engine calls the platform; the platform never reaches into engine internals. Capabilities
reuse the existing `work.*` family. All migrations are additive/reversible; runtime reflection
is preserved.

## Canonical components (remain authoritative, unchanged)
The engine core (lifecycle state machine, dependency-advance, parallel/conditional resolution,
approvals/SoD, SLA, automation dispatch); all nine tables + `work_approvals` and their five
trigger functions + SoD check + cycle detection; `workflow_events` as the domain event ledger;
the DB `workflow_templates`/steps/dependencies as the execution-snapshot source; the
`work.read`/`work.write`/`work.approve` capabilities; the 19 routes + two Jinja templates; the
11-test suite.

## Wrapped components (thin additive adapter; internals untouched)
Template resolution → bind to a published F1.5 `template_id@version` (registry becomes
discovery/versioning authority; DB template remains the snapshot source). Event emission →
transitions additionally `publish_event(Envelope)` in the engine's existing transaction;
idempotent, opt-in subscribers provide event-driven advancement/automation. Audit/Evidence →
complete `write_audit_event` on step-completion/approvals/SLA; link F3.3 evidence via the
produced `audit_event_id`.

## Deprecated components (soft; additive — nothing removed in Epic 4)
Timeline-only signalling as the *sole* propagation path is superseded (not removed) by outbox
event emission; the orphaned state of the F1.5 registry is deprecated (the registry is
activated). No table, function, trigger, route, or capability is dropped.

## Compatibility contract (NORMATIVE for all Epic 4 work)
- **Stable public APIs:** the 19 existing `/workflows` and `/api/v1/workflows/...` routes
  (paths, methods, status codes, `work.*` gating); service entry points `launch_workflow`,
  `complete_step`, `transition_workflow`, `request_approval`, `decide_approval`, `process_event`
  — signatures and observable behavior preserved (new parameters optional/defaulted).
- **Stable execution semantics:** the lifecycle state machine; dependency-gated activation;
  parallel/conditional handling; automatic instance completion; SLA due-date computation.
- **Stable DB guarantees:** `workflow_events` append-only; published templates/steps immutable;
  cycle and cross-version dependency rejection; independent-approval SoD; unique idempotency
  keys; single Alembic head; additive/reversible migrations only.
- **Stable automation guarantees:** idempotent event→workflow dispatch (dedupe by
  `idempotency_key`); action-ledger idempotency + stale-action rejection; bounded action types
  fail closed.
- **Backward-compat expectations:** additive-only schema; reflection preserved (reflected tables
  not promoted to Core metadata); no role widened; no new `record.read_all`; cross-domain callers
  unaffected; synchronous advancement remains the default.
- **Permitted to evolve:** additional F1.4 event emission on transitions; new opt-in outbox
  subscribers; additive binding columns; evidence linkage; additive routes/fields; a new
  capability solely for template publishing (if adopted). Event-driven advancement may become the
  default only via a superseding decision.

## Event model
Transitions emit an F1.4 `Envelope` (via `new_event` + `publish_event`) alongside the preserved
`workflow_events` ledger, inside the engine's existing transaction (outbox atomicity). Canonical
types: `workflow.launched|paused|resumed|cancelled|completed|reopened`,
`workflow.step.activated|completed|skipped`, `workflow.approval.requested|decided`,
`workflow.sla.escalated`. `subject_ref="workflow_instance:<id>"` (reference only);
`correlation_id` ties an instance flow; `causation_id` links the triggering event; payloads carry
references only. Idempotency uses the envelope `event_id`; subscribers are idempotent and keyed in
`outbox_processed_events`. Delivery inherits F1.3 semantics. Event-driven advancement is opt-in
behind the dispatcher flag; with it off, emission is inert and synchronous advancement is unchanged.

## Registry integration (F1.5)
Instances bind to a published registry `template_id@version` via an additive nullable link on
`workflow_instances`; the DB `workflow_templates` row remains the execution snapshot. Epic 4
consumes published templates only (SOP publishing stays a separate, gated process). The
registry↔DB-template identity mapping uses an explicit `platform_template_ref` link. The registry
is not made runtime-persistent in Epic 4.

## Security integration (Epic 2)
Reuse `work.read`/`work.write`/`work.approve`. Object-level authorization reuses the existing
record-scope path (extended additively). Field-level redaction (F2.4) applies to surfaced metadata.
No role widened; no new `record.read_all`. At most one new capability (`work.publish`) if template
publishing is later brought in-scope. Default-deny.

## Audit & evidence integration (Epic 3)
Extend `write_audit_event` to the currently-unaudited paths (`complete_step`, approvals,
`evaluate_sla`) with reference-only metadata and `workflow.*` action naming; every state-changing
operation is hash-chained (ADR-015). Link F3.3 evidence to significant events (approvals,
completions) via the produced `audit_event_id`, preferring no schema change. Auditor visibility via
F3.4 export is inherited.

## Migration strategy
Additive only; single head; reversible; idempotent DDL (F3.2 lessons); pristine-DB full down/up
validation from `f3d4e5v6i7d8`. Only additive link columns anticipated (instance↔registry binding);
events reuse `outbox_events.payload`; evidence links through `audit_event_id`; audit completion is
code-only. Reflected tables are not promoted to Core metadata. No data backfill.

## Operational considerations
The outbox dispatcher remains OFF by default (`OUTBOX_DISPATCHER_ENABLED=false`); event-driven
advancement/automation is dark-launched and enabled deliberately. Synchronous advancement stays the
default. The existing APScheduler drives SLA evaluation and (when enabled) outbox dispatch; no new
background mechanism is added. Dead-letters remain operator-visible. Structured logs on
emission/dispatch, no PII.

## Alternatives considered
- **Option A — extend the engine, no platform abstraction.** Rejected: leaves F1.3/F1.4/F1.5
  orphaned, re-entrenches domain coupling, grows parallel-model debt; fails the platform objective.
  (Decision-matrix 139/200.)
- **Option C — replace the engine on the platform.** Rejected: discards RC-validated logic, five DB
  invariants, and eleven tests; breaks tax/portal callers; requires in-flight-instance migration and
  trigger re-authoring; worst backward compatibility; violates ADR-013. (93/200.)
- **Option B — wrap + preserve (chosen).** Preserves invariants/tests/callers, delivers the platform
  objective additively, reduces debt; consistent with the ADR-013 method used for F2.2/F1.3/F3.1.
  (187/200.)

## Consequences
**Positive:** platform substrate activated; the F1.5 registry reconciled; audit/evidence gaps closed;
a single coherent workflow model; validated correctness and cross-domain callers preserved; smaller,
lower-risk Epic 4; reversible, dark-launched rollout. **Neutral/limits:** two event surfaces coexist
by design (domain `workflow_events` + platform envelope stream); the schema-bifurcation/reflection
condition persists intentionally; event-driven advancement is opt-in until a future decision.

## Risks
Facade leakage (mitigation: one-way dependency, engine module intact); declared-metadata trap
(mitigation: keep reflection, no promotion); capability duplication (mitigation: reuse `work.*`);
outbox-OFF operational gap (mitigation: opt-in dark launch, synchronous default); registry↔template
identity mapping (explicit `platform_template_ref`).

## Future work
External head-hash anchoring of the platform event stream; promoting selected envelope fields to
indexed columns for correlation tracing; a machine-readable per-event-type schema registry; making
event-driven advancement the default; runtime-editable/persistent registry; template publishing
workflow and any `work.publish` capability; domain-specific automation adapters.

## References
ADR-013, ADR-015; `docs/OUTBOX.md` (F1.3), `docs/EVENTS.md` (F1.4), `docs/WORKFLOW_TEMPLATES.md`
(F1.5), `docs/AUTHORIZATION.md` (F2.2), `docs/AUDIT_LOG.md`/`AUDIT_INTEGRITY.md`/`EVIDENCE.md`/
`AUDIT_EXPORT.md` (F3.1–F3.4), `docs/WORKFLOW_EXECUTION.md` (F4.1); the E4.0 Epic 4 Plan and the
ADR-016.A Decision Review; Engineering Constitution §§6, 9; migration `e530f5b3d4e5`;
`app/services/workflow_automation.py`, `app/routes/workflows.py`.
