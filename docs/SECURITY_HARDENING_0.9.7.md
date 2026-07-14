# Release 0.9.7 — Security Hardening

Source of truth: Release v0.9.6 on `main` (Alembic head `i970d9f7b8c9`).
New Alembic head: `j0a81f9c8d7e`.
Authoritative backlog: [RC8 Architecture Review](RC8_ARCHITECTURE_REVIEW.md) and
[RC9 Architecture Verification](RC9_ARCHITECTURE_VERIFICATION.md).

Release 0.9.7 fixes the confirmed, RC9-verified security, authorization,
record-scope, and workflow-permission defects before Sprint 5.4 begins. No new
feature work is included. Least privilege, immutable audit, and record-level
authorization are all preserved — no fix widens access to resolve an
authorization failure.

## Objective and approach

Every fix enforces authorization at the **route boundary** and routes its
decision through a single canonical record-scope model
(`app/security/authorization.py`, built on the existing
`app.security.policy.has_record_scope`). The trusted internal service functions
(`assign_work`, `reassign_work`, `deactivate_assignment`, …) are left unchanged
so their legitimate internal callers (engagement creation, automatic assignment
rules, the scheduler) keep working. This closes the exploited gaps without
introducing a second authorization model.

## Findings fixed

| RC9 | Finding | Fix | Files |
|---|---|---|---|
| **H1** | Work-assignment privilege escalation — any `work.write` holder could self-grant client access via `/api/v1/work/assignments` | Assigning a person/household now requires `assignment.manage` **and** write scope over the record; assigning work on any other entity requires write scope over the underlying client record. Assignment administration is separated from ordinary work mutation. | `app/security/authorization.py`, `app/services/work_management.py`, `app/routes/work.py` |
| **H8** | Work-assignment IDOR — reassign/remove checked only capability, not ownership | Reassign/remove/automatic now verify the caller may manage the assignment (`assignment.manage`, `record.write_all`, or own user/team). Listing was already scoped via `authorized_assignments`. | `app/security/authorization.py`, `app/services/work_management.py`, `app/routes/work.py` |
| **H2** | Role-composition privilege escalation — `role.manage` could grant any capability / assign any role | Ceiling check: a principal may only grant capabilities it already holds, and may only assign a role whose capability set is a subset of its own (prevents self-escalation to administrator). The `administrator` system role cannot be recomposed. | `app/services/identity.py`, `app/routes/admin.py` |
| **H3** | Tax review/correction IDOR — two endpoints skipped the record-scope check every sibling applied | `api_review_decision` and `api_resolve` now resolve the owning return and enforce the same canonical `_authorized` helper as every other tax-return endpoint. | `app/services/tax_return_lifecycle.py`, `app/routes/tax_returns.py` |
| **H4** | Compliance workflow-approval lockout — middleware demanded `work.write` for a route that requires `work.approve` | Middleware carve-outs map approval-decision and tax-review routes to their dedicated capabilities (`work.approve`, `tax.review`) before the coarse `.read→.write` inference runs. Least privilege preserved; correction resolution stays on `tax.write`. | `app/security/middleware.py` |
| **H5** | Relationship-deactivation IDOR — scope was checked against a caller-supplied query param, not the mutated record | The route resolves the relationship's actual owning record and authorizes against that with write scope. | `app/services/relationships.py`, `app/routes/relationships.py` |
| **H6** | Client-profile / picker enumeration — profile pages leaked every active person (and email) firm-wide | The "available people" pickers on the person and household profiles are filtered through `accessible_person_ids`, limiting non-firm-wide staff to their own book. | `app/security/authorization.py`, `app/routes/people.py`, `app/routes/households.py` |
| **H7** | Portal secure-messaging permission not enforced on read/send/mark-read | `portal_scope`/`require_scope` gained an optional permission filter; the messaging paths compute reachability using only `messages`-permitted grants (default deny, correlated to the specific grant). Attachments inherit the same scope. | `app/portal/service.py` |
| **H9** | `process_reminders()` firm-wide trigger reachable by office-scoped `tax.intake.write` | The manual HTTP trigger now additionally requires `record.read_all`; the daily scheduler still calls the service directly. | `app/routes/tax_intake.py` |
| **H11** | Dead "Unassigned" tax dashboard metric + stale `not_started` column default | Metric rewritten to count returns with no active assignment; migration `j0a81f9c8d7e` aligns the column default to `received` and normalizes residual rows. | `app/services/tax_domain.py`, `migrations/versions/j0a81f9c8d7e_*.py` |
| **H14** | Dead "pending matches" dashboard metric (queried a never-persisted value) | Replaced with `count_pending_match_groups()`, which mirrors the real /matches backlog computation. | `app/routes/matches.py`, `app/services/dashboard.py` |
| **H22 (narrow)** | Duplicate connection pool via `person_merge` → `app.database.schema` | `person_merge` now imports the shared reflected metadata/engine from `app.db`, eliminating the second engine created at startup. | `app/services/person_merge.py` |

