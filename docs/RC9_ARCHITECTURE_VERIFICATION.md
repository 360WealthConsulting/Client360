# Client360 — RC9 Architecture Verification

**Purpose:** independently verify every High Priority issue (H1–H22) from `docs/RC8_ARCHITECTURE_REVIEW.md` before Sprint 5.4 begins. Each issue below was re-examined by a dedicated adversarial verification pass that read the current code fresh, traced every call chain and helper function, cross-checked actual role/capability seed data in the migrations (exploitability depends on who really holds a capability, not just whether a check is missing), and actively tried to refute the original claim before confirming it.

**Method:** six independent verification passes, grouped to mirror the underlying code clusters (work/admin/middleware; tax-return/relationship/people IDOR; portal/Microsoft-365; tax correctness; scalability; database schema). No application code was modified. No fixes were implemented.

**Result summary:** of 22 High Priority claims, **21 are confirmed defects** (three with materially reduced current blast radius due to today's role/capability seed data, two with severity revised down, one with severity revised up), and **1 is a false positive** (H12 — the underlying code inconsistency is real, but exhaustive tracing shows it produces no actual authorization-outcome difference in the current codebase).

---

## Classification Summary

| # | Title | Classification | Severity (re-assessed) | Live-exploitable today? |
|---|---|---|---|---|
| H1 | Self-grant record access via `/api/v1/work/assignments` | Confirmed defect | **Critical** | Yes — `advisor`/`operations` hold `work.write` |
| H2 | Unrestricted role composition/assignment (`role.manage`) | Confirmed defect | High | No — `role.manage` seeded only to `administrator` today |
| H3 | Tax review-decision/correction-resolve skip record-scope check | Confirmed defect | High | Yes — `tax.review`/`tax.write` holders |
| H4 | Middleware/route capability granularity mismatch | Confirmed defect | **Critical** (workflow half) / latent (tax half) | Yes — `compliance` role locked out of `work.approve` today |
| H5 | Relationship deactivation IDOR | Confirmed defect | High | Yes |
| H6 | Firm-wide "available people" picker PII enumeration | Confirmed defect | High | Yes |
| H7 | Portal messages permission not enforced on read/send/mark-read | Confirmed defect | High | No — no HTTP path creates a `messages:false` grant yet |
| H8 | Work assignment reassign/deactivate IDOR | Confirmed defect | High | Yes |
| H9 | `process_reminders()` has no office/assignment scope | Confirmed defect | High | No — `tax.intake.write` seeded only to `administrator` today |
| H10 | Microsoft OAuth tokens plaintext + no refresh | Confirmed defect | High | Yes (both halves) |
| H11 | Dead "Unassigned" tax dashboard metric | Confirmed defect | Medium *(down from High)* | N/A (visibility bug, not security) |
| H12 | Household-context drift between intake and lifecycle | **False positive** | N/A | N/A |
| H13 | Document auto-matching substring containment | Confirmed defect | High | Yes |
| H14 | Dead "pending matches" dashboard metric | Confirmed defect | Medium *(down from High)* | N/A (visibility bug, not security) |
| H15 | `work_items()` unfiltered dual full-table scan | Confirmed defect | High | Yes (performance) |
| H16 | Tax intake `staff_dashboard()`/`api_detail()` N+1 | Confirmed defect | High | Yes (performance) |
| H17 | Portal `dashboard()` fan-out N+1, `portal_scope()` ×4 | Confirmed defect | High | Yes (performance) |
| H18 | Unbounded firm-wide list endpoints | Confirmed defect | High | Yes (performance) |
| H19 | Portfolio concentration search N+1 | Confirmed defect | **High** *(up from Medium — actual cost is up to 7 queries/row, not 2-4)* | Yes (performance) |
| H20 | ~48 missing indexes | Confirmed defect | High (Critical for hot tables) | Yes (performance, growing) |
| H21 | ~16 missing constraints | Confirmed defect | Medium | No (latent data-integrity risk) |
| H22 | `app/db.py`/`app/database/schema.py` split | Confirmed defect | Medium | Yes — dual connection pool confirmed **actually occurring**, not theoretical |

---

## Detailed Verification — Security / Authorization (H1–H10)

### H1: Self-grant record access via `/api/v1/work/assignments`
**Classification:** Confirmed defect
**Affected files:** `app/routes/work.py:62-77,121-125`, `app/services/work_management.py:16-17,57-85`, `app/security/policy.py:8-12`
**Affected endpoints:** `POST /api/v1/work/assignments`, `POST /work/assignments/{entity_type}/{entity_id}`
**Exploit scenario:** `assign_work()` inserts directly into `record_assignments` for `entity_type` including `"person"`/`"household"`, with `user_id` taken verbatim from the request body, gated only by `require_capability("work.write")`. Per `migrations/versions/d420f4a2c3d4_add_work_management_platform.py:82-88`, `work.write` is granted to `advisor`, `operations`, and `administrator` — but the intended gate, `assignment.manage`, is granted only to `operations`/`administrator` (`migrations/versions/c410f4a1b2c3_add_firm_identity_rbac_audit.py:27`). Any `advisor` can `POST {"entity_type":"person","entity_id":<any id>,"assignment_role":"secondary","user_id":<self>}` and immediately gain full read/write on any client — record IDs are sequential integers, requiring no prior visibility to guess valid targets.
**Severity:** Critical — reachable by the most common non-admin role in the system, no prior access required.
**Recommended fix:** Require `assignment.manage` in `assign_work`/`reassign_work`/`deactivate_assignment` whenever `entity_type in {"person","household"}`; keep `work.write` only for narrower operational entity types (task, document, workflow_instance/step).
**Estimated effort:** S (hours–1 day) — one capability check keyed off `entity_type`, no schema change.
**Migration impact:** None.
**Backward compatibility impact:** Breaks any current `advisor` workflow that legitimately self-assigns to a person/household without `assignment.manage` — likely none exist today given this is the escalation path itself, but should be confirmed with product owner before shipping.
**Release risk:** Low to ship; high to defer — this is full privilege escalation reachable today by the most common role.

### H2: Unrestricted role composition/assignment
**Classification:** Confirmed defect (currently zero blast radius)
**Affected files:** `app/routes/admin.py:42-48`, `app/services/identity.py:27-34`
**Affected endpoints:** `PUT /admin/roles/{role_id}/capabilities`, `POST /admin/user-roles`
**Exploit scenario:** `compose_role()`/`assign_role()` allow any `role.manage` holder to set any role's capability set (including `identity.manage`/`role.manage`/all of `administrator`'s grants) or assign any role to any user, with no ceiling/subset check against the actor's own capabilities, and no protection on `system_role=True` rows. **Caveat confirmed via migration grants:** `role.manage` is seeded only to `administrator` today (`migrations/versions/c410f4a1b2c3_add_firm_identity_rbac_audit.py:24-28`), who already holds every capability — so no current non-admin principal can exploit this. The defect becomes immediately exploitable the instant `role.manage` is ever delegated to a narrower role (e.g. a future "team lead" or "HR/People Ops" role), which is the entire reason a distinct `role.manage` capability exists separately from `identity.manage`.
**Severity:** High (would be Critical the moment `role.manage` is delegated more narrowly).
**Recommended fix:** In `compose_role`, reject any `capability_id` not already held by the acting principal; block mutation of `system_role=True` roles. In `assign_role`, reject assigning a role whose capability set isn't a subset of the actor's own.
**Estimated effort:** M (2-5 days) — needs the actor's principal threaded into the service layer (currently absent), plus tests.
**Migration impact:** None required; optionally leverage the existing `roles.system_role` column for an immutability guard.
**Backward compatibility impact:** None against current seed roles.
**Release risk:** Low to ship now (defensive, no current dependency); should specifically gate any future decision to delegate `role.manage` more broadly.

