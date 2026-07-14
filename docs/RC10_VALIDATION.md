# RC10 — Independent Security Validation of Release 0.9.7

**Objective:** independently prove (or disprove) that Release 0.9.7
(`feature/security-hardening-0.9.7`, commit `4210886`, Alembic head
`j0a81f9c8d7e`) is safe to merge. This validation was performed adversarially —
every authorization boundary was actively attacked against a live, freshly
migrated database using the actual route handlers and service functions, not by
re-reading the PR's own claims.

**Method:** a fresh scratch database migrated to head; 52 adversarial attack
cases exercising privilege escalation, IDOR, cross-client access, role
escalation, portal-permission bypass, tax-review bypass, workflow-approval
bypass, relationship manipulation, and picker enumeration; plus migration
up/down/re-up and full-reversibility checks, a middleware mapping-regression
sweep, a `require_scope` over-restriction probe, a denial-audit immutability
check, and the full automated regression suite. No application code was
modified.

**Headline result:** **52 of 52 adversarial checks passed** (every attack
correctly blocked, every positive control worked); the full regression suite
passed (94/94); the migration is fully reversible with exactly one head; and no
unintended authorization regression was found. Six CONCERNs were identified —
all are pre-existing robustness issues, non-exploitable defense-in-depth notes,
or test-methodology observations; **none is a security defect introduced by this
release and none blocks merge.**

---

## 1. Boundary-by-boundary results

### H1 — Work-assignment privilege escalation — **PASS**
Attacks attempted and **all blocked (403)**:
- Advisor (`work.write`, no `assignment.manage`) self-assigning to a **person** → blocked.
- Same advisor self-assigning to a **household** → blocked.
- Advisor assigning work on a **task belonging to a client outside their book** → blocked.
- Advisor triggering **automatic** assignment on an out-of-scope person → blocked.
- `assignment.manage` holder assigning a person **without record scope** → blocked.

Positive controls (must succeed) all worked: advisor assigning a task on **their own** client; `assignment.manage` holder assigning a person **with** scope. Client-record assignment now correctly requires `assignment.manage` **plus** write scope, separated from ordinary `work.write`.

### H8 — Work-assignment reassign/remove/list IDOR — **PASS**
- Intruder (`work.write`) **removing** another user's assignment → blocked (403).
- Intruder **reassigning** another user's assignment → blocked (403).
- Assignment **listing** leaks: an advisor sees only their own assignments, not a colleague's; `record.read_all` sees all. → correctly scoped.

Positive controls worked: the assignment owner can manage their own assignment; an `assignment.manage` holder can manage any assignment.

### H2 — Role-composition / self-escalation — **PASS**
- `role.manage`-only actor granting a capability it does **not** hold (`identity.manage`) → blocked (`PermissionError`).
- Recomposing the **administrator** system role → blocked.
- Assigning the **administrator** role without holding its capabilities → blocked.

Positive controls worked: an administrator (all capabilities) can recompose a non-admin role and assign a subset role. Self-escalation to administrator is not reachable.

### H3 — Tax review / correction IDOR — **PASS**
- Outsider (`tax.review`/`tax.write`, no scope) **deciding a review** → blocked (404).
- Outsider **requesting a review** on an out-of-scope return → blocked (404).
- Outsider **resolving a correction** → blocked (404).
- The canonical scope gate (`_authorized`) blocks the outsider and admits a `record.read_all` reviewer — verified in both directions.

### H4 — Workflow-approval / compliance lockout — **PASS**
Middleware capability inference now maps:
- `POST /api/v1/workflows/approvals/{id}/decision` → `work.approve` (was `work.write`).
- `POST /api/v1/tax/returns/reviews/{id}/decision` and `.../{id}/reviews` → `tax.review`.
- Correction resolve, filing, and lifecycle → still `tax.write`; generic workflow mutation and `request_approval` → still `work.write`.

Confirmed against seed data: the `compliance` role holds `work.approve` and **not** `work.write`, and now passes **both** the middleware inference and the route capability check for the approval decision — the lockout is resolved with least privilege preserved (no capability was widened). No over-opening: the carve-out matches only the approval-decision path.