## Canonical authorization service (item 9)

`app/security/authorization.py` is the single entry point for record-scope
decisions used by the fixed routes:

- `record_in_scope(principal, entity_type, entity_id, *, write=False)` — wraps
  `has_record_scope` (the one authorization model).
- `assignment_manageable(connection, principal, row)` — H8 ownership rule.
- `accessible_person_ids(connection, principal)` — H6 picker scoping (`None`
  means the principal holds `record.read_all`).
- `team_ids(connection, principal)` — shared active-team resolution.

The tax subsystem continues to authorize through its established
`list_engagements`/`_authorized` office+assignment helper (a legitimately
different scope model), now applied consistently across **every** tax-return
mutation including the two endpoints that previously skipped it. The broader
consolidation of the three historical scope implementations
(`has_record_scope` / `_scope_filter` / `authorized_assignments`) into one
service remains an RC9 Release 1.0 item; this release consolidates the checks
used by the affected routes without ripping out the tax office-scope model.

## Audit (item 11)

Denied high-risk mutations now emit immutable `outcome="denied"` audit events:
`assignment.create_denied` / `reassign_denied` / `remove_denied` /
`automatic_denied`, `authorization.role_assign_denied` /
`role_compose_denied`, `relationship.deactivate_denied`, and
`tax.intake.reminders_denied`. Successful assignment, role, relationship, and
approval changes remain audited exactly as before. A tolerant `audit_denied`
helper was added so denial auditing can never convert a 403 into a 500.

## Capabilities and endpoints

No capabilities were added, removed, or re-granted. The seeded role→capability
matrix is unchanged; the fixes change **enforcement**, not grants:

- `administrator`: unchanged (holds everything).
- `advisor` (`work.write`, no `assignment.manage`): can no longer assign client
  records or manipulate other users' assignments.
- `operations` (`assignment.manage`, `work.write`): can administer assignments,
  still bounded by record scope for person/household targets.
- `compliance` (`work.approve`, `record.read_all`, no `work.write`): **can now
  approve workflow work** (H4) — previously locked out.

Endpoints with changed authorization behavior: `POST /api/v1/work/assignments`,
`POST /work/assignments/{entity_type}/{entity_id}`,
`POST /api/v1/work/assignments/{id}/reassign`,
`DELETE /api/v1/work/assignments/{id}`,
`POST /api/v1/work/assignments/automatic`, `POST /admin/user-roles`,
`PUT /admin/roles/{id}/capabilities`,
`POST /api/v1/tax/returns/reviews/{id}/decision`,
`POST /api/v1/tax/returns/review-corrections/{id}/resolve`,
`POST /relationships/{id}/deactivate`, `GET /people/{id}`,
`GET /households/{id}`, `POST /api/v1/tax/intake/reminders`, and the portal
secure-messaging read/send/mark-read paths.

## Migration

`j0a81f9c8d7e_align_tax_return_status_default` — `ALTER COLUMN
tax_engagement_returns.status SET DEFAULT 'received'` plus a one-time normalize
of any residual `not_started` rows. Fully reversible; verified via
base→head, v0.9.6→head, downgrade→v0.9.6, and re-upgrade with sentinel
preservation. Exactly one Alembic head is maintained (`j0a81f9c8d7e`).

## Validation

- Full automated suite: **94 passed** (74 existing + 20 new security regression
  tests), zero regressions.
- Python compilation (`compileall`) clean.
- FastAPI startup/shutdown clean; OpenAPI generates; 167 routes registered.
- Staff and portal templates render.
- Clean base→head migration; v0.9.6 upgrade, downgrade, and re-upgrade; sentinel
  data (people, documents, workflows, portal requests, audit events, tax
  engagements/returns) byte-identical across the full cycle.
- Seeded-role authorization matrix confirmed (compliance holds `work.approve`
  without `work.write`; advisor holds `work.write` without `assignment.manage`).
- Immutable audit trigger rejects UPDATE and DELETE.
- Negative cross-record access verified by the regression suite.
- `git diff --check` (whitespace) clean.

## Constraints honored

No Sprint 5.4 work. No Release 0.9.8 items implemented (H10 token encryption,
H15–H20 performance/index work, H21 constraints, and the full authorization
consolidation remain deferred per RC9). Access was never widened to resolve an
authorization failure. Immutable audit and record-level authorization
preserved. Backward compatibility preserved (internal service callers and all
existing tests unchanged). Exactly one Alembic head. No production data changed
manually.