### H3: Tax review-decision/correction-resolve skip record-scope check
**Classification:** Confirmed defect
**Affected files:** `app/routes/tax_returns.py:52-59` (`api_review_decision`, `api_resolve` — neither calls `_authorized()`, unlike every sibling endpoint at lines 40-64), `app/services/tax_return_lifecycle.py:73-90` (`decide_review`, `resolve_correction`)
**Affected endpoints:** `POST /api/v1/tax/returns/reviews/{review_id}/decision`, `POST /api/v1/tax/returns/review-corrections/{correction_id}/resolve`
**Exploit scenario:** `decide_review` only rejects the caller if `review["reviewer_user_id"]` is set and differs — an identity check, not an office/scope check — and `request_review` explicitly permits team-only assignment (no `reviewer_user_id`), leaving zero caller-identity gate for team-routed reviews. `resolve_correction` has no ownership/scope check whatsoever — a bare `UPDATE ... WHERE status='open'` keyed only on `correction_id`. No compensating middleware control exists: the `RULES` regex only gates role-level `tax.write`/`tax.review`, and neither `RECORD_PATH` nor `FIRM_WIDE_COLLECTION` matches `/api/v1/tax/returns/...`. Any `tax.review`/`tax.write` holder can decide or resolve any review/correction firm-wide, regardless of office/assignment scope.
**Severity:** High.
**Recommended fix:** After fetching the review/correction row, resolve `tax_engagement_return_id` and apply the same scope check `_authorized()`/`list_engagements` uses before allowing the mutation.
**Estimated effort:** S (hours–1 day) — one extra lookup query + reuse of existing scope logic in two functions.
**Migration impact:** None.
**Backward compatibility impact:** A firm-wide centralized QA/review team (if one exists operationally) acting outside normal record scope would need `record.write_all` or explicit assignment going forward — confirm this isn't an intended pattern before shipping.
**Release risk:** Low to ship (fails closed, mirrors an established pattern in the same file); high to defer — live, scope-bypassing mutation of financial filing state.

### H4: Middleware/route capability granularity mismatch
**Classification:** Confirmed defect
**Affected files:** `app/security/middleware.py:16-42,112-115`, `app/routes/workflows.py:69`, `app/routes/tax_returns.py:53`
**Affected endpoints:** `POST /api/v1/workflows/approvals/{approval_id}/decision` (realized today); `POST /api/v1/tax/returns/reviews/{review_id}/decision` and sibling tax-review routes (latent)
**Exploit scenario / failure:** The middleware `RULES` regex maps any non-GET `/api/v1/workflows*` request to require `work.write` (via the `.read`→`.write` rewrite for non-GET). The route itself requires only `work.approve`. Per `migrations/versions/e530f5b3d4e5_add_workflow_process_automation.py:37-38`, `work.approve` is granted to `administrator` and `compliance`; per `migrations/versions/d420f4a2c3d4_add_work_management_platform.py:86-88`, `compliance` is explicitly **not** granted `work.write`. **This means the `compliance` role — the only non-admin role designed to approve workflow work — is locked out by the middleware today, in the currently-shipped configuration**, before the route's own correct check is ever reached. The identical structural gap exists for `tax.review` vs. `tax.write`, but is latent today since no non-admin role currently holds any `tax.*` capability.
**Severity:** Critical for the workflow-approval half (an already-granted role cannot do its job in production today); High/latent for the tax-review half.
**Recommended fix:** Add explicit middleware carve-outs (`/api/v1/workflows/approvals/*/decision` → `work.approve`; `/api/v1/tax/returns/reviews/*` → `tax.review`), ordered before the generic prefix rules, as a minimal patch; medium-term, generate `RULES` from route-declared capabilities so they cannot drift, with a CI check.
**Estimated effort:** S (hours–1 day) for the minimal carve-out; M (2-5 days) for the generator/CI-test approach.
**Migration impact:** None.
**Backward compatibility impact:** None negative — this only loosens an over-broad, incorrect middleware check to match the already-shipped, more precise route-level check.
**Release risk:** Low to ship; high to defer — `compliance` cannot approve workflow steps in production today. This is a functional break, not a theoretical gap.

### H5: Relationship deactivation IDOR
**Classification:** Confirmed defect
**Affected files:** `app/routes/relationships.py:52-59`, `app/services/relationships.py:176-223`, `app/security/middleware.py:138-161`
**Affected endpoints:** `POST /relationships/{relationship_id}/deactivate`
**Exploit scenario:** The middleware does apply a compensating check for `/relationships/*` paths — but it validates `person_id` read from the **query string**, not the entity actually being mutated. `end_relationship(relationship_id, person_id)` never verifies `relationship_id` belongs to `person_id`. An advisor legitimately scoped to `person_id=100` sends `POST /relationships/555/deactivate?person_id=100` where relationship 555 actually belongs to an unrelated `person_id=999` — the middleware validates scope for the (legitimately-owned) query param, then the handler deactivates the unrelated relationship. The same middleware file demonstrates the *correct* pattern one section down for documents (resolving the owning `person_id` from the record itself) — this route doesn't follow it.
**Severity:** High.
**Recommended fix:** Resolve the relationship's actual owning `person_id` server-side (mirroring the document-match pattern) and check scope against that, not the client-supplied query parameter.
**Estimated effort:** S–M (part of a day) — small middleware addition plus a route-level defense-in-depth check, with a regression test.
**Migration impact:** None.
**Backward compatibility impact:** None expected — legitimate UI flows always supply the correct `person_id` from the profile page context.
**Release risk:** Low to fix; high to defer — a live, exploitable IDOR through an existing-but-flawed control, easy to mistake as already protected.

