# Changelog

All notable Client360 releases are documented here.

## [Unreleased]

### Added
- Index-assisted global search (pg_trgm GIN indexes) with results de-duplicated per canonical person.
- Timeline display styling for activity-note, communication, and client-update events.
- Development-only sign-in provider (`/dev-auth`, gated by `CLIENT360_DEV_AUTH`; impossible to enable in production) and authenticated Playwright browser E2E coverage across login, dashboard, people, households, search, notes, tasks, and communications.
- Staff-editable canonical contact/address fields on the client profile (audited + added to the timeline).
- Human-readable timestamps across the client surface via a shared Jinja `humandt` filter.
- Task-submission idempotency: a DB-backed `tasks.idempotency_key` (unique) + hidden form token make a resubmitted create-task form a conflict-safe no-op (no duplicate task on browser back/resubmit or retried POST).
- Optional inbound/outbound direction on logged communications (call/email/meeting), captured in the Log form and shown in the activity feed.
- Match Review "unresolved contacts" queue (`/matches/unresolved`): single-source contacts that promotion leaves ambiguous (multiple candidate people, or a contact detail shared with another unlinked contact) are surfaced for a human to link to an existing client or create a new one. Human decision only — no automatic merge thresholds; every resolution is audited.
- Household detail roll-up: member count, aggregate household AUM, and open tasks across all members.
- `docs/RELEASE_READINESS.md` — a living release-readiness tracker maintained through Sprint 2.

### Changed
- Task/note assignee picker scoped to provisioned staff (active users holding an active role).

### Fixed
- Order-dependent event-loop test flakiness (global conftest fixture); a non-portable test path that failed CI.
- Single-source contacts were never promoted to canonical people: the Wealthbox import now runs `promote_unlinked` after ingest (same transaction), so imported single-source contacts become people (ambiguous cases left for Match Review) instead of being stranded.

## [0.11.0] — 2026-07-17 — Documentation Foundation

**Documentation-only release (Roadmap Phase A). No application code or database migrations
changed.** Establishes the Documentation Foundation & Governance layer for the 360 Wealth
Consulting Operations Manual. Signed off:
[`docs/releases/0.11.0/RELEASE_SIGNOFF.md`](docs/releases/0.11.0/RELEASE_SIGNOFF.md); RC-validated by
[`P5_RELEASE_CANDIDATE_VALIDATION.md`](docs/releases/0.11.0/P5_RELEASE_CANDIDATE_VALIDATION.md).

> ⚠️ **Foundation only — no substantive content.** Governance content authoring, legacy Atlas
> reconciliation execution, Confluence migration, advisory→blocking enforcement, and all regulated
> insurance rule sets remain **deferred**. **AD-5 is unresolved**; the accountable compliance
> reviewer is UNFILLED and regulated content stays blocked (`compliance_gate: AD-5 ⇒ never
> published`). Michael Shelton approved business/operational scope only — not regulatory certification.

### Added
- Framework ratification + architecture decisions **D1–D10** ([`P0_ARCHITECTURE_CHECKPOINT.md`](docs/releases/0.11.0/P0_ARCHITECTURE_CHECKPOINT.md)).
- **Confluence skeleton** — 8 Operations Manual nodes + 3 Area Shell template pages ([`P1_CONFLUENCE_SKELETON_REPORT.md`](docs/releases/0.11.0/P1_CONFLUENCE_SKELETON_REPORT.md)).
- **Git governance skeleton** — `governance/` tree (README, CONTRIBUTING, 6 directory READMEs), skeleton only ([`P2_GOVERNANCE_TREE_REPORT.md`](docs/releases/0.11.0/P2_GOVERNANCE_TREE_REPORT.md)).
- **Canonical Publication Register** — `docs/registers/pages.yml` (554 rows: 26 areas + `SHARED` + `GOV`, complete per-profile coverage, 27-type Hybrid union) with schema, generator, and validator (`scripts/registers/`).
- **Generated crosswalk** — `docs/DOCUMENTATION_CROSSWALK.md` is a deterministic generated view.
- **D10 taxonomy migration** — framework area-code taxonomy; legacy crosswalk letters preserved.
- **Legacy Atlas inventory** — 23 pre-existing pages recorded as non-canonical `manual_review` (none moved/edited).
- **Advisory documentation DoD** — `scripts/docs/check_documentation_dod.py`, `.github/pull_request_template.md`, and a non-blocking `documentation-advisory.yml` workflow ([`P4_DOD_GATE_REPORT.md`](docs/releases/0.11.0/P4_DOD_GATE_REPORT.md)).

## [0.10.0] — 2026-07-16 — Insurance Operations

**Release 0.10.0 contains the completed non-regulated Insurance Operations implementation
(Phases 0–9). AD-5-regulated functionality remains intentionally excluded pending compliance
review and approval.**
Individual **life insurance & annuities** (advisor-sold, in-force-managed) as a domain inside
Client360 — not group/employer benefits (0.9.11), not P&C. Built additively on the
0.9.11 platform and 0.9.13 test/CI/release infrastructure. RC-validated by
[RC-0.10.0](docs/RC_0.10.0_VALIDATION.md) (717 passed, 5 skipped, 0 failed) and approved by
[RELEASE_0.10.0_APPROVAL](docs/RELEASE_0.10.0_APPROVAL.md). Design of record:
[`docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md`](docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md).

> ⚠️ **Non-regulated skeletons only.** Phases 2–4 ship the operational/non-regulated
> plumbing only. All regulated logic — suitability determination, replacement/1035
> recommendation, licensing/CE **validation**, and any compliance approval or
> regulatory decision engine — is **deferred behind the AD-5 gate** and is not built
> or enabled. A qualified, named compliance reviewer plus an approved sign-off
> artifact is required before any regulated phase may proceed (see AD-5 below).