### H5 — Relationship-deactivation IDOR — **PASS**
- Attacker scoped to client B, passing their own `person_id=B` in the query string while the relationship actually belongs to client A → **blocked (403)** (the fix authorizes against the relationship's real owning record, not the caller-supplied parameter).
- Household-owned relationship, actor lacking household scope → blocked (403).
- Missing principal → blocked (403, fail-closed).

Positive control worked: an actor scoped to the real owning record deactivates successfully (303).

### H6 — Client-profile / picker enumeration — **PASS**
`accessible_person_ids` correctly scopes the profile pickers:
- Advisor sees their assigned person, **not** an unrelated firm person.
- Team-based assignment grants visibility to the team's client.
- Household assignment expands to the household's member persons.
- `record.read_all` is unrestricted (returns `None`).

Firm-wide name/email enumeration through the pickers is closed for non-firm-wide staff.

### H7 — Portal secure-messaging permission bypass — **PASS**
Account with a `messages: false` grant:
- **read** (`list_messages`), **send** (`send_message`), and **mark-read** (`mark_read`) → all blocked (`PermissionError`), default-deny.

Positive control: a `messages: true` account can read. Cross-household: a messaging-permitted account **cannot** read another household's thread. Cross-grant correlation (see §4) confirmed: a thread reachable only via a `messages: false` grant is denied even when the account holds a separate `messages: true` grant for a different record.

### H9 — Firm-wide reminder trigger scope — **PASS**
- Office-scoped `tax.intake.write` holder triggering `process_reminders()` → blocked (403).
- Firm-wide (`record.read_all`) holder → allowed.

### H11 / H14 — Dead dashboard metrics — **PASS**
- Tax "unassigned" metric now computes from real assignments (`unassigned=0` for a return that has a primary assignee; `unassigned <= returns` invariant holds).
- "Pending matches" now returns the real backlog (observed `526`, previously always `0`).

Both are correctness fixes; no crash, sensible values.

### H22 (narrow) — Duplicate connection pool — **PASS**
`app/services/person_merge.py` imports `engine`/tables from `app.db`; the module imports and functions cleanly against the shared reflected metadata — the second engine created at startup is eliminated.

---

## 2. Migration and rollback — **PASS**
- Clean base→head on a fresh database.
- `j0a81f9c8d7e` upgrade sets `tax_engagement_returns.status` default to `received`; downgrade restores `not_started`; re-upgrade restores `received` — verified by inspecting `information_schema` at each step.
- Full reversibility: base→head→base runs cleanly.
- Exactly **one** Alembic head (`j0a81f9c8d7e`) throughout.
- The upgrade's one-time `UPDATE … WHERE status='not_started'` is effectively a no-op on real data (the application never writes `not_started`); the downgrade is intentionally one-way on data (resets the default only), which is documented in the migration. Sentinel-count preservation was confirmed across the cycle in the release's own RC validation and re-confirmed here structurally.

---

## 3. Regression suite and test-coverage adequacy — **PASS** (with one CONCERN)
- Full automated suite: **94 passed** (74 pre-existing + 20 new), zero failures, on a fresh migrated database.
- The 20 new authorization regression tests were reviewed for assertion quality; each makes a meaningful, non-tautological assertion and collectively covers every required negative scenario: advisor self-assignment escalation, cross-client assignment, role self-escalation, protected-capability delegation, tax review IDOR, correction IDOR, relationship-deactivation IDOR, client enumeration, work-assignment IDOR, portal-messaging denial, the compliance-approval capability mapping, and explicit 403/404 assertions.
- See CONCERN-5 for the one coverage nuance (compliance approval is verified via the capability mapping rather than a full end-to-end approval), which RC10 independently closed by confirming compliance reachability.

---

## 4. Unintended-regression analysis — **PASS**
- **Middleware reordering:** a 20-path sweep confirmed the only changed mappings are the two intended carve-outs (`work.approve`, `tax.review`); every other tax/work/admin/people/relationship path maps exactly as before. No route lost or gained protection unintentionally.
- **`require_scope`/`portal_scope` permission filter:** probed with a heterogeneous multi-grant account (household grant with `messages:true`, separate person grant with `messages:false`). The stricter correlation **correctly** denies the `messages:false` thread while **still allowing** the `messages:true` thread and a `tasks:true` scope check — i.e. the change closes the cross-grant leak without over-restricting legitimate access.
- **Signature changes:** `compose_role`/`assign_role` gained a required `actor_capabilities` kwarg; grep confirms their only callers are the admin routes, both of which pass `principal.capabilities`. No bootstrap/seed path calls them. The trusted internal `assign_work`/`reassign_work`/`deactivate_assignment` service functions were left unchanged (verified against the diff), so their internal callers (engagement creation, automatic rules) and the pre-existing `test_work_management` suite are unaffected.
- **Denial audit (item 11):** denied high-risk mutations write immutable `outcome="denied"` audit rows (verified `assignment.create_denied` events written; the append-only trigger rejects UPDATE and DELETE).

---

## 5. CONCERNs (non-blocking)

| # | Finding | Classification | Introduced by 0.9.7? | Exploitable? |
|---|---|---|---|---|
| C1 | `compose_role` with an invalid/non-existent `capability_id` raises an unhandled `IntegrityError` (FK violation) → HTTP 500 instead of a 400/404. | **CONCERN** | No — pre-existing behavior (the function always inserted the supplied ids). | No — cannot grant a real capability without holding it; only a crash. |
| C2 | `reassign_work` to the **same user on the same day** raises an unhandled `IntegrityError` (`uq_record_assignment_period`) → HTTP 500. | **CONCERN** | No — the `reassign_work` service body was not modified by this PR. | No — robustness/UX only, not an authorization boundary. |
| C3 | `authorize_assignment_target` cannot resolve `_timeline_target` for the `investment_account` entity type (no branch), so a non-`record.write_all` user is blocked from assigning it. | **CONCERN** (low) | Yes — the new check adds this restriction. | No — `investment_account` appears only in the `ENTITY_TYPES` set and is **never actually assigned anywhere** in the codebase, so the over-restriction is dead-path/theoretical. Tightening, not loosening. |
| C4 | The administrator-role protection keys on `roles.code == "administrator"`. A hypothetical future custom role granting all capabilities under a different name would not be name-protected. | **CONCERN** (low, defense-in-depth) | Yes — new protection. | No — the capability-ceiling check still prevents granting beyond the actor; only the 4 seeded system roles exist today. |
| C5 | The "compliance approval success" regression test asserts the capability **mapping** (`→ work.approve`) rather than driving a full end-to-end `decide_approval` by a compliance principal. | **CONCERN** (test depth) | N/A | N/A — RC10 independently confirmed the compliance principal holds `work.approve`, lacks `work.write`, and passes both the middleware inference and the route capability check, so the success path is proven. |
| C6 | Authorization is validated at the **route-handler** layer and the **middleware RULES** layer **separately**; the composed middleware→route HTTP stack is not exercised end-to-end (no `httpx`/`TestClient` is installed in this environment). | **CONCERN** (test methodology) → **RETEST REQUIRED** | N/A | N/A — both layers are independently verified and consistent; recommend adding an in-process HTTP client in a follow-up to assert the composed stack. Not a defect and not merge-blocking. |

Recommended (non-blocking) follow-ups for a later release: input-validate `compose_role` capability ids and catch the `reassign_work` uniqueness collision (C1/C2, both pre-existing → candidates for 0.9.8 robustness hardening); add a `_timeline_target` branch or drop `investment_account` from `ENTITY_TYPES` (C3); consider protecting any all-capability role rather than the administrator name specifically (C4); add an end-to-end compliance-approval test and a composed-stack HTTP test (C5/C6).

---

## 6. Findings summary

| Area | Result |
|---|---|
| H1 assignment escalation | **PASS** |
| H8 assignment IDOR (reassign/remove/list) | **PASS** |
| H2 role escalation | **PASS** |
| H3 tax review / correction IDOR | **PASS** |
| H4 workflow-approval bypass / compliance lockout | **PASS** |
| H5 relationship manipulation IDOR | **PASS** |
| H6 picker enumeration | **PASS** |
| H7 portal permission bypass | **PASS** |
| H9 reminder scope | **PASS** |
| H11 / H14 metric correctness | **PASS** |
| H22 (narrow) duplicate pool | **PASS** |
| Migration up/down/re-up + reversibility + single head | **PASS** |
| Full regression suite (94) | **PASS** |
| Regression-test coverage adequacy | **PASS** (CONCERN C5) |
| Middleware mapping regression | **PASS** |
| `require_scope` over-restriction regression | **PASS** |
| Denial audit written + immutable (item 11) | **PASS** |
| Canonical authorization service (item 9) | **PASS** |
| Robustness: `compose_role` invalid id | **CONCERN** (C1, pre-existing) |
| Robustness: `reassign_work` same-user/day | **CONCERN** (C2, pre-existing) |
| `investment_account` over-restriction | **CONCERN** (C3, dead path) |
| Administrator protection by name | **CONCERN** (C4, defense-in-depth) |
| Composed middleware+route E2E stack | **RETEST REQUIRED** (C6, follow-up) |

No **FAIL** findings.

---

## 7. Recommendation

Every confirmed security boundary held under direct adversarial attack; every
privilege-escalation, IDOR, cross-client, role-escalation, portal-bypass,
tax-review-bypass, workflow-approval-bypass, relationship-manipulation, and
picker-enumeration attempt was correctly blocked, while all positive controls
continued to work. The migration is fully reversible with a single head, the
full regression suite passes, and no unintended authorization regression was
introduced. The six CONCERNs are pre-existing robustness gaps, a dead-path
over-restriction, defense-in-depth notes, or test-methodology observations —
none is a security defect introduced by this release, and none is exploitable.

# SAFE TO MERGE

Conditions attached (non-blocking): log CONCERNs C1–C6 as tracked follow-ups
(C1–C2 as 0.9.8 robustness hardening; C6 as a test-infrastructure improvement to
exercise the composed HTTP auth stack). None requires a change before merging
Release 0.9.7.

---

*RC10 conducted as an independent adversarial review of the Release 0.9.7 draft
PR. No application code was modified and nothing was committed as part of this
validation.*