### H6: Firm-wide "available people" picker PII enumeration
**Classification:** Confirmed defect
**Affected files:** `app/routes/people.py:335-339`, `app/routes/households.py:147-167`, rendered into `app/templates/people/workspace.html:173-177` and `app/templates/households/profile.html:132-136`
**Affected endpoints:** `GET /people/{person_id}`, `GET /households/{household_id}` (HTML)
**Exploit scenario:** Both handlers query every active person firm-wide (households.py variant also has no `active` filter) with no `record_assignments` scope, and this data is confirmed rendered into visible page HTML — `households/profile.html` additionally exposes `primary_email` as visible option text. Neither `RECORD_PATH` nor `FIRM_WIDE_COLLECTION` middleware regexes gate this in-page picker query (they gate the record itself, not this embedded query). Any advisor scoped to view even one client profile receives, embedded in that page's HTML, the full name (and for households, email) of every active person in the firm — trivially scraped via view-source.
**Severity:** High (households.py variant more severe — leaks email addresses directly).
**Recommended fix:** Scope both queries to the caller's authorized record set; replace the full-firm dropdown with a scoped, paginated/type-ahead search widget.
**Estimated effort:** M (2-5 days) — requires threading `principal` through both handlers and reworking the picker UI.
**Migration impact:** None required; reuses existing `record_assignments` infrastructure.
**Backward compatibility impact:** Advisors without `record.read_all` stop seeing clients outside their book in this dropdown — the intended fix, not a regression; any legitimate cross-book linking workflow needs an explicit, separately-authorized search path.
**Release risk:** Low to fix; high to defer — passive, no-action-required PII enumeration reachable by any advisor with a single client assignment.

### H7: Portal messages permission not enforced on read/send/mark-read
**Classification:** Confirmed defect (not currently reachable over HTTP)
**Affected files:** `app/portal/service.py:113-124,137-142,144-152` (compare `:103-104` `create_thread`, which correctly checks `permission="messages"`)
**Affected endpoints:** `GET/POST /api/v1/portal/messages/{thread_id}`, `POST /api/v1/portal/messages/{message_id}/read`
**Exploit scenario:** A portal account with an active grant where `permissions={"messages": False, ...}` cannot create a thread but **can** read/send/mark-read on any existing thread within household/person scope, since those three functions check only household/person membership, never the `messages` permission key. **Reachability caveat:** `invite_portal_account` — the only writer of `portal_access_grants` — is called from no route or admin tool in the current codebase (only from test fixtures), so there is currently no live HTTP path for staff to create a `messages:false` grant. The DB column's own `server_default='{}'` (empty dict = all permissions false) means this fires the instant any invite/permission-editing UI is wired up, which the data model clearly anticipates. A related, distinct gap: `require_scope`'s permission check passes if **any** grant on the account has the permission, not specifically the grant covering the household/person in question.
**Severity:** High — objectively provable, inconsistent with its own sibling function, touches secure financial/tax correspondence; not currently live-exploitable.
**Recommended fix:** Mirror `create_thread`'s `require_scope(..., permission="messages")` call in all three functions; also correlate the permission check to the specific grant covering the target household/person, not any grant on the account.
**Estimated effort:** S (hours) for the primary fix; +~1 day to also fix the cross-grant leakage.
**Migration impact:** None.
**Backward compatibility impact:** None — no live grant today has `messages: False`.
**Release risk:** Low to ship now (cheap, additive); should specifically gate any future invite/permission-management UI work.

### H8: Work assignment reassign/deactivate IDOR
**Classification:** Confirmed defect
**Affected files:** `app/routes/work.py:121-146`, `app/services/work_management.py:88-121`
**Affected endpoints:** `POST /api/v1/work/assignments/{assignment_id}/reassign`, `DELETE /api/v1/work/assignments/{assignment_id}`
**Exploit scenario:** Both routes depend only on `work.write`. Neither `reassign_work` nor `deactivate_assignment` takes a `principal` parameter — the assignment row is loaded purely by `assignment_id` with no check that the caller is the assignee, on their team, or otherwise related to the entity. The middleware's `RECORD_PATH` regex doesn't match assignment IDs, so no compensating check fires. Any `advisor`/`operations` holder (broad grant per seed data) can reassign or deactivate any colleague's assignment firm-wide — an IDOR that doubles as an internal sabotage/denial-of-service vector against colleagues' books of business.
**Severity:** High.
**Recommended fix:** Thread `principal` into both functions; require `assignment.manage`, or that the caller matches the existing assignee/team, or holds `record.write_all`. Naturally fixed alongside H1's unified authorization refactor.
**Estimated effort:** S (hours–1 day) — same shape of fix as H1, can be done in the same PR.
**Migration impact:** None.
**Backward compatibility impact:** Reassigning outside the caller's own team would newly require `assignment.manage` — already held by `operations`, so likely no regression.
**Release risk:** Low to ship; moderate to defer.

### H9: `process_reminders()` has no office/assignment scope filter
**Classification:** Confirmed defect (currently zero blast radius)
**Affected files:** `app/services/tax_intake.py:184-205`, `app/routes/tax_intake.py:64`
**Affected endpoints:** `POST /api/v1/tax/intake/reminders`
**Exploit scenario:** Queries `tax_missing_items`/`tax_questionnaires` firm-wide with no `tax_office_memberships`/`record_assignments` filter, unlike every other tax read/write path (`_scope_filter()`). **Caveat confirmed via migration grants:** `tax.intake.write` is seeded only to `administrator` today (`migrations/versions/h860c8e6a7b8_add_tax_engagement_intake.py:41`), so no office-scoped role can call this yet — but the entire `tax_offices`/`tax_office_memberships` schema exists specifically to support office-scoped staff, and `tax.intake.write` is clearly the capability meant for office-level preparers. The moment that delegation happens, any office-scoped holder can trigger firm-wide client-facing notifications for clients outside their office.
**Severity:** High.
**Recommended fix:** Reuse `_scope_filter()`'s join pattern to scope the reminder query when the caller isn't `record.read_all`; if intended as a firm-wide cron job, gate the HTTP trigger behind a dedicated admin-level capability instead.
**Estimated effort:** S-M (1-2 days).
**Migration impact:** None — reuses existing tables.
**Backward compatibility impact:** None today.
**Release risk:** Low to ship now while cheap; should specifically gate any future delegation of `tax.intake.write` to office-scoped roles.

