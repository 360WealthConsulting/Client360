# Client360 — Workflow API & Administrative Surface (F4.8 / Epic 4)

The minimal, least-privilege API surface that exposes the completed Epic 4 workflow
functionality. F4.8 **surfaces existing capabilities only** — it introduces **no new
workflow behavior, states, approval/SLA/automation logic, or broad permissions**. All
handlers delegate to the existing services (F4.1–F4.7).

`app/routes/workflows.py`

## Reconciliation (ADR-013 / ADR-016)
- **Additive routes only.** Three new endpoints are added alongside the existing 16
  workflow API/UI routes; the engine, state machine, events, automation consumers,
  approval engine, SLA engine, and audit/evidence layers are unchanged.
- **No new business logic in routes.** Handlers validate input and delegate to existing
  services; the routes module contains no engine/DB mutation.
- **No migration, no new capability.** Route inventory: 306 → **309**.

## New endpoints (this feature)
| Method & path | Capability | Delegates to | Purpose |
|---|---|---|---|
| `POST /api/v1/workflows/approvals/{approval_id}/reassign` | `work.write` | `reassign_approval` (F4.5) | Reassign a pending approval (SoD-checked) |
| `GET /api/v1/workflows/{instance_id}/history` | `work.read` | `workflow_detail(...)["events"]` | Workflow event history (record-scoped) |
| `GET /api/v1/workflows/{instance_id}/evidence` | `audit.read` | `list_workflow_evidence` (F4.7) | Retrieve write-once workflow evidence |

### Reassignment
`{new_approver_user_id?, new_approver_team_id?, reason?}` → `{ "id": approval_id }`.
Enforces the existing separation-of-duty rules (new approver ≠ requester; pending only);
returns 409 on an invalid reassignment.

### History
Returns `{ "events": [...] }` — exactly the events already exposed by `workflow_detail`,
enforcing the existing **record-scope** authorization (404 not found / 403 out of scope).

### Evidence retrieval
Returns `{ "evidence": [...] }` — the workflow's write-once evidence records (F4.7),
reference-only, with `evidence_metadata` re-redacted for defense-in-depth. Gated on the
**auditor** capability `audit.read` (consistent with F3.4), separate from operational
`work.*` — a user with only `work.write` cannot read evidence.

## Full workflow API surface (for reference — existing + new)
- **Read** (`work.read`): `GET /workflows`, `GET /workflows/{id}`, `GET /api/v1/workflows/templates`,
  `GET /api/v1/workflows/{id}`, **`GET /api/v1/workflows/{id}/history`**.
- **Write** (`work.write`): `POST /api/v1/workflows` (launch); `POST .../{id}/{pause,resume,cancel,complete,reopen}`;
  `POST .../steps/{id}/complete`; `POST .../steps/{id}/approvals` (request); **`POST .../approvals/{id}/reassign`**;
  `POST .../events`; `POST .../automation/sla`.
- **Approve** (`work.approve`): `POST .../approvals/{id}/decision`.
- **Auditor** (`audit.read`): **`GET .../{id}/evidence`**.
- **Metrics** (`capacity.read`): `GET .../metrics`.

## Authorization (least-privilege, separated)
- Every protected route maps to a single, appropriate capability via
  `require_capability(...)` (401 if unauthenticated, 403 if the capability is absent).
- **Separation preserved:** read (`work.read`) vs. write (`work.write`) vs. approval
  decision (`work.approve`) vs. audit/evidence (`audit.read`) vs. metrics
  (`capacity.read`). No broad permission (e.g. `record.read_all`) is introduced.
- **SoD unaffected:** approval separation-of-duty is enforced in the service + DB
  regardless of capabilities — the reassignment route cannot bypass it.
- **Record scope preserved:** history reuses `workflow_detail`'s record-scope check.

## Out of scope (not implemented)
Dashboards, reporting, workflow redesign, new states, new approval/SLA/automation
behavior, and broad administrative permissions.

## Compatibility (ADR-016 Compatibility Contract)
Existing routes, service signatures, execution semantics, and all Epic 4 layers are
unchanged. The only inventory change is the 3 additive routes (306 → 309). No new
capability, no schema change, no migration.

## References
ADR-013, ADR-015, ADR-016; `docs/WORKFLOW_APPROVALS.md` (F4.5),
`docs/WORKFLOW_EVIDENCE_AUDIT.md` (F4.7), `docs/AUDIT_EXPORT.md` (F3.4),
`docs/AUTHORIZATION.md` (F2.2); `app/routes/workflows.py`.