### Added — Phase 0 · Schema foundation (`v2b3d4f5a6c7`)
- Insurance schema foundation: product catalog (carrier profiles → product families
  → product versions), `insurance_case` coordinator (1:1 with an engagement),
  policy/party/producer tables.
- `insurance.*` capabilities and roles seeded; `insurance` registered in the shared
  Exception Engine (`SUPPORTED_DOMAINS` + CHECK) and Work Management (`work_items` domain).

### Added — Phase 1 · Policies core (`w3c4e5g6b7d8`, `x4d5f6h7c8e9`)
- Product-version evolution: carrier codes (NAIC) + rider compatibility as first-class,
  versioned data (not hard-coded).
- Policies core with coverages/riders/parties/values; multi-owner / multi-insured /
  multi-beneficiary support; policy CRUD JSON API and book/detail UI.
- Policy lifecycle statuses (issued, delivered, reinstated) and lifecycle events on the
  **shared Timeline/Audit** (no separate history model); name-resolved UI.

### Added — Phase 2 · New-business pipeline — non-regulated skeleton (`y5e6g7i8d9f0`)
- Application/case progression (case status transitions), requirement tracking
  (`insurance_requirements`: requested → satisfied — an operational checklist, **not** a
  determination), underwriting-**status** tracking (records the carrier's status; the
  platform does not decide it), document collection via the shared `documents` table,
  workflow-driven carrier-communication orchestration, Timeline/Audit events, operational
  pipeline reporting (counts only), case-workspace + pipeline UI, and JSON APIs.
- **Not built (AD-5-gated):** suitability determination, replacement/1035 recommendation
  logic, automated compliance approvals, any regulatory decision engine. A test asserts no
  such function exists in the service.

### Added — Phase 3 · In-force servicing — non-regulated skeleton (`z6f7h8j9e0g1`)
- Policy reviews as a first-class **state machine** (due → scheduled → in_progress →
  completed / deferred / overdue / cancelled); obligation calendar (annual reviews
  materialize their next occurrence on completion); a scheduled/manual scan flips past-due
  reviews to `overdue` and raises `INS_REVIEW_OVERDUE` through the **shared Exception
  Engine** (idempotent, auto-resolving); operational review metrics (completion rate,
  overdue/deferred counts); reviews-board UI + JSON APIs; Timeline/Audit review events.
- **Not built (AD-5-gated):** suitability determination (the `suitability` review type and
  `insurance.suitability` capability stay reserved), replacement/1035 recommendation logic,
  and any compliance/regulatory decision engine. Tests assert the scan result carries no
  compliance field. Live cron wiring of the scan is deferred to Phase 6; the callable +
  manual endpoint ship now.

### Added — Phase 4 · Producer licensing & CE — non-regulated skeleton (`a7g8i9k0f1h2`)
- Producer **licensing records** (`insurance_licenses`) and **CE records**
  (`insurance_ce_records`) — firm-internal, capability-gated
  (`insurance.licensing.read`/`.write`), audited, staff-entered; date-driven expiry
  reminders (`detect_licenses_expiring` / `detect_ce_period_ending` raise
  `INS_LICENSE_EXPIRING` / `INS_CE_PERIOD_ENDING` through the shared Exception Engine,
  firm-level/unanchored for oversight roles); operational licensing counts; licensing
  dashboard UI + JSON APIs.
- **Not built (AD-5-gated):** licensing **validation** (whether a producer may sell a
  product in a state), CE **satisfaction determination**, sale/issue **blocking** on
  licensing status, and any compliance/regulatory decision engine. Stored
  `credits_required` / `credits_completed` are staff-entered figures — the platform draws
  no conclusion from them. Tests assert no validation/determination function exists.

### Added — Phase 5 · Insurance commissions — expected/received ledger & reconciliation (`b8i9k1l2g3j4`)
- **Split-aware expected ledger.** `insurance_commissions` — one expected/received row per
  producer split; `generate_expected` fans a commission basis across a policy's active
  producers by `split_percentage` (an `override` role credits an upline entity), so a
  split-commission policy credits each producer correctly. `record_expected` captures a
  single entry. Schedules: `first_year | renewal | trail | override | other`.
- **Received posting & reconciliation.** `record_received` posts a payment and recomputes
  status (received / partial / variance within a one-cent tolerance). Carrier statements
  import (`insurance_commission_statements` + `_statement_lines`) and reconcile against
  expected rows (`reconcile_line` auto-matches by policy + schedule; `reconcile_statement`
  rolls the whole statement up) — where variance surfaces.
- **Operational exceptions.** `INS_COMMISSION_VARIANCE` (received ≠ expected) and
  `INS_COMMISSION_OUTSTANDING` (expected past due, unpaid) raise through the **shared
  Exception Engine** — idempotent, auto-resolving, anchored to the policy's owners. Callable
  + manual scan endpoint ship now; live cron wiring is Phase 6.
- **Revenue rollup.** `insurance_reporting.commission_report` — expected/received/outstanding/
  variance totals by schedule and by organization, tagged with the `insurance_commissions`
  revenue category. Operational reporting only.
- **Surface.** JSON API (`/api/v1/insurance/commissions*`, `/commission-statements*`,
  `/commission-lines/*`) + a `/insurance/commissions` staff console. New capability
  `insurance.commissions.write` (read capability was seeded in Phase 0), granted to
  administrator / insurance_agent / insurance_operations; ledger scoped by policy record scope.