### H10: Microsoft OAuth tokens plaintext + no refresh
**Classification:** Confirmed defect
**Affected files:** `app/database/schema.py:660-661`, `app/routes/microsoft365_oauth.py:189-201`, `app/jobs/microsoft_{mail,calendar,document}_sync.py`, `app/jobs/scheduler.py:17-38`
**Affected endpoints:** `GET /microsoft365/connect`, `GET /microsoft365/callback`; background jobs `microsoft-mail-sync` (15 min), `microsoft-calendar-sync` (15 min), `microsoft-document-sync` (30 min)
**Exploit scenario:** `access_token`/`refresh_token` are plain `Text` columns with zero application-level encryption anywhere in the codebase (`cryptography` is already an unused transitive dependency). `refresh_token=None` is hardcoded on every save, and the upsert's conflict clause never updates it either — so no refresh token is ever captured or usable. Any DB read access (backup leak, insider access) yields a live bearer token with broad scopes (`Mail.Send`, `Calendars.ReadWrite`, `Contacts.ReadWrite`, `Files.ReadWrite`, `Sites.Read.All`). Separately, ~60-90 minutes after any connection, all three scheduled jobs begin failing every cycle silently (caught by a blanket `except Exception: logger.exception(...)` with no alerting, and `/microsoft365/status` only reports config presence, not sync health).
**Severity:** High — wealth-management/tax context (mailbox/calendar/SharePoint plausibly contain SSNs, account numbers, tax documents); zero-effort plaintext exposure.
**Recommended fix:** Encrypt tokens at rest (application-level Fernet with a KMS-backed key, or persist an MSAL `SerializableTokenCache`); implement real refresh via `acquire_token_silent`; fix the upsert to actually persist refresh material; add sync-health status surfaced on `/microsoft365/status`.
**Estimated effort:** M (2-5 days), could stretch to L if a KMS/key-rotation strategy must be built from scratch.
**Migration impact:** Yes — new columns for token-cache blob and/or sync-status tracking; existing plaintext `access_token` can be wrapped in place, but `refresh_token` is already null for 100% of rows (nothing to migrate there).
**Backward compatibility impact:** Every existing connected account will need to fully reconnect once the fix ships — but this is not a new regression, since every account is already unrecoverable ~60-90 minutes post-connect under current behavior; the fix converts a recurring hourly failure into a one-time reconnect event.
**Release risk:** Moderate to ship (needs careful MSAL testing, must preserve graceful degradation as fallback); high and compounding to defer.

---

## Detailed Verification — Correctness / Data Integrity (H11–H14)

### H11: Dead "Unassigned" tax dashboard metric
**Classification:** Confirmed defect — **severity revised down to Medium**
**Affected files:** `migrations/versions/g750b7d5f6a7_add_tax_domain_foundation.py:32`, `migrations/versions/i970d9f7b8c9_add_tax_return_lifecycle.py:26` (backfill only, no `ALTER COLUMN ... SET DEFAULT`), `app/services/tax_domain.py:72,92`, `app/services/tax_return_lifecycle.py:14`
**Affected endpoints:** `GET /tax`, `GET /api/v1/tax/dashboard`, `GET /api/v1/tax/engagements`
**Finding:** Confirmed — the only write path into `tax_engagement_returns` (`create_engagement()`) always explicitly sets `status="received"`; combined with the one-time backfill, no row can currently have `status='not_started'`, so the "unassigned" metric is permanently 0. The DB-level `server_default` is still the orphaned `'not_started'` value, a state `transition_return()` can never move a row out of without `force=True`. **Downgrade rationale:** this is a silently-misleading operational metric and a latent schema-drift trap for future manual/bulk writers, not an active data-corruption or security issue for any current user flow.
**Severity:** Medium.
**Recommended fix:** Rewrite the metric to mean "no active `record_assignments` row" rather than a stale status value; add a migration to align the DB default with actual application behavior.
**Estimated effort:** S (hours–1 day).
**Migration impact:** Yes, small — one `ALTER COLUMN` migration.
**Backward compatibility impact:** Dashboard number changes from a hardcoded 0 to a real count — a correction.
**Release risk:** Low to fix; ongoing operational blind spot to defer.

### H12: Household-context drift between intake and lifecycle
**Classification:** False positive
**Explanation:** The code-level discrepancy is real — `tax_intake.py`'s `_return_context` coalesces `household_id` from the person's own household when the engagement's is null; `tax_return_lifecycle.py`'s `_context` does not. `household_id` is indeed nullable and commonly omitted (`EngagementCreate.household_id: Optional[int] = None`). However, exhaustive tracing of every `require_scope()` call site shows `household_id` is never the sole authorization argument — `person_id` is always passed alongside it, and `require_scope()` treats a `None` household as a no-op check, never as more restrictive. More decisively, `portal_scope()` — the function that actually determines whether a joint/shared-household portal account can see a return — resolves household membership from **the person's own row** (`people.c.household_id`), completely independent of whatever is stored on the tax engagement record. Both `portal_returns()` and `portal_intakes()` (the two subsystems' respective portal-listing queries) also both use the raw, uncoalesced `tax_engagements.c.household_id` — i.e., even that layer is already consistent between the two subsystems. The coalesce difference is real code duplication/drift worth cleaning up for hygiene and to prevent a *future* bug (a hypothetical future call site using `household_id` as the sole gate would behave inconsistently), but it produces no observable difference in access-control outcomes in the code that exists today.
**Recommended follow-up (not a defect fix, a hygiene item):** Consolidate the two context-loaders into one shared, correctly-coalescing function as originally recommended in RC8's refactor list — worth doing to prevent future drift, just not an active bug today.

### H13: Document auto-matching substring containment
**Classification:** Confirmed defect
**Affected files:** `app/jobs/microsoft_document_sync.py:79-98,230-291`, `app/routes/microsoft365_documents.py:26-51`, `app/services/microsoft_documents.py:6-20`
**Affected endpoints:** `POST /microsoft365/documents/sync` (background); document display via `get_person_microsoft_documents`
**Exploit scenario:** `match_drive_item` uses unbounded substring containment (`email in search_text`, `full_name in normalized_parent`), and `store_microsoft_document` writes `status="matched"` directly on any single-candidate match with no intermediate confidence gate — the human-review queue only surfaces `status=="pending"` (unmatched) rows, so a spurious single-candidate match bypasses review entirely. Concrete demonstrated collision: client "Ed Munson" produces a normalized name `"ed munson"`, which is a contiguous substring of an unrelated folder path containing `"fred munson family trust"` (`"...fr` + `ed munson...`) — `match_drive_item` returns exactly one candidate with no ambiguity, and the document is auto-assigned to the wrong client with zero human confirmation.
**Severity:** High — confidentiality/compliance exposure of client financial documents in a wealth-management context, with a demonstrated reproducible collision.
**Recommended fix:** Require word/token-boundary matching; downgrade weak-signal (`folder_name`-only, no email corroboration) matches to `status="pending"` for mandatory human review rather than auto-committing.
**Estimated effort:** M (2-5 days) — matching-logic rewrite, regression tests, plus a one-time backfill/audit of historically auto-matched `folder_name` documents.
**Migration impact:** No schema migration for the logic fix; a data-quality backfill pass (re-flagging existing matches for review) is recommended.
**Backward compatibility impact:** Stricter matching moves some previously auto-matched documents back to the review queue — an intended, more conservative behavior change; increases staff review workload.
**Release risk:** Low to ship (worst case is more manual review, not wrong assignment); ongoing cross-client document exposure risk to defer.

