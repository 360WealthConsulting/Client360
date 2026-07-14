# Sprint 4.3 Release Candidate Validation

## Release candidate

- Pull request: #11
- Release candidate: RC3
- Branch: `feature/workflow-process-automation`
- Base release: v0.9.1
- Base migration: `d420f4a2c3d4`
- RC migration head: `e530f5b3d4e5`
- Validation date: July 14, 2026

## Overall result

**PASS — recommended for merge after reviewer approval.** RC3 is functionally and migration ready. Validation identified and repaired three release blockers before final sign-off: published definitions lacked database immutability, dependency validation did not detect multi-hop cycles, and execution records lacked full JSON snapshots. Validation also corrected workflow-level assignment visibility in My Work and two non-rendering templates.

## Automated validation

| Area | Result | Evidence |
|---|---|---|
| Template versioning | Pass | Unique `(code, version)` constraint; creation of version 2 alongside published version 1 tested. |
| Published-template immutability | Pass | PostgreSQL triggers reject updates/deletes to published templates, steps, and dependencies. |
| Execution snapshots | Pass | Instance template snapshot and per-step definition snapshots are captured at launch and tested. |
| Launch and idempotency | Pass | Repeated launch key returns the original instance and one launch event. |
| Pause, resume, cancel, complete, reopen | Pass | State-machine transitions and invalid transition rejection tested. |
| Dependency execution | Pass | Downstream steps remain pending until all dependencies complete. |
| Parallel execution | Pass | Sibling branches activate together after their shared dependency. |
| Conditional execution | Pass | False declarative condition produces a skipped step and does not block downstream completion. |
| Circular dependency prevention | Pass | Self-cycle constraint and recursive multi-hop cycle trigger tested. Cross-template edges are rejected. |
| Approval routing | Pass | Assigned independent approver flow tested through approval decision and step completion. |
| Segregation of duties | Pass | Service checks and database constraint reject requester self-approval. |
| Escalations and SLA | Pass | Overdue active steps create one SLA escalation; retries are idempotent. |
| Automation triggers | Pass | Matching domain events launch configured templates; source event retry does not duplicate. |
| Idempotent actions | Pass | Action ledger prevents duplicate timeline side effects. |
| Assignment integration | Pass | Workflow-instance assignment exposes child steps in authorized My Work results. |
| Queue integration | Pass | Template queue code resolves to runtime queue criteria (`waiting_on=client`) and appears in reusable queue calculations. |
| Timeline publication | Pass | Launch and assignment events publish against the linked person/household. |
| Immutable audit publication | Pass | Workflow launch audit exists; PostgreSQL append-only trigger rejects tampering. |
| Authorization and filtering | Pass | Capability dependencies and person/household/workflow assignment scoping tested. |
| APIs | Pass | OpenAPI contains template, launch, detail, lifecycle, step, approval, event, SLA, and metrics operations. |
| Startup and routes | Pass | FastAPI lifespan startup completed; workflow routes registered. |
| Template rendering | Pass | Workflow list and detail Jinja templates compile and render. |
| Clean migration | Pass | Empty PostgreSQL database migrated from base to `e530f5b3d4e5`. |
| v0.9.1 upgrade | Pass | Database at `d420f4a2c3d4` upgraded to RC3. |
| Downgrade/re-upgrade | Pass | RC3 downgraded to `d420f4a2c3d4` and upgraded again. |
| Sentinel preservation | Pass | Client, assignment, task, document, and legacy workflow each remained present (1/1 each). |
| Migration lineage | Pass | Exactly one Alembic head: `e530f5b3d4e5`. |
| Full suite | Pass | 50 tests passed. |
| Python compilation | Pass | `app`, `migrations`, and `tests` compiled successfully. |

## Manual validation

- Reviewed the workflow list output for all 12 published version-1 templates.
- Reviewed an instance detail render containing status, version, ordered steps, SLA timestamps, and event history.
- Confirmed My Work receives child steps when ownership is assigned at the workflow-instance level.
- Confirmed the Waiting on Client template queue mapping appears on its runtime step.
- Confirmed the draft PR targets `main` and remains unmerged.

## Migration and data safety

The migration is additive from Release 0.9.1. Existing `workflow_instances`, `workflow_steps`, and `work_approvals` remain in place; new columns are nullable or have safe JSON defaults. Legacy workflow rows receive empty snapshots because no historical published definition exists to reconstruct. Downgrading removes Sprint 4.3 definitions, execution-ledger data, and automation data, but preserves all Release 0.9.1 domain and operational rows.

Database triggers enforce append-only events, immutable published definitions, same-template dependencies, cycle prevention, and approval segregation. There is exactly one migration head.

## Known issues and limitations

- Template authoring and version publication are API/database capabilities; RC3 UI is read-only.
- Conditions intentionally support deterministic equality matching only. A richer rules language should be separately designed and sandboxed.
- The enabled action registry is deliberately limited to internal timeline publication. Microsoft, custodian, document, relationship, and portfolio actions must be added through provider/domain adapters.
- The SLA scheduler runs every five minutes in each application process. Database uniqueness makes escalation creation idempotent, but a dedicated worker/leader should be considered before horizontally scaling application replicas.
- Legacy Release 0.9.1 workflow rows retain empty snapshot JSON by design.
- The local Python runtime emits the existing urllib3 LibreSSL warning; it did not affect validation. Production should use a supported OpenSSL build.

## Production readiness and recommendation

RC3 is production-ready for the scoped Sprint 4.3 feature. Migration, rollback, security, rendering, and functional controls passed. Merge PR #11 after code-owner review, with a database backup before deployment and `alembic current` verification after deployment. Do not begin Sprint 4.4 until the release is accepted.