- **Non-regulated.** This is money movement and reconciliation only — no suitability,
  replacement/1035, licensing, or CE determination; nothing is blocked. A test asserts no
  regulated-determination function or verb leaked into the commission surface.

### Fixed — Phase 5 audit & revenue-validation pass
- **Adjustment / reversal / chargeback** — added `record_adjustment` (a signed delta applied
  to an entry's canonical net `received_amount`, distinguished by kind in the audit trail) so
  true-ups, reversals, and carrier chargebacks are first-class and flow through the rollup;
  audited as `insurance.commission.adjusted`. `write_off` remains for uncollectible expected.
- **Audit completeness** — `reconcile_statement` now writes its own
  `insurance.commission.statement_reconciled` event for the statement-level status roll-up
  (in addition to the per-line events). Every commission mutation is now covered by an
  immutable audit event; a test asserts one per mutation. Variance exception open/resolve is
  audited by the shared engine (`exception.raised` / `exception.resolved`).
- **Timeline privacy** — commission variance/outstanding exceptions are now **firm-internal
  (unanchored)**: they carry no person/household, so the shared engine no longer publishes a
  client-facing "Commission variance" Timeline event. Commission/compensation activity stays
  in the immutable audit log and the firm-internal exception queue — never the client Timeline
  (test-enforced).
- **Revenue source of truth** — the rollup now reads the **full scoped ledger (uncapped)** so
  totals cannot silently truncate; it derives every figure from `insurance_commissions`
  (`service_revenue` is never written by the ledger), is idempotent and non-duplicating on
  repeated runs, and reflects corrections/reversals immediately. Added **producer-payout vs
  agency-retained** and **by-producer** breakdowns, derived from the ledger + split data.
- **Robustness** — statement→policy auto-match no longer crashes on a duplicate policy number
  (deterministic oldest-first pick).

### Added — Phase 6 · Insurance exceptions, work management & scheduled scanning (`c9k0m1n2h3j4`)
- **Single `run_insurance_scan()`** orchestrates every insurance detector (in-force reviews,
  producer licensing/CE expiry, commission variance/outstanding) through the **shared Exception
  Engine** — no insurance-specific engine. Idempotent (stable dedupe), auto-resolving/reopening,
  with **per-detector failure isolation** so one detector or one organization's bad data never
  aborts the scan. Honest aggregate reporting: **organizations scanned, exceptions opened /
  resolved / reopened / skipped, failures**, plus each detector's own result.
- **Scheduled scanning** via the **existing scheduler** — `run_insurance_detector_scan`
  registered as `insurance-detector-scan` (interval `INSURANCE_SCAN_INTERVAL_MINUTES`, default
  30; `max_instances=1`, `coalesce=True` — no overlap). No new scheduler framework.
- **Insurance work queues** seeded through the **existing queue framework**
  (`work_queues.criteria`): `insurance_unassigned`, `insurance_exceptions`, `insurance_reviews`,
  `insurance_licensing`, `insurance_commissions`, `insurance_high_priority` — projected through
  the same `work_items` surface as tax/benefits. No new queue framework.
- **Automatic assignment** via the **existing assignment rules** (`app/services/insurance_work.py`
  reuses `apply_assignment_rules`) — `auto_assign_unassigned` applies rules to unassigned open
  insurance exceptions; with no rule configured, items stay in *Insurance — Unassigned*. No new
  assignment model.
- **Organization-based record scope** — commission exceptions now anchor the client
  **organization** (`related_entity_type='organization'`) for org-scoped queues/assignment while
  keeping `person_id`/`household_id` NULL, so **no compensation ever reaches the client
  Timeline** (client-facing exception visibility remains out of scope). Reviews keep their
  existing org/person/household anchor.
- **Manual twin** `POST /api/v1/insurance/scan` (capability `insurance.write`) runs the same
  orchestrated scan + auto-assignment.
- Non-regulated throughout: no suitability, replacement/1035, or licensing determination; the
  **AD-5 gate is unaffected**.

### Changed — Pre-Phase-7 architecture cleanup (`d0l1n2o3i4k5`)
Behavior-preserving cleanup from the Release 0.10.0 architecture review (items #1–#3):
- **Docs refresh** — removed stale "cron wiring is Phase 6 (future)" wording from
  `insurance_detectors.py` and the architecture doc now that the scheduled scan is live;
  corrected the commission-exception privacy comment to reflect the Phase 6 organization anchor.
- **De-duplicated scan plumbing** — introduced shared `_exception_status`, `_scan_delta`, and
  `_run_detector_deltas` helpers; the four scan functions now share one diff implementation
  instead of four copies. **No functional change** (identical return shapes/values; all detector
  tests unchanged).
- **Dedicated scan authorization** — new capability **`insurance.scan`** (data-only migration
  `d0l1n2o3i4k5`) gates the operational scans (`/scan`, `/reviews/scan`, `/commissions/scan`)
  instead of overloading `insurance.write`/`.commissions.write`. Running a non-mutating detection
  sweep is now its own authority. Granted to the same roles that could scan before
  (administrator, insurance_agent, insurance_operations) — **no expansion, no weakening**; the
  producer-licensing scan keeps its tighter `insurance.licensing.write` gate.

### Added — Phase 7 · Policyholder portal surface (no migration; reuse the portal)
- **Read-only policyholder policy view** through the **existing** portal framework
  (`app/services/insurance_portal.py` + portal routes/template) — no insurance-specific portal
  engine, auth, session, or scope model. Scope is **opt-in**: resolved with
  `portal_scope(account_id, permission="insurance")`, so only a grant that allows the
  `insurance` permission sees anything; policies match by person / shared-household /
  organization scope.
- **Proportional disclosure** — carrier, product, policy number, status, issue date, face
  amount, premium, coverages, riders, and the policyholder's own owner/insured/beneficiary
  designations. `GET /api/v1/portal/insurance/policies[/{id}]` + a `/portal/insurance` page; the
  portal dashboard gains an `insurance_policies` slice.
- **Out-of-scope policy ids deny existence with 404**; unauthenticated portal access → 401.
- **Client-facing exception visibility stays out of scope** — the surface never exposes
  producers, commissions/compensation/splits, licensing/CE, exceptions, or internal metadata.
  Insurance exceptions cannot reach the client action-needed surface (the shared
  `client_action_items` is hard-scoped to `domain='tax'`). Factual policy data only — no
  suitability/replacement determination; **AD-5 unaffected**.
- No schema change (read-only over existing tables; the `insurance` grant permission is JSON).

### Added — Phase 8 · Reporting & dashboards (no migration; extend `insurance_reporting`)
- **Consolidated operations dashboard** (`insurance_reporting.operations_dashboard` +
  `GET /api/v1/insurance/dashboard` / `/insurance/dashboard`) — a **firm-internal staff** surface
  that composes the existing per-domain reports (pipeline, reviews, commissions, licensing) plus
  three new operational summaries, **proportional to the viewer's capabilities**: each optional
  section is included only if the viewer holds its capability (commissions →
  `insurance.commissions.read`, licensing → `insurance.licensing.read`, exceptions →
  `exception.read`, work_queues → `work.read`, portal_adoption → `record.read_all`); the response
  names the `sections_included`.
- **New summaries, all derived from a scope-filtered list** (authorization before aggregation):
  `exception_summary` (reuses `exception_engine.list_exceptions(domain="insurance")`, counts by
  code/severity/status), `work_queue_report` (reuses Work Management `work_items` + the existing
  queue criteria for depths), and `portal_activity_report` (firm-internal policyholder-portal
  adoption — oversight only).
- **Reuse only** — extends `insurance_reporting.py`; no parallel reporting engine, dashboard
  framework, authorization system, or record-scope model. Record scope is applied before every
  aggregation; `record.read_all` aggregates firm-wide.
- **Firm-internal boundary** — a staff surface under `/insurance/*` (401 without auth), never the
  client portal; producer compensation, commissions, licensing, exceptions, and queue internals
  are shown only to staff who already hold those capabilities. The Phase 7 portal is untouched.
- **Non-regulated** — operational counts, workflow status, and financial reconciliation only; no
  suitability, replacement/1035, licensing-validation, sale-blocking, compliance-approval, or any
  compliance metric. A test asserts the dashboard carries no compliance/determination content.
  **AD-5 unaffected.** No schema change (read-only; reuses existing capabilities).

### Added — Phase 9 · Integration ports as disabled stubs (no migration; reuse the provider idiom)
- **Vendor-neutral extension points, disabled** (`app/services/insurance_integrations.py`) — six
  ports: `carrier_policy_feed`, `case_status_feed`, `commission_statement_feed`,
  `licensing_appointment_feed`, `document_evidence_intake` (inbound), and `operational_export_hook`
  (outbound). Ships the neutral interfaces + **disabled** stubs only — same registry idiom as
  `benefits_providers` / `tax_filing_providers` / `portal.providers`; **no parallel integration
  framework**.
- **Inert by construction** — every port reports `enabled=False` / `status='not_connected'`.
  Calling a disabled port **fails safe** (`outcome='disabled'`, no external I/O — no HTTP, file
  transfer, auth, polling, or vendor API call). `enabled` is hardcoded and **never read from
  configuration or environment** — no port activates because a config value exists; activation is
  an explicit code decision (a concrete adapter + registry row) in a future release.
- **No secrets / endpoints / scheduled jobs** — no credentials, tokens, certificates, URLs, or
  production config are added; no scheduler job is registered. Read-only registry/status routes
  (`GET /api/v1/insurance/integration/ports[/{key}]`, `insurance.read`) and an inert invoke
  (`POST …/{key}/invoke`, `insurance.write`); invoking writes an **audit-safe** event
  (`insurance.integration.port_invoked`) with metadata only — never the payload or secrets.
- Non-regulated transport extension points only — no suitability, replacement/1035, licensing
  validation, sale-blocking, or compliance approval; **AD-5 unaffected**. No schema change.

### Blocked / deferred
- **AD-5 — compliance reviewer NOT YET NAMED → all regulated insurance logic BLOCKED.**
  Michael Shelton is recorded as the **business** owner (workflow/operational scope); this
  is **not** regulatory certification. No regulated phase passes its RC gate without a
  completed, approved sign-off artifact from a qualified, named compliance reviewer. This
  is not resolvable in code and remains open.
- **Remaining phases:** 10 (RC validation + release), plus the AD-5-gated regulated portions of
  Phases 2–4.

### Infrastructure / hygiene (0.10.0 pre-Phase-5 checkpoint)
- **Interpreter portability** — `scripts/lib/pyenv.sh` resolves a Python 3 interpreter
  (active virtualenv → repo-local `.venv` → `python3` → `python` if Python 3, else a clear
  failure) with no hardcoded paths; `test.sh`, `restore_rehearsal.sh`, `release.sh`,
  `demo.sh`, `check_migrations_reversible.sh`, `check_migration_heads.sh`, and
  `check_schema_at_head.sh` now source it and invoke `python`/`alembic`/`pytest`/`uvicorn`
  through `$PYTHON`. Fixes the bare-`python` failure that broke the harness on
  venv-only/py3.12 environments (previously 1 failing safety test).

### Migrations
Additive, off head `u1f9c0i9h8g7`, single head `d0l1n2o3i4k5`, reversible:
`v2b3d4f5a6c7` → `w3c4e5g6b7d8` → `x4d5f6h7c8e9` → `y5e6g7i8d9f0` → `z6f7h8j9e0g1` →
`a7g8i9k0f1h2` → `b8i9k1l2g3j4` → `c9k0m1n2h3j4` (Phase 6: data-only insurance work queues) →
`d0l1n2o3i4k5` (pre-Phase-7: data-only `insurance.scan` capability).

## [0.9.13] — 2026-07-16 — Platform Foundation

Developer platform, testing, and release hardening. **No product or business-logic
change; no schema change** (Alembic head unchanged at `u1f9c0i9h8g7`). Validated by
[RC-0.9.13](docs/RC_0.9.13_VALIDATION.md). Delivers issue #24.

### Added
- **Isolated test database** (#24) — the suite ran against the real development
  database (`client360`); it now refuses any non-disposable target. `app/safety.py`
  guard, `tests/conftest.py`, and `scripts/test.sh` (setup/reset/run/verify/status).
  A full run leaves `client360` byte-for-byte unchanged; local suite **287s → ~11s**.
- **Ruff lint gate** (#26) — `pyproject.toml` config and `scripts/ruff_gate.py`, a
  count-based ratchet that baselines the legacy backlog and fails only on *new*
  violations. Backlog tracked in #26.
- **CI/CD hardening** — pip caching, single-Alembic-head, migration-reversibility,
  schema-at-head, and test-DB-isolation checks; CHANGELOG lint; failure artifacts;
  branch protection requiring the `build` check on `main`.
- **Release tooling** — `scripts/release.sh` (guarded, dry-run), `scripts/check_changelog.py`,
  and `scripts/gen_rc.py` + `docs/templates/RC_TEMPLATE.md`.
- **Developer Demo Mode** (previously unreleased tooling) — safety-guarded local demo
  on a `client360_demo` database reusing real auth; `scripts/demo.sh`, role-aware
  landings, docs.

### Changed
- **Runtime Python 3.9 → 3.12.** Resolved the Typer/Click constraint by removing an
  orphaned `typer` pin (imported nowhere, required by nothing) — `click` unchanged.
  `requirements-py39.lock` retained one cycle for rollback.

### Fixed
- **Importers no longer run on import** — `schwab`, `wealthbox`, and `dave_ramsey`
  read `app/.env`, built an engine, and ran a real client-data import merely as a
  side effect of being imported. `dave_ramsey` alone wrote 7,755+ records per test run.
- `benefits` routes: `payload.dict()` → `model_dump()` (Pydantic v2 deprecation).
- Latent Jinja template bugs where `data.items`/`work.items`/`tax.items` resolved to
  the dict `.items` method; `/work`, `/tax/intake`, `/tax`, and the work queue pages render.

### Migrations
None — 0.9.13 is tooling/infrastructure only. Single head `u1f9c0i9h8g7`.

## [0.9.12] — 2026-07-16 — Application Shell & UI Consolidation

Consolidated every staff-facing page into a single application shell on a shared design
system, with progressive-enhancement interaction polish. **Frontend only** — no business
logic, routes, authorization, or record-scope semantics changed; no schema change (Alembic
head unchanged at `u1f9c0i9h8g7`). Validated by [RC-1](docs/RC1_UI_VALIDATION.md)
(0 unmet criteria). Merge commit `98b0622`.

### Added
- **Application shell + design system** — all 21 staff routes render inside one shell
  (`docs/UI_DESIGN_SYSTEM.md`); shared components, styled 403/404/500 pages, empty states.
- **Interaction polish** — client-side sortable data tables (numeric-aware, `aria-sort`),
  skip-to-content link, `aria-current`/`aria-expanded` navigation state. Progressive
  enhancement: every page works fully with JavaScript disabled.

### Fixed
- **Authorization denials now content-negotiate** — browser navigations get a styled HTML
  403, API/JSON clients keep the JSON body; the denial itself (status, audit) is unchanged.
  Denials now also carry the standard security headers (`x-frame-options`, CSP
  `frame-ancestors`, `nosniff`, `referrer-policy`), which they previously lacked.
- **CI was never running** — the workflow was tab-indented (invalid YAML) and failed at 0s on
  every commit, including the 0.9.11 merge. It now parses, provisions Postgres, and gates.
- **Importers are side-effect free on import** — `app/importers/schwab.py` and `wealthbox.py`
  no longer read `app/.env`, build an engine, or run a client-data import merely on import.
- Flaky securities-symbol collision fixed in the portfolio query tests.

### Migrations
None — 0.9.12 is frontend/tooling only. Single head `u1f9c0i9h8g7`.

## [0.9.11] — 2026-07-15 — Employer Operations & Employee Benefits

Usable **Employer Operations** product on shared Client360 concepts (Organizations,
relationship roles, service lines, universal Engagement) with **Employee Benefits + Retirement**
first-class (ADR-18). Reuses Person/Household, Documents, Work Management, Timeline, Audit, the
Exception Engine, the Portal, and the scheduler — no second engine/scheduler/portal/workflow/
reporting framework/data model. Tax untouched. Validated by [RC14](docs/RC14_VALIDATION.md)
(**SAFE TO MERGE**, 0 defects). See [Release 0.9.11 Notes](docs/RELEASE_0.9.11.md). Alembic
head `u1f9c0i9h8g7`.

### Added
- **Organization foundation** — `relationship_entities` + `organization_profiles` (EIN
  encrypted); permanent relationship roles; typed ownership (`relationship_ownership`); service
  lines; universal `engagements`; canonical services with Organization record scope; disabled
  carrier/recordkeeper(Betterment)/payroll/HRIS ports.
- **Benefits & retirement** — 17 plan types, plans/plan-years, employments/enrollments/deferral
  elections; Betterment seeded (no integration).
- **Detectors** — 18 health + retirement detectors (`domain='benefits'`; idempotent/auto-resolve/
  reopen); date-driven obligation detector; documented inert gaps (never inferred).
- **Compliance & renewal obligations** — templates + instantiated obligations (verified dates);
  shared SLA sweep extended to benefits (internal-only, honest outcomes).
- **Work Management** — benefits exceptions in the canonical `work_items()` + seven benefits
  queues; assignment rules; scheduled scan (overlap-prevented, per-org isolation, honest metrics).
- **Staff API + consoles** — `/api/v1/organizations` + `/api/v1/benefits`; `/organizations`,
  `/benefits`, `/benefits/reporting` on the modern shell (names not IDs; EIN gated).
- **Employer portal** — org-scoped Action Needed (PII-free allowlist), census upload, secure
  messages, auditable employer notifications.
- **Dashboards & reporting** — proportional benefits dashboard (book, participation,
  compliance/renewal calendar, exceptions); authorization-filtered; reuses `exception_reporting`.
- New `organization.*` / `benefits.*` capabilities + `benefits_*` roles (no role widened; no new
  `record.read_all`).

### Migrations
`r8c69f7e6d5c` · `s9d7a8g7f6e5` · `t0e8b9h8g7f6` (data-only) · `u1f9c0i9h8g7`. Single head.

## [0.9.10] — 2026-07-14 — Exception Engine

Platform-wide **Exception Engine** (ADR-17), implemented **tax domain only**. Validated by
[RC13](docs/RC13_VALIDATION.md) (**SAFE TO MERGE**, 0 defects); merged to `main` and tagged
`v0.9.10`. See [Release 0.9.10 Notes](docs/RELEASE_0.9.10.md). Alembic head `q7b58f6c5d4e`.

### Added

- **Canonical Exception Engine** — domain-neutral `exceptions` / `exception_events` /
  `exception_types` (required CHECK-constrained `domain`); one state machine, idempotent
  dedupe, stale-action rejection, immutable append-only event ledger, audit + timeline on
  every mutation, and record-scope authorization on every read/write.
- **15 tax detectors** translating existing tax source-of-truth conditions into exceptions
  (stable dedupe keys; auto-resolve on clear, reopen on recurrence).
- **Deterministic, replay-safe SLA sweep** with severity-based escalation and **honest
  notification outcomes** (email/SMS stubbed → `disabled`, never fabricated).
- **Work Management integration** — exceptions project through the single `work_items()`
  point into My/Team Work, queues (`tax_exceptions`, `tax_exceptions_critical`,
  `compliance_exceptions`), agenda, capacity, and bottlenecks; reuses `record_assignments`
  (no second assignment model).
- **Versioned API + staff console** (`/api/v1/exceptions/*`, `/exceptions`) — thin routes
  over canonical services; out-of-scope → 404; blocker/compliance resolution segregation.
- **Client portal "Action Needed"** (`/portal/action-needed`,
  `/api/v1/portal/exceptions[/{id}]`) — strict client-visible allowlist, plain-language,
  scoped, portal-safe, read-only; no internal-field/event/audit leakage.
- **Exception dashboards & reporting** (`/exceptions/reporting`,
  `/api/v1/exceptions/report`) — authorization-filtered metrics (open/blocker/high/at-risk/
  breached/unassigned/compliance, by category/owner/team/client/return, aging, escalation
  distribution, MTTA, MTTR, reopen rate, SLA compliance, real trend); role-appropriate
  audiences; compact summary embedded on advisor/tax/operations dashboards.
- New least-privilege capabilities `exception.read` / `exception.write` /
  `exception.resolve` / `exception.compliance` (no role widened; no new `record.read_all`).

### Migrations

- `p6a47e5d4f3b` — exception engine schema (additive/reversible).
- `q7b58f6c5d4e` — data-only work-queue criteria (reversible). Single head.

## [0.9.9] — 2026-07-14

Platform Consolidation — a security, performance, and production-readiness
release with no new end-user features. See
[Release 0.9.9 Notes](docs/RELEASE_0.9.9.md) and
[RC12 Validation](docs/RC12_VALIDATION.md).

### Security

- Microsoft 365 OAuth tokens encrypted at rest (Fernet-encrypted MSAL cache keyed
  by `MICROSOFT_TOKEN_KEY`) with a durable `acquire_token_silent` refresh
  lifecycle; crypto fails closed when the key is absent; no plaintext token is
  written to the database or logs.
- Delegated Graph scopes reduced to least-privilege read-only (no `Mail.Send`, no
  `*.ReadWrite`).
- CSRF defense-in-depth: `Referer` fallback added to the `Origin` check.
- Config hardening: production boot fails without `SESSION_SECRET`; startup warns
  on a development fallback or a missing `MICROSOFT_TOKEN_KEY`.

### Performance

- 24 hot-path foreign-key indexes (built `CONCURRENTLY`, reversible) making the
  client/household/portal/workflow read paths index-bound.
- Eliminated four verified N+1 / full-scan hot paths (intake dashboard 28→7,
  concentration filter 28→2, portal `/notifications` 21→1, `work_items()`
  authorization pushed into SQL → O(caller's book)), preserving output and
  authorization semantics.

### Changed

- Consolidated the Microsoft Graph connector onto a single delegated path and the
  portal provider registries onto one canonical `ProviderRegistry`.
- Per-account Microsoft sync-health surfaced on `/microsoft365/status` and the new
  `/readiness` endpoint.

### Added

- `GET /readiness` (DB, Alembic head drift, scheduler, sync-health; 200/503);
  `GET /health` remains DB-independent liveness.
- Backup/restore runbook and rehearsal script.

### Removed

- `POST /timeline/test` debug endpoint, the unused app-only Graph connector
  modules, and verified-unused imports across 18 files.

### Migrations

- `m3d14a2f1e0c` (token security columns), `n4e25b3c2f1d` + `o5f36c4d3e2a`
  (hot-path indexes). Additive and reversible; single head `o5f36c4d3e2a`.

## [0.9.8] — 2026-07-14

Sprint 5.4 — Tax Document Intelligence & Missing Information. See
[Release 0.9.8 Notes](docs/RELEASE_0.9.8.md) and
[Tax Document Intelligence](docs/SPRINT_5_4_TAX_DOCUMENT_INTELLIGENCE.md).

### Added

- Deterministic tax document matching engine (exact identifiers, confidence
  scoring, ambiguity floor) with mandatory human review for anything not
  deterministically resolved. Replaces the substring-based Microsoft document
  matching (RC8 H13).
- Authorization-aware ownership validation and record-scope-checked reviewer
  actions (accept/reject/reassign/classify/duplicate/revert) with immutable,
  append-only review and evidence ledgers.
- Missing-information engine that recomputes from accepted document links and
  drives the existing checklist / portal-request / workflow-gating mechanisms.
- Staff document-review workspace and `/api/v1/tax/documents` + checklist/missing
  APIs; new `tax.document.review` capability and four document review queues.
- AI classifier port (interface only; inert — no vendor, no external call).
- Shared tax dashboard stylesheet (`tax.css`), closing an RC8 unstyled-class gap.

- RC11 remediation: wired ingestion end-to-end — portal uploads and Microsoft
  documents now flow through the engine (dual-source links reference either a
  canonical or a Microsoft document, no binary duplicated); made ingestion
  idempotent; added review-state guards (HTTP 409 on stale actions); re-validate
  document owner vs return client on accept/reassign (HTTP 403 + denied audit);
  and persist unmatched documents reviewably without fabricating ownership.

### Database

- Added `tax_document_links`, `tax_document_classifications`,
  `tax_document_match_evidence`, `tax_document_review_events` (append-only), the
  `tax.document.review` capability, four review queues, and the
  `tax_missing_items` FK index (RC9 H20); legacy free-text Microsoft matching
  rules deactivated. RC11 remediation adds a dual-source link model (nullable
  `document_id` + `microsoft_document_id` with an exactly-one-source CHECK) and a
  nullable return for unmatched links. Parent `j0a81f9c8d7e`; new head
  `l2c03f1e0d9b`.

### Security

- Eliminated all substring/containment ownership matching for tax documents
  (H13). Auto-assignment requires a single exact-identifier candidate above the
  auto-match threshold with no competing candidate above the ambiguity floor.

### Validation

- 136 automated tests passed; independent RC11 adversarial validation and retest
  (43/43 checks) confirmed H13 cannot be recreated across nine datasets and that
  the RC11 remediation introduced no new gap (SAFE TO MERGE). Clean installation,
  v0.9.7 upgrade/downgrade/re-upgrade, and sentinel preservation validated. See
  [RC11 Validation](docs/RC11_VALIDATION.md) and [RC11 Retest](docs/RC11_RETEST.md).

## [0.9.7] — 2026-07-14

Security hardening release. Fixes the confirmed, RC9-verified authorization,
record-scope, and workflow-permission defects before Sprint 5.4. No new feature
work; least privilege, immutable audit, and record-level authorization
preserved. See [Security Hardening 0.9.7](docs/SECURITY_HARDENING_0.9.7.md).

### Security

- Fixed work-assignment privilege escalation: assigning a client record now
  requires `assignment.manage` plus record scope, separated from ordinary
  `work.write` mutation (H1); reassign/remove now enforce assignment ownership
  (H8).
- Fixed role-composition privilege escalation: `role.manage` can only grant
  capabilities it holds and cannot assign a more-powerful role or recompose the
  protected administrator role (H2).
- Enforced record-scope authorization consistently on tax return review and
  correction endpoints (H3).
- Corrected the middleware/route capability mismatch that locked the compliance
  role out of workflow approvals (H4).
- Required authorization over a relationship's owning record before
  deactivation (H5).
- Scoped client-profile pickers to prevent firm-wide name/email enumeration
  (H6).
- Enforced the portal `messages` grant on secure-message read/send/mark-read
  with default-deny (H7).
- Restricted the firm-wide reminder trigger to firm-wide record authority (H9).

### Fixed

- Rewrote the always-zero "Unassigned" tax dashboard metric (H11) and the
  always-zero "pending matches" dashboard metric (H14).
- Eliminated a duplicate database connection pool created at startup via the
  `person_merge` import chain (H22, narrow fix).

### Added

- Canonical record-scope authorization service (`app/security/authorization.py`)
  and 20 authorization regression tests.
- Immutable `outcome="denied"` audit events for denied high-risk mutations.

### Database

- Migration `j0a81f9c8d7e` aligns `tax_engagement_returns.status` server default
  to `received` (parent `i970d9f7b8c9`; new head `j0a81f9c8d7e`).

### Validation

- 94 automated tests passed (74 existing + 20 new), clean installation, v0.9.6
  upgrade/downgrade/re-upgrade, sentinel preservation, startup, route, OpenAPI,
  template, authorization-matrix, and immutable-audit validation.
- Independent RC10 adversarial validation passed (52/52 attack cases blocked;
  no unintended regressions; SAFE TO MERGE). See
  [RC10 Validation](docs/RC10_VALIDATION.md).

## [0.9.6] — 2026-07-14

### Added

- Canonical 15-state tax return lifecycle with immutable transition history.
- Preparer, manager, and partner reviews linked to the existing independent
  approval engine, including corrections and return-to-preparer behavior.
- Portal return approval, e-file authorization, delivery acknowledgement,
  provider-neutral filing events, nine production queues, four dashboards, and
  versioned staff/portal APIs.

### Database

- Added five production tables and ten return lifecycle/filing columns with
  parent `h860c8e6a7b8`; new head `i970d9f7b8c9`.

### Validation

- Added the Tax Return Lifecycle architecture and PR #16 RC7 validation
  record.
- Passed 74 automated tests, clean installation, v0.9.5 upgrade/downgrade/
  re-upgrade, sentinel preservation, startup, route, OpenAPI, and template
  validation.
- Found and fixed two template defects during release-candidate validation:
  a missing shared staff base template and a Jinja/dict-key collision on the
  production dashboard.

## [0.9.5] — 2026-07-14

### Added

- Versioned engagement-letter, organizer, questionnaire, and document-checklist
  templates with immutable published definitions and launch-time snapshots.
- Tax intake orchestration, saved progress, conditional/required questions,
  missing-information tracking, portal completion, daily reminders, readiness
  dashboards, and automatic workflow advancement.
- Versioned staff and portal APIs for tax intake, backed by existing document,
  notification, assignment, queue, timeline, audit, and authorization services.

### Database

- Added 12 intake tables with parent revision `g750b7d5f6a7`; new head
  `h860c8e6a7b8`.

### Validation

- Added the Tax Engagement Intake architecture and RC6 validation report.
- Passed 69 automated tests, clean installation, v0.9.4 rollback/re-upgrade,
  sentinel preservation, startup, route, OpenAPI, and template validation.

## [0.9.4] — 2026-07-14

### Added

- Provider-neutral tax firms, offices, staff office roles, tax years, seasons,
  filing jurisdictions, return types, filing statuses, engagements, returns,
  calendars, versioned deadline rules, and workflow links.
- Authorized tax production dashboard and versioned `/api/v1/tax` reference,
  dashboard, engagement, and deadline operations.
- Five reusable tax work queues, four tax capabilities, eight baseline return
  types, six filing statuses, and a versioned Tax Engagement Foundation workflow.
- Automatic engagement workflow generation with existing assignment, queue,
  timeline, immutable audit, and record-level authorization integration.

### Documentation

- Added the nine-sprint Epic 5 Tax Practice Platform technical design.
- Defined normalized tax, workflow, portal, document, provider, security,
  reporting, migration, testing, and Release 1.0 readiness architecture.
- Added Tax Domain Foundation operating documentation and the RC5 release
  validation report.

### Database

- Alembic head: `g750b7d5f6a7`.
- Added 14 normalized tax-domain tables while preserving Release v0.9.3 data.

## [0.9.3] — 2026-07-14

### Added

- Separate portal identities, household/delegated grants, invitations,
  MFA-ready sessions, password-reset handoff, and device tracking.
- Secure client messaging, internal-note isolation, attachments, and receipts.
- Document requests, upload versions, approvals, client workflow tasks,
  notifications, and provider-neutral e-signature abstractions.
- Versioned portal APIs and eight portal pages.

### Security

- Portal accounts and sessions are isolated from staff identities.
- Self-only, joint, trusted-contact, and delegated household grants are
  explicitly scoped and time bounded.
- Messages, read receipts, route mutations, and security events are audited;
  client-visible queries exclude internal staff notes.

### Database

- Alembic head: `f640a6c4e5f6`.
- Added 15 portal identity, access, session, collaboration, notification, and
  signature-request tables without changing Release 0.9.2 data.

## [0.9.2] — 2026-07-14

### Added

- Immutable, versioned workflow templates with complete launch-time snapshots.
- Dependency-aware sequential, parallel, and conditional workflow execution.
- Pause, resume, cancel, complete, and reopen controls.
- Independent approval routing with segregation-of-duties enforcement.
- SLA escalation processing and five-minute scheduler automation.
- Event-driven triggers and an idempotent automation action ledger.
- Workflow UI, metrics, reporting data, and `/api/v1/workflows` APIs.
- Twelve published templates for prospecting, onboarding, Schwab operations,
  transfers, reviews, tax, estate, insurance, termination, and compliance.

### Changed

- Workflow-instance assignments now authorize and expose child workflow steps
  in My Work.
- Published template definitions and workflow/audit event ledgers are protected
  by database triggers.

### Database

- Alembic head: `e530f5b3d4e5`.
- Added seven tables for templates, dependencies, events, triggers, actions, and
  escalations.
- Added execution snapshots and lifecycle metadata to Release 0.9.1 workflow
  records without replacing existing data.

## [0.9.1] — 2026-07-14

- Added Operational Work Management, assignments, reusable queues, My Work,
  Team Work, capacity, SLA risk, and versioned work APIs.
- Alembic head: `d420f4a2c3d4`.

## [0.9.0] — 2026-07-14

- Integrated Microsoft 365, Relationship Intelligence, Schwab Portfolio
  Intelligence, firm identity, capability authorization, and immutable audit.
- Alembic head: `c410f4a1b2c3`.