### H14: Dead "pending matches" dashboard metric
**Classification:** Confirmed defect — **severity revised down to Medium**
**Affected files:** `app/services/dashboard.py:44-48`, `app/routes/matches.py:307-320,381-399,552-555,586-589`, `migrations/versions/53802af14074_add_match_review_decisions.py:28`
**Affected endpoints:** main dashboard (`get_dashboard_data()`)
**Finding:** Confirmed — the only writer to `match_review_decisions` explicitly disallows `decision="pending"` (rejects with HTTP 400), and the column is `NOT NULL` with no default, so a `"pending"` row can never exist. The dashboard's `pending_matches` count is therefore structurally always 0, while `/matches` itself computes the real backlog correctly in-memory (never persisted). Purely a visibility bug, not a correctness/security issue.
**Severity:** Medium.
**Recommended fix:** Rewrite the dashboard metric to reuse the same in-memory computation already used by `/matches` rather than querying a value that can never exist in the table.
**Estimated effort:** S (hours–1 day).
**Migration impact:** None.
**Backward compatibility impact:** Dashboard tile changes from hardcoded 0 to a real, nonzero-capable count — a correction.
**Release risk:** Low to fix; leaves an operationally relevant backlog metric permanently blind if deferred.

---

## Detailed Verification — Scalability (H15–H19)

### H15: `work_items()` unfiltered dual full-table scan
**Classification:** Confirmed defect
**Affected files:** `app/services/work_management.py:146-169,172-174,213-217`
**Affected endpoints:** 9 routes in `app/routes/work.py` (`GET /work`, `/work/team`, `/work/queues/{code}`, and 6 API equivalents) all route through `dashboard()`/`work_items()`/`queue_detail()`
**Finding:** Confirmed precisely — `select(tasks)` and the `workflow_steps` JOIN `workflow_instances` both execute with no `WHERE` clause, pulling every row into Python before filtering by authorization/priority/status/due-date via set intersections. Every staff member's daily landing page is O(total firm work), not O(caller's book).
**Severity:** High — the highest-traffic dashboard in the app.
**Recommended fix:** Push the authorization predicate into SQL (`EXISTS` against `record_assignments`) and push priority/status/team/due-date filters into the WHERE clause instead of post-filtering in Python. `production_dashboard()` (tax return lifecycle) already demonstrates the desired pattern in this codebase.
**Estimated effort:** M (2-5 days) — the SQL rewrite must exactly replicate today's authorization semantics; needs strong test coverage to avoid an authz regression during the rewrite.
**Migration impact:** No schema change strictly required, but benefits from indexes on `tasks.person_id`/`household_id`, `workflow_steps.workflow_instance_id`, `record_assignments(entity_type, entity_id)` (see H20).
**Backward compatibility impact:** Low if the SQL predicate is proven equivalent — return shape is unchanged.
**Release risk:** Moderate to ship (touches authorization-adjacent logic — needs careful testing); performance-only degradation to defer, but the one most likely to surface as a user complaint soonest.

### H16: Tax intake `staff_dashboard()`/`api_detail()` N+1
**Classification:** Confirmed defect
**Affected files:** `app/services/tax_intake.py:145-165`, `app/routes/tax_intake.py:36-39`
**Affected endpoints:** `GET /tax/intake`, `GET /api/v1/tax/intake` (1+6N to 1+7N queries for N returns), `GET /api/v1/tax/intake/{return_id}` (pays the full dashboard cost AND a fresh detail call — roughly 1+7N to 1+8N queries for a single-record fetch)
**Finding:** Confirmed with precise query count — `intake_detail()` issues 6 unconditional + 1 conditional query per call, called in an unbatched Python loop once per authorized return. For a tax season with ~200 in-flight returns, a single `api_detail()` call triggers on the order of 1,200-1,600+ SELECT statements.
**Severity:** High — will be most acutely felt exactly when N is largest (tax season) and the endpoint is used most.
**Recommended fix:** Bulk-load via `WHERE ... IN (return_ids)` queries following the pattern `production_dashboard()` already uses; fix `api_detail()` to authorize via a cheap existence check instead of the full dashboard.
**Estimated effort:** M (2-5 days).
**Migration impact:** No new migration strictly required; `tax_missing_items` would benefit from an index on `tax_engagement_return_id` (see H20).
**Backward compatibility impact:** Low — return shapes unchanged if the bulk rewrite assembles identical per-item structure.
**Release risk:** Low-moderate to ship (mechanical, testable by output diffing); high risk of a real production incident during tax season specifically if deferred.

### H17: Portal `dashboard()` fan-out N+1, `portal_scope()` recomputed 4×
**Classification:** Confirmed defect
**Affected files:** `app/portal/service.py:189-193,210-223`, `app/services/tax_intake.py:167-172`, `app/services/tax_return_lifecycle.py:159-162`, `app/routes/portal.py:90-93,106-107,112-113`
**Affected endpoints:** `GET /portal/{page}`, `GET /api/v1/portal/dashboard`, `GET /api/v1/portal/documents`, `/requests`, `/tasks`, `/notifications`, `/messages`
**Finding:** Confirmed exactly 4 independent `portal_scope()` executions per `dashboard()` call. Confirmed `portal_intakes()`/`portal_returns()` call their respective per-item detail loaders (6-7 and 5 queries each) in an uncached loop. Confirmed the 4 narrow endpoints call `dashboard()` wholesale and discard everything but one key — for a client with 5 returns and 3 intakes, a call to the notifications endpoint alone (which needs one 20-row SELECT) triggers 55-60+ queries.
**Severity:** High — the worst amplification factor of the five scalability claims, and it's the client-facing (external) surface.
**Recommended fix:** Thread a single computed `portal_scope()` through `dashboard()` and its sub-calls instead of recomputing; give the narrow endpoints dedicated single-purpose queries instead of calling `dashboard()` wholesale; fix the underlying N+1 in `portal_intakes()`/`portal_returns()` via bulk queries (same pattern as H16).
**Estimated effort:** M (2-5 days) for the scope-threading fix alone; L if combined with the bulk-query rewrite and narrow-endpoint split as one coherent refactor.
**Migration impact:** None for scope-threading; benefits from the same indexes as H16 for the bulk rewrite.
**Backward compatibility impact:** Low if narrow-endpoint extraction returns identical fields/ordering to what `dashboard()` currently slices out.
**Release risk:** Low-moderate to ship; highest-amplification issue of the five to defer — narrow endpoints that look cheap from the URL will be the first to time out as the portal's real client usage grows.

