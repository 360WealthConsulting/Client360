# Client360 — Workflow Audit, Evidence & Capability Reconciliation (F4.7 / Epic 4)

Completes the workflow audit and evidence model: every **material workflow outcome**
produces a tamper-evident **audit** record (F3.1 / ADR-015) and a write-once
**evidence** record (F3.3), linked together and traceable to the underlying domain
record and the responsible actor/process. Audit and evidence **observe and document**
workflow activity — they never drive workflow execution. Reconciles workflow
operations with the capability model.

`app/services/workflow_evidence.py` · audit/evidence wiring in
`app/services/workflow_automation.py`

## Reconciliation (ADR-013 / ADR-016)
- **Reuses existing infrastructure** — `write_audit_event` (F3.1 hash chain) and
  `record_evidence` (F3.3 write-once). No new audit or evidence mechanism.
- **Additive.** One optional param added: `record_evidence(evidence_uid=…)` for
  deterministic correlation. No schema change, no migration, no new route, no new
  capability. Engine behavior/state is unchanged — audit/evidence are side records.

## Audit & evidence coverage matrix
Every material outcome writes one audit event and one linked write-once evidence
record (deterministic uid `wf:<outcome>:<audit_event_id>`):

| Material outcome | Audit action | Audit entity | Added in |
|---|---|---|---|
| Workflow launch | `workflow.launched` | `workflow_instance` | E-earlier (evidence: F4.7) |
| Lifecycle transition | `workflow.{paused,resumed,cancelled,completed,reopened}` | `workflow_instance` | (evidence: F4.7) |
| Step activation | `workflow.step.activated` | `workflow_step` | **F4.7** |
| Step completion | `workflow.step.completed` | `workflow_step` | **F4.7** |
| Auto-completion (all steps done) | `workflow.completed` | `workflow_instance` | **F4.7** |
| Approval request | `workflow.approval.requested` | `work_approval` | F4.5 (evidence: F4.7) |
| Approval decision | `workflow.approval.decided` | `work_approval` | F4.5 (evidence: F4.7) |
| Approval reassignment | `workflow.approval.reassigned` | `work_approval` | F4.5 (evidence: F4.7) |
| Automation execution | `workflow.automation.executed` | `automation_action` | **F4.7** |
| SLA escalation | `workflow.sla.escalated` | `workflow_escalation` | F4.6 (evidence: F4.7) |

## Traceability chain
Each outcome is correlated across the layers: **workflow instance / step** →
**domain event** (`workflow_events`, referenced in evidence metadata) → **outbox
event** (F1.4 envelope, where a lifecycle/approval/SLA event is published) →
**audit record** (`audit_events`, hash-chained) → **evidence record**
(`evidence.audit_event_id` FK + `evidence_metadata` references). Evidence carries
**references only** (ids/labels) — never PII or binary content (Constitution §9).

## Determinism, idempotency & retry-safety
- **Deterministic:** an evidence record's uid is `wf:<outcome>:<audit_event_id>`.
- **Idempotent / write-once:** `record_workflow_evidence` returns the existing record
  if the uid is already present; the `evidence` table is immutable (F3.3 trigger) and
  `evidence_uid` is unique.
- **Retry-safe:** idempotent operations (SLA escalation, automation) write their audit
  event only once, so retries create **no** duplicate audit or evidence. Non-idempotent
  transitions/step-completions are distinct occurrences, each with its own audit +
  evidence.
- **Never changes state:** `record_workflow_evidence` only reads/writes the evidence
  store; it cannot mutate `workflow_instances`/`workflow_steps`.

## Capability reconciliation
Reviewing the workflow operations introduced through F4.1–F4.6:

| Externally-callable operation (route) | Capability | Notes |
|---|---|---|
| View workflow / templates / detail | `work.read` | least-privilege read |
| Launch, transitions, step complete, request approval, event trigger, manual SLA | `work.write` | execution/write |
| Approval **decision** | `work.approve` | **separated** from `work.write` |
| Workflow metrics | `capacity.read` | read-only metrics |

Findings:
- **Each externally-callable protected operation maps to an appropriate capability.**
- **No new or broad capability introduced** (no `record.read_all`); no documented gap.
- **Execution, approval, and metrics are appropriately separated** (`work.write` vs.
  `work.approve` vs. `capacity.read`).
- **SoD cannot be bypassed by capability assignment.** Separation-of-duty (requester ≠
  approver; only the assigned approver decides) is enforced in the service layer **and**
  the DB (`ck_work_approval_segregation`), independent of the caller's capabilities —
  holding `work.approve` does not let a requester approve their own work.
- **Internal/background processes are not user-facing permissions.** Outbox event
  publication, automation consumers, and SLA evaluation run in the background (or the
  scheduler); they do not introduce user capabilities. (The manual SLA trigger route is
  appropriately `work.write`-gated.)
- **Deferred (F4.8):** `reassign_approval` currently has **no route** (not externally
  callable); when F4.8 exposes it, it should map to an appropriate capability
  (e.g. `work.write`) — recorded here as a follow-up, not a gap today.

## Out of scope (deferred)
UI, dashboards, reporting, new workflow business capabilities/states, approval- or
SLA-policy changes, and F4.8 routes/screens.

## Compatibility (ADR-016 Compatibility Contract)
Public routes, service signatures (only additive optional params where required for
correlation), execution semantics, the state machine, event publication, automation
consumers, the approval engine, the SLA engine, DB guarantees, and the route inventory
(306) are **unchanged**. No new capability, no schema change, no migration.

## References
ADR-013, ADR-015, ADR-016; `docs/AUDIT_LOG.md` (F3.1), `docs/EVIDENCE.md` (F3.3),
`docs/AUDIT_EXPORT.md` (F3.4), `docs/WORKFLOW_APPROVALS.md` (F4.5),
`docs/WORKFLOW_SLA.md` (F4.6), `docs/AUTHORIZATION.md` (F2.2);
`app/services/workflow_automation.py`, `app/services/workflow_evidence.py`.