### H18: Unbounded firm-wide list endpoints
**Classification:** Confirmed defect
**Affected files:** `app/routes/activity_dashboard.py:12-36`, `app/routes/task_dashboard.py:11-36`, `app/services/relationships.py:371-444`, `app/services/portfolio.py:37-47`
**Affected endpoints:** `GET /activities`, `GET /tasks` (both take zero query params — no way to even request a bounded slice today), `search_relationships()`, `search_portfolios()`
**Finding:** Confirmed — no `.where()`/`.limit()` in any of the four, and the two HTML dashboard routes accept no query parameters at all to opt into pagination even if a caller wanted to.
**Severity:** High for `/activities`/`/tasks` (described as the two most-visited daily pages, HTML-rendered so the full row set is also template-rendered); Medium for the two search functions (lower-frequency, but unbounded when unfiltered).
**Recommended fix:** Add `limit`/`offset` query params with sane defaults (100-200 rows) to all four; consider a shared pagination dependency/helper rather than fixing ad hoc.
**Estimated effort:** M (2-5 days) — mechanically simple per endpoint, but needs coordinated updates to any client-side/template logic that assumes "all rows."
**Migration impact:** None for LIMIT/OFFSET itself; may benefit from a covering index on the `ORDER BY` columns at scale.
**Backward compatibility impact:** Real — any current caller relying on receiving the complete unbounded dataset in one response will see fewer rows by default; templates and count displays need to be updated in the same change.
**Release risk:** Low to ship if defaults are conservative; the two most-visited pages make this the claim most likely to produce a visible, reproducible slow-page complaint as data accumulates.

### H19: Portfolio concentration search N+1
**Classification:** Confirmed defect — **severity revised up (actual per-row cost worse than originally estimated)**
**Affected files:** `app/services/portfolio.py:9-25,37-47`
**Affected endpoints:** callers of `search_portfolios(..., concentration=...)`
**Finding:** Confirmed and the original per-row query-count estimate (2-4) was too low — `get_person_portfolio()` calls `_portfolio()` **twice** per row (once for the person, once again recomputing the household's full portfolio from scratch), and `_portfolio()` itself issues 1-3 queries depending on whether the person has any holdings. Actual worst case is **7 queries per row**, not 2-4. Combined with H18 (no LIMIT on the same search), a firm-wide concentration search over ~800 people-with-accounts can trigger up to ~5,600 sequential DB round trips for one request.
**Severity:** High (upgraded from RC8's implicit framing).
**Recommended fix:** Compute the concentration ratio via SQL directly (window function or joined subquery against `account_holdings`/`securities`) applied as a `HAVING` clause, eliminating the per-row Python loop entirely; combine with the H18 LIMIT fix.
**Estimated effort:** M (2-5 days) — must carefully match the existing Python `aggregate_portfolio()` semantics for "largest position percent" to avoid a silent numeric mismatch.
**Migration impact:** None strictly required; may benefit from indexes on `account_holdings.account_id`/`security_id`.
**Backward compatibility impact:** Low for response shape; real risk of a subtly wrong search result if the SQL-computed percentage doesn't exactly match the current Python computation — needs careful cross-validation, especially given this type of search likely has compliance/business consequences if wrong.
**Release risk:** Moderate to ship (needs correctness validation against the existing calculation); this claim's true cost was understated in RC8, so it will hit a wall sooner than the other four as client/account count grows.

---

## Detailed Verification — Database Schema (H20–H22)

### H20: ~48 missing indexes on FK/hot-filter columns
**Classification:** Confirmed defect
**Affected files/tables (11 highest-impact examples independently re-verified, all with zero indexing support):** `people.household_id`, `tasks.person_id`, `activities.person_id`, `documents.person_id`, `timeline_events.person_id`/`household_id`, `household_relationships.person_id` (partial — the composite unique constraint's leading column is `household_id`, so `person_id`-only lookups get zero benefit), `portal_notifications.portal_account_id`, `portal_threads.household_id`/`person_id`, `tax_engagements.person_id`/`household_id`, `tax_missing_items.tax_engagement_return_id`, `audit_events.actor_user_id`/`(entity_type,entity_id)`.
**Affected endpoints:** N/A (schema-level) — all 11 examples confirmed used in live equality-filter queries in hot-path code (notably `app/services/client_summary.py`, the per-client "360 view" aggregator hit on every client detail page load, filters `tasks`/`activities`/`documents`/`timeline_events` by `person_id` with zero index support on any of them).
**Finding:** No hidden covering index was found for any of the 11 sampled examples — RC8's methodology holds up on the sampled subset. `audit_events` is the one partial exception: no current code path filters it by `actor_user_id`/`entity_type`+`entity_id` (today's only query is a bare `ORDER BY occurred_at DESC LIMIT 500`), so that specific gap is a forward-looking risk for an obviously-needed future "search audit log" feature rather than an active bottleneck today. The remaining ~37 items in RC8's full count were not individually re-verified in this pass (out of scope), but the sampled accuracy supports treating the full list as reliable.
**Severity:** High (Critical for `tasks`/`activities`/`documents`/`timeline_events` given confirmed hot-path usage; Medium for `audit_events`).
**Recommended fix:** Add single-column btree indexes on each confirmed column.
**Estimated effort:** M (2-5 days) — the DDL is trivial, but must use `CREATE INDEX CONCURRENTLY` (via `op.get_context().autocommit_block()`, since Alembic's default transactional block doesn't support it) to avoid locking production tables, plus staging validation that the planner actually selects each new index.
**Migration impact:** New migration(s) required; must be built `CONCURRENTLY` and may need splitting into multiple small migrations to bound lock/connection-pool risk.
**Backward compatibility impact:** None — additive indexes change performance only, not query results.
**Release risk:** Low to ship; risk of deferring only grows as tables grow and `CONCURRENTLY` builds get slower on larger data.

### H21: ~16 missing constraints
**Classification:** Confirmed defect, **with an important correction to RC8's framing**
**Affected files:** `app/database/schema.py:239` (`tasks.status`), `:360` (`documents.review_status`), `app/database/work_tables.py:15` (`workflow_instances.status`), `app/database/identity_tables.py:31` (`record_assignments.entity_type`), `:40` (`audit_events.entity_type`), plus 5 more polymorphic `entity_type` columns (`assignment_events`, `assignment_rules`, `automation_triggers`, `relationship_entities`, `portal_notifications`)
**Correction:** RC8's framing implied the tax-domain migrations (`g750b7d5f6a7`, `h860c8e6a7b8`, `i970d9f7b8c9`) collectively prove a known, provable CHECK-constraint fix pattern for enum-like columns in this codebase. Verification found this is **only true for `g750b7d5f6a7`** (2 CheckConstraints, neither an enum check — one is a year-range check, one is an XOR-style subject check). Across all 19 migrations, exactly 7-8 CheckConstraints exist total, and **zero of them validate a status/type enum against a fixed vocabulary** — all are self-reference guards, segregation-of-duty checks, or "exactly one of two nullable FKs" checks. Notably, `h860c8e6a7b8` and `i970d9f7b8c9` — the latter being this branch's own migration — added numerous new free-text status/type columns (`tax_engagement_returns.filing_status`, `tax_return_reviews.status`/`review_type`, `tax_client_approvals.status`/`approval_type`, `tax_filing_events.filing_status`, etc.) with **zero** CheckConstraints, despite the opportunity. There is no existing precedent anywhere in this codebase for the specific fix (enum-CHECK on a status column) RC8 implies is already proven.
**Exploit scenario:** Any application bug, typo, direct SQL session, or future migration can silently write an invalid status/entity_type value that never surfaces as a DB error — only as a silent UI/logic bug (record invisible to status-filtered queries, or a polymorphic join to the wrong `entity_type` silently returning zero rows).
**Severity:** Medium — latent data-integrity risk, not an active availability/security issue; the tax lifecycle's own new tables (money-adjacent workflow state) have zero DB-level guardrail.
**Recommended fix:** Introduce lookup tables for the highest-traffic enums (mirroring `tax_filing_statuses`/`tax_return_types`, which already exist elsewhere in this schema), and add `CHECK (entity_type IN (...))` on the polymorphic columns as a lower-effort interim step.
**Estimated effort:** L (1-2+ weeks) — no existing precedent to copy from; requires a data audit of existing values before any `ADD CONSTRAINT` (to avoid failing against dirty data), and lookup-table conversion touches every raw string-comparison call site.
**Migration impact:** Requires a pre-migration data-audit step; use `op.create_check_constraint(..., not_valid=True)` followed by a separate `VALIDATE CONSTRAINT` to avoid a full-table lock on large tables (not automatic in Alembic — needs raw `op.execute`).
**Backward compatibility impact:** Real risk — any current write path or historical data violating the proposed CHECK will start failing; must be audited first.
**Release risk:** Low to defer (constraints are a safety net, not a functional gap); real risk to rush without first auditing for dirty data.

### H22: `app/db.py`/`app/database/schema.py` split
**Classification:** Confirmed defect, **with materially more precise numbers than RC8's estimate**
**Affected files:** `app/db.py:14,17,19-127`, `app/database/schema.py:32` + 22 `Table()` defs, `identity_tables.py` (10), `work_tables.py` (7), `portfolio_tables.py` (13, including 3 built in a loop), `app/routes/matches.py:11,16` → `app/services/person_merge.py:6` → `app.database.schema`
**Precise recount:** `app/db.py` exposes 103 named tables via live reflection (plus 2 more reflected-but-unexported); across all 4 schema-definition files there are exactly **52** `Table()` objects total (not an ambiguous "subset"); ground truth across all 19 migrations shows **105 distinct tables ever created**. **53 of 105 tables (50.5%) have zero Python model anywhere** — confirming RC8's "roughly half" claim almost exactly. **Materially more important correction:** even for the 52 tables that *do* have a Python model, the running application (everything except `person_merge.py`) never actually imports or queries through those `Table()` objects — every route/service in the app queries through `app.db`'s independently-reflected copies. So in practice ~100% of runtime queries go through reflection, and the Python models are read only by 3 of the 19 migrations (as DDL source) and by exactly one lone consumer, `person_merge.py` (for 3 tables only: `people`, `person_source_links`, `source_contacts`) — a second, hand-maintained shadow model for those 3 tables with no test or CI enforcing it stays in sync with the real migration-driven DDL.
**Affected endpoints:** N/A (schema-level) — but the "dual connection pool" concern is **confirmed as actually occurring today, not theoretical**: `app/routes/matches.py` is live-mounted in `app/main.py` (`app.include_router(matches_router)`), and its import chain (`matches.py` → `person_merge.py` → `app.database.schema`) opens a second `create_engine(DATABASE_URL)` in the same process as `app.db`'s engine, at process startup.
**Exploit scenario:** (1) App boot fails with a raw `SQLAlchemy` error inside `app/db.py` if Postgres is unreachable or unmigrated at import time, before FastAPI's own lifespan/health-check machinery runs — no graceful degradation. (2) Drift risk: `schema.py`'s hand-typed Table defs are duplicates of baseline-migration DDL, not generated from it — a future raw `op.add_column` migration would silently not appear in `schema.py`'s version of that table, meaning `person_merge.py`'s 3-table shadow model could silently diverge from reality. (3) Confirmed dual connection pools, doubling baseline connection footprint for no functional benefit.
**Severity:** Medium — architecturally messy and a genuine startup-robustness/maintainability risk, but not a data-correctness or availability issue in steady-state operation.
**Recommended fix:** Standardize on one model. Given 53/105 tables already lack any model and reflection already works reliably, the lower-risk path is to fully commit to reflection: convert the 3 migrations that call `metadata.tables[name].create()` to raw `op.create_table()` DDL, and route `person_merge.py` through `app.db` like every other consumer, eliminating the second schema-definition system entirely.
**Estimated effort:** S (a few hours) for the narrow fix (`person_merge.py` import swap alone — eliminates the confirmed dual-pool issue immediately with no other behavior change); L (1-2+ weeks) for full consolidation (rewriting 3 already-applied migrations' DDL source and deleting/merging 4 files).
**Migration impact:** Narrow fix: none. Full consolidation: rewrites the *upgrade()* bodies of 3 already-applied migrations to use raw DDL instead of Python-model-derived `create()` calls — safe only because Alembic tracks applied state by revision id rather than re-diffing DDL, but requires care to keep `downgrade()` paths consistent.
**Backward compatibility impact:** Narrow fix: none. Full consolidation: internal refactor only, no user-facing change, but real regression risk if any subtle mismatch (nullable, server_default, FK ondelete) exists between `schema.py`'s hand-written definitions and the actual migration DDL for the 22 directly-defined tables — must be diffed column-by-column before deletion.
**Release risk:** Low to ship the narrow S-effort fix immediately. The L-effort full consolidation should be a dedicated cleanup sprint, not bundled into near-term work, since it touches already-applied migration history with no existing test coverage for schema-definition equivalence.

---

## Prioritized Remediation Roadmap

### Release 0.9.7 — Security Hardening (DELIVERED — branch `feature/security-hardening-0.9.7`)
*All twelve items below were implemented, tested, and validated in Release 0.9.7. See [Security Hardening 0.9.7](SECURITY_HARDENING_0.9.7.md). Status legend: **Fixed** = implemented and covered by a passing regression test.*

| Item | Fix | Effort | Status |
|---|---|---|---|
| H1 | Require `assignment.manage` + record scope for person/household grants; separate from `work.write` | S | **Fixed** |
| H4 | Middleware carve-outs for `work.approve` and `tax.review` paths (unblocks `compliance`) | S | **Fixed** |
| H3 | Add canonical `_authorized` scope check to `api_review_decision`/`api_resolve` | S | **Fixed** |
| H8 | Add ownership check to work-assignment reassign/deactivate/automatic | S | **Fixed** |
| H5 | Fix relationship-deactivation IDOR (resolve owner server-side) | S–M | **Fixed** |
| H7 | Enforce `messages` grant on portal message read/send/mark-read (default deny) | S | **Fixed** |
| H9 | Require firm-wide `record.read_all` for the manual `process_reminders()` trigger | S–M | **Fixed** |
| H2 | Ceiling/subset check on role composition and assignment; protect administrator role | M | **Fixed** |
| H6 | Scope the "available people" picker queries via `accessible_person_ids` | M | **Fixed** |
| H11 | Rewrite "Unassigned" metric + migration `j0a81f9c8d7e` aligns the `status` default | S | **Fixed** |
| H14 | Rewrite "pending matches" metric via `count_pending_match_groups()` | S | **Fixed** |
| H22 (narrow) | Route `person_merge.py` through `app.db` (eliminates dual connection pool) | S | **Fixed** |

**Not deferred, not N/A:** all twelve scoped items were fixed. H12 (verified a
false positive in this document) was intentionally excluded and remains a
Release 1.0 hygiene item. The broader three-way authorization-model
consolidation (H1/H3/H5/H6/H8 shared root cause) remains a Release 1.0 item as
originally scheduled; Release 0.9.7 introduced the canonical
`app/security/authorization.py` service and routed the affected endpoints
through it without removing the tax office-scope model.

### Release 0.9.8 — Correctness, Security-Depth & Performance Hardening
*Target: ~3–4 weeks. Confirmed defects that need more careful testing, a schema migration, or larger rewrites than the 0.9.7 batch. Two of these (H10 plaintext OAuth tokens, H13 cross-client document mis-assignment) are live security/confidentiality issues, not performance items — they are placed here rather than in 0.9.7 purely because their fixes are M–L effort with migration and external-integration (MSAL) testing dependencies, not because they are lower severity. If the Microsoft 365 integration is actively relied on or the token store is exposed to backups/replicas, H10 should be pulled forward into 0.9.7.*

| Item | Fix | Effort |
|---|---|---|
| H10 | Encrypt Microsoft OAuth tokens at rest; implement token refresh; add sync-health status | M–L |
| H13 | Fix document-matching substring containment; backfill/audit existing auto-matches | M |
| H15 | Rewrite `work_items()` to filter via SQL, not post-hoc Python | M |
| H16 | Bulk-query rewrite of tax intake `staff_dashboard()`/`api_detail()` | M |
| H17 | Thread `portal_scope()` through `dashboard()`; split narrow portal endpoints into dedicated queries | M–L |
| H18 | Add pagination to `/activities`, `/tasks`, relationship/portfolio search | M |
| H19 | Rewrite portfolio concentration search to compute in SQL | M |
| H20 (batch 1) | Add the 11 highest-impact missing indexes via `CREATE INDEX CONCURRENTLY` | M |

### Release 1.0 — Structural Consolidation
*Target: ~4–6 weeks, can run partly in parallel with Sprint 5.4/5.5 feature work. Larger-scope items needed for a defensible GA posture but not individually urgent.*

| Item | Fix | Effort |
|---|---|---|
| H20 (remainder) | Complete the remaining ~37 missing-index items | M |
| H21 | Data-audit + CHECK constraints / lookup tables for highest-traffic status/type columns | L |
| H12 (hygiene) | Consolidate the two tax context-loaders into one shared, correctly-coalescing function (prevents a future real bug even though today's instance is a false positive) | M |
| Broader authorization consolidation | Unify the three divergent record-scope implementations (`has_record_scope`/`_scope_filter`/`authorized_assignments`) referenced across H1/H3/H5/H6/H8's root cause | M–L |

### Post-1.0 — Deferred Structural Work
*Larger refactors, appropriate as dedicated cleanup sprints once the above is stable.*

| Item | Fix | Effort |
|---|---|---|
| H22 (full) | Full consolidation of `app/db.py`/`app/database/schema.py` into one schema source of truth | L |
| Remaining RC8 Medium/Low items | API response-envelope standardization, dead-code removal (filing providers, portal signatures, MS365 connector duplicate), template-convention unification, JSONB migration, event-table partitioning strategy | L (multiple workstreams) |

---

## Recommendation: Security-Hardening Sprint Before Sprint 5.4

**Recommendation: run the Release 0.9.7 security-hardening scope as a dedicated sprint before beginning Sprint 5.4.**

Rationale:

1. **H1 is a Critical, live privilege-escalation vulnerability reachable today by the most common non-admin role (`advisor`) with no prior access required and trivial exploitation** (sequential, guessable entity IDs). This alone is a release blocker for any continued internal rollout, independent of Sprint 5.4.
2. **H4 is an already-broken production feature, not a theoretical risk** — the `compliance` role, which was specifically granted `work.approve` to perform independent approvals, is locked out by a middleware/route mismatch today. This is a functional regression currently in production, not a future risk.
3. **H3, H5, H6, H8 are all confirmed, live-exploitable IDOR/enumeration bugs reachable by already-granted roles** (`advisor`, `operations`, `tax.review`/`tax.write` holders) — not edge cases requiring future role delegation.
4. **The fix set is small and cheap.** Ten of the twelve 0.9.7 items are S-effort (hours to one day); the whole batch is estimated at 1.5–2 weeks for one engineer, not a multi-month diversion. This is a short, high-value, low-risk sprint, not a reason to indefinitely delay feature work.
5. **Starting Sprint 5.4 (tax document intelligence) first would add new surface area — new routes, new tables, new authorization checks — on top of an authorization layer with a proven-broken pattern (the middleware/route capability mismatch already caused one lockout and enabled multiple IDORs).** Every new tax-domain route added before H4's root cause is fixed inherits the same risk of silently mismatched capability requirements.
6. **None of the 0.9.7 items require schema changes with data-audit prerequisites** (unlike H21, correctly deferred to Release 1.0) — they are safe to ship quickly with standard code review and the existing test suite, extended with targeted regression tests for each fix.

After the 0.9.7 hardening sprint closes, Sprint 5.4 can proceed normally, with the Release 0.9.8 and 1.0 items (performance, token security, schema consolidation) continuing as parallel or immediately-following hardening work rather than blocking new feature development further.

---

*Verification conducted as RC9, prior to Sprint 5.4. No application code was modified as part of this review, and no fixes were implemented. This document should be treated as the authoritative, verified backlog superseding RC8's High Priority section — re-run the relevant verification passes after each remediation release to confirm closure.*
