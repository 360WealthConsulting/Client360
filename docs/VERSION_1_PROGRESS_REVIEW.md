# Client360 — Version 1.0 Progress Review

**As of:** Release v0.9.8 (`main` @ `8d27e95`, Alembic head `l2c03f1e0d9b`).
**Scope:** whole-platform review of everything shipped through v0.9.8.
**Nature:** assessment and roadmap only. No application code was modified.

---

## 1. Executive Summary

Client360 has shipped nine releases (v0.9.0 → v0.9.8) building a unified client
intelligence and practice-management platform for 360 Wealth Consulting and 360
Tax Solutions. The codebase is ~13,300 lines of application Python across 32
route modules and 22 service modules, backed by **110 database tables** on a
single linear Alembic history (22 migrations, one head), **26 composable
capabilities** across 4 seeded roles, **30 work queues**, ~178 registered routes,
and **136 automated tests**.

Two epics dominate the work to date:

- **Epic 4 — Practice Management Platform** (Operational Work Management, Workflow
  Automation, Client Portal) is functionally complete and released across
  v0.9.1–v0.9.3.
- **Epic 5 — Tax Practice Platform** is roughly half delivered: the domain
  foundation, engagement intake, return lifecycle, and (as of v0.9.8) document
  intelligence are shipped (v0.9.4–v0.9.8); exceptions, filing/compliance
  completion, the full client tax portal, and production reporting remain.

Underpinning both is a v0.9.0 foundation (CRM, Microsoft 365 intelligence,
relationships, Schwab portfolio, identity/RBAC/audit) and a dedicated **security
hardening release (v0.9.7)** that closed two exploitable privilege-escalation
paths and multiple IDORs found by the RC8/RC9 architecture review.

**The engineering discipline is strong** — single linear migration history,
immutable audit, capability-based authorization, adversarial RC validation gates
(RC8–RC11), and a consistent "reuse the platform, don't fork it" principle.
**The gaps to 1.0 are concentrated in three areas:** (a) tax platform completeness
(Epic 5 is ~55% done), (b) production-hardening debt that was identified but not
yet scheduled — most notably Microsoft 365 OAuth tokens stored in plaintext with
no refresh (RC8/RC9 **H10**) and dashboard/query performance (N+1, missing
indexes; **H15–H20**), and (c) the operational/production-readiness gates
(managed-OIDC/MFA validation, backup-restore rehearsal, accessibility,
penetration test, observability, runbooks) that feature completion does not
satisfy.

**Overall Release 1.0 readiness: ~65%** (see §14).

---

## 2. Completed Epics and Sprints

| Release | Tag | Epic / Sprint | Content |
|---|---|---|---|
| v0.9.0 | `v0.9.0` | Foundation | CRM, Microsoft 365 intelligence, relationships, Schwab portfolio, identity/RBAC/audit |
| v0.9.1 | `v0.9.1` | Epic 4 · Sprint 4.2 | Operational Work Management (assignments, queues, My Work / Team Work, capacity, SLA) |
| v0.9.2 | `v0.9.2` | Epic 4 · Sprint 4.3 | Workflow & Process Automation (versioned templates, approvals, escalations, triggers) |
| v0.9.3 | `v0.9.3` | Epic 4 · Sprint 4.4 | Client Portal & Secure Collaboration (identities, grants, messaging, requests, e-signature abstraction) |
| v0.9.4 | `v0.9.4` | Epic 5 · Sprint 5.1 | Tax Domain Foundation (firms/offices/jurisdictions/deadlines/engagements) |
| v0.9.5 | `v0.9.5` | Epic 5 · Sprint 5.2 | Tax Engagement Intake (letters/organizers/questionnaires/checklists/missing items) |
| v0.9.6 | `v0.9.6` | Epic 5 · Sprint 5.3 | Tax Return Lifecycle (15-state pipeline, reviews, client approvals, filing events, delivery, dashboards) |
| v0.9.7 | `v0.9.7` | Security Hardening | RC8/RC9 authorization, record-scope, and workflow-permission fixes (no new features) |
| v0.9.8 | `v0.9.8` | Epic 5 · Sprint 5.4 | Tax Document Intelligence (deterministic matching, H13 fix, missing-information engine) |

Note: the as-built Sprint 5.2/5.3 ordering swapped the original Epic 5 design
(intake shipped before lifecycle); see `docs/EPIC_5_REVISED_PLAN.md`.

---

## 3. Features Implemented

**Client intelligence / CRM.** Canonical people, households, source-contact
matching and merge, tasks, activities, documents (+ versions), timeline, unified
search, and Client Workspace.

**Microsoft 365 intelligence.** Outlook mail and calendar sync into canonical
timelines; SharePoint/OneDrive document metadata linkage (no binary duplication);
unmatched mail/attendee/document review queues.

**Relationship intelligence.** Family, professional, business, trust, estate,
beneficiary, and household relationships as a normalized graph.

**Portfolio intelligence.** Schwab accounts, registrations, holdings, cash, lots,
transactions, performance, billing, beneficiaries, and household rollups.

**Identity, authorization, audit.** OIDC staff login, capability-composed roles,
teams, record assignments, sessions, record-level authorization, immutable
append-only audit, and a canonical record-scope authorization service (v0.9.7).

**Work management.** Assignment engine + history, reusable queues, My Work / Team
Work, capacity/SLA views, versioned APIs.

**Workflow automation.** Immutable versioned templates with launch-time
snapshots, dependency/parallel/conditional execution, independent approvals
(segregation of duties), SLA escalations, event triggers, 5-minute scheduler.

**Client portal.** Separate portal identities, household/delegated grants, secure
messaging, document requests, client tasks, notifications, e-signature
abstraction (data model only — see §4).

**Tax practice platform.** Firms/offices/jurisdictions/deadlines; engagements and
returns; engagement letters, organizers, questionnaires, document checklists,
missing-item tracking; a 15-state return lifecycle with preparer/manager/partner
reviews, client approvals, provider-neutral filing events, and delivery; a
deterministic document matching engine with mandatory human review; production
queues and dashboards.

---

## 4. Remaining Epic 5 Work

Per the accepted revised plan (`docs/EPIC_5_REVISED_PLAN.md`), four sprints
remain to complete Epic 5:

- **Sprint 5.5 — Tax Exceptions:** extensions, estimated payments, notices,
  amendments (no tables/services exist yet).
- **Sprint 5.6 — Filing / Delivery / Compliance completion:** wire the currently
  **orphaned** filing-provider abstraction (`tax_filing_providers.py` is never
  imported) into `record_filing`; retention classes / legal hold; delivery-package
  generation; compliance evidence packs; wire or retire the **dead**
  `portal/signatures.py` e-signature module.
- **Sprint 5.7 — Secure Tax Portal completion:** the full client tax journey
  (delivery center, preferences, notice collaboration) plus the production
  identity/MFA/rate-limit gates that overlap the Release 1.0 portal launch gates.
- **Sprint 5.8 — Tax Production Reporting & Capacity:** reconciled dashboards,
  deadline calendar, productivity and capacity views with scope-aware drill-down
  (in-Python dashboard aggregation must move to SQL).

Cross-cutting tax debt to retire in these sprints: the unstyled-class gap was
closed in 5.4 (`tax.css`), but the orphaned filing provider, dead e-signature
module, and the in-Python dashboard aggregation remain.

---

## 5. Remaining Epic 6 Work

Moved out of Epic 5 (per the revised plan) to keep Epic 5 vendor-independent:

- **Epic 6 — Tax Data Acquisition & Provider Integration:** Drake first adapter;
  UltraTax/Lacerte/CCH interface contracts; IRS transcript request/import;
  provider connections, import runs, normalized facts/provenance, reconciliation,
  transcript consent/artifacts. Requires vendor contracts, secrets management, and
  transcript regulatory handling.
- **Epic 6 — AI-Assisted Tax Operations:** governed AI classification, extraction,
  and recommendation ports with evidence capture and human-decision records
  (Sprint 5.4 shipped only an inert interface-only classifier port).
- **Epic 6 — Seasonal capacity forecasting & workload balancing** (advanced
  analytics beyond the reconciled operational reporting that completes Epic 5).

---

## 6. Recommended Epic 7 — Platform Consolidation

The RC8/RC9 review surfaced cross-cutting debt that is not owned by any feature
epic and would be best delivered as a dedicated consolidation epic before or
alongside 1.0:

1. **Authorization-model unification.** Collapse the three divergent record-scope
   implementations (`has_record_scope` / `_scope_filter` / `authorized_assignments`)
   into the single canonical service introduced in v0.9.7 (RC9 Release 1.0 item).
2. **Database constraint & type hardening (H21).** CHECK constraints / lookup
   tables for the many free-text status/type columns; polymorphic `entity_type`
   validation; migrate `json` → `jsonb` platform-wide.
3. **Performance & indexing (H15–H20).** Eliminate the N+1 dashboard patterns
   (work items, tax intake, portal fan-out, portfolio concentration) and add the
   ~48 missing foreign-key/hot-column indexes; a partitioning/retention strategy
   for the unbounded append-only event tables.
4. **API & template consistency.** One shared response envelope and pagination
   convention (17+ list-response shapes today; no `response_model` anywhere);
   consolidate the three page-rendering conventions (raw f-string HTML, standalone
   templates, `{% extends %}`) onto shared layouts.
5. **Schema source-of-truth consolidation.** Resolve the `app/db.py` (reflection)
   vs `app/database/schema.py` (partial models) split so the full schema is
   discoverable in Python and startup does not require a live DB.
6. **Dead-code removal.** ~600-line unused Microsoft Graph connector
   (`app/connectors/microsoft365/*`), orphaned `tax_filing_providers.py`, dead
   `portal/signatures.py`.

---

## 7. Current Database Architecture

- **110 tables**, single linear Alembic history (22 migrations, head
  `l2c03f1e0d9b`), additive/reversible migrations, sentinel-preservation validated
  each release.
- **Strengths:** immutable append-only ledgers protected by DB triggers
  (`audit_events`, `workflow_events`, timeline, tax lifecycle/filing/document
  events); consistent idempotency-key discipline; snapshot-on-launch for workflow
  templates; the newer tax and document tables carry CHECK constraints and FK
  indexes.
- **Debt (RC8/RC9):** ~48 missing FK/hot-column indexes on the older ~60% of
  tables; most free-text status/type columns lack CHECK/lookup enforcement (H21);
  no `jsonb` (all `json`); no partitioning/retention for unbounded event tables;
  `app/db.py` reflects the live DB at import while ~50% of tables have no Python
  model; hand-picked (non-hash) migration revision IDs beyond the baseline.

---

## 8. Current API Surface

- ~178 registered routes; **~90 under `/api/v1/`** (the Epic 4/5 JSON APIs) with a
  large remaining set of unversioned HTML/legacy routes and a few unversioned JSON
  endpoints (`/api/stats`, `/api/search`, `/admin/*`).
- **Strengths:** versioned tax/work/workflow/portal APIs; capability + record +
  office + portal scope enforced; consistent request-id and security headers;
  middleware carve-outs prevent capability shadowing (H4 lesson).
- **Debt:** no shared response envelope (each router invents its own key; 17+
  shapes); no `response_model`/OpenAPI schema on any endpoint; no pagination
  convention except one ad-hoc `limit`; inconsistent `ValueError`/`PermissionError`
  → HTTP-status mapping across routers.

---

## 9. Current Security Architecture

- **Model:** capability-based RBAC (26 capabilities, 4 seeded roles) composed into
  roles; record-level authorization via `record_assignments` + `record.read_all/
  write_all` bypass; tax office-membership scope; separate portal identity system
  with household/delegated grants; append-only immutable audit; OIDC-only staff
  auth; passwordless MFA-gated portal auth; SHA-256-hashed opaque session tokens.
- **v0.9.7 hardening (closed):** work-assignment privilege escalation (H1) and
  IDOR (H8); role-composition escalation with administrator-role protection (H2);
  tax review/correction IDOR (H3); compliance workflow-approval lockout (H4);
  relationship-deactivation IDOR (H5); client-picker enumeration (H6); portal
  secure-messaging permission enforcement (H7); firm-wide reminder scope (H9).
  Introduced the canonical record-scope authorization service and denial audit
  events. Independently re-verified (RC10, 52/52 adversarial checks).
- **v0.9.8 additions:** deterministic tax document matching (eliminated the H13
  substring cross-client exposure), reviewer authorization, cross-owner denial
  with immutable audit, append-only document ledgers (RC11 + retest, 43/43).
- **Open security debt:** **Microsoft 365 OAuth access/refresh tokens are stored
  in plaintext with no refresh implementation (H10)** — the highest-priority open
  item; CSRF is best-effort (Origin-only, fails open on missing header); the
  session secret has a hardcoded dev fallback outside `production`; audit reads of
  sensitive PII/tax data are not logged; the three divergent record-scope
  implementations remain (consolidation deferred).

---

## 10. Current Microsoft 365 Integration Status

- **Working:** OAuth connect/callback; delta-aware SharePoint/OneDrive document
  sync; mail and calendar sync into canonical timelines; unmatched review queues;
  and (v0.9.8) deterministic document matching feeding the tax pipeline.
- **Open (RC8):** **plaintext token storage and no token refresh (H10)** — the
  integration silently stops ~1 hour after connect; single "most recently
  connected" account is used for all sync (schema supports many); no
  throttling/backoff (a 429 aborts a cycle); mail/calendar are single-page
  (non-paginated) fetches; no sync-health observability; ~600 lines of unused
  alternate Graph client. These are the biggest reliability/security gaps outside
  the tax platform.

---

## 11. Current Tax Platform Status

- **Shipped (v0.9.4–v0.9.8):** ~40 tax tables; firms/offices/jurisdictions/
  deadlines; engagements/returns; intake (letters, organizers, questionnaires,
  checklists, missing items); a 15-state return lifecycle with reviews, client
  approvals, provider-neutral filing events, delivery, production queues and
  dashboards; and a deterministic document intelligence engine (portal +
  Microsoft ingestion, mandatory review, missing-information recompute).
- **Remaining (Epic 5):** exceptions (5.5), filing-provider wiring / compliance
  completion (5.6), full client tax portal (5.7), reconciled reporting (5.8).
- **Debt:** orphaned filing-provider abstraction; dead portal e-signature module;
  in-Python dashboard aggregation (N+1 at season scale).
- **Assessment:** functionally the most advanced part of the app and the most
  rigorously validated (RC8–RC11), but ~55% of the planned tax scope.

---

## 12. Current CRM Status

- **Working:** canonical people/households, source matching + merge, tasks,
  activities, documents + versions, timeline, search, Client Workspace,
  relationship graph, portfolio rollups.
- **Debt (RC8):** advisor notes are stored as flat `.txt` files (not in Postgres,
  not backed up); missing indexes on the highest-traffic person-profile queries
  (`tasks.person_id`, `activities.person_id`, `documents.person_id`,
  `timeline_events.person_id/household_id`); two dashboard KPIs were dead (both
  fixed in v0.9.7); a leftover `POST /timeline/test` debug endpoint; the match
  review re-parses a CSV on every page load.

---

## 13. Current Practice Management Status (Epic 4)

- **Work management & workflow engine:** functionally complete and reused
  correctly by the tax platform (tax reviews use `work_approvals`; tax work uses
  `record_assignments` and `work_queues`).
- **Debt (RC8):** `work_items()` does unfiltered full-table scans on every
  dashboard load; SLA escalation only scans `workflow_steps` (plain-task
  `sla_due_at` is dead); escalations are write-only (no consumption/resolution
  path); the automation-action engine is modeled but unwired; the in-process
  scheduler has no leader election (duplicates work under multiple replicas).
- **Client portal:** grant/scope model is sound and reused, but has functional
  gaps — no returning-user login (only invitation acceptance creates a session),
  password reset generates a token it never delivers, no client document-download
  path, and the e-signature module is dead code. These gate any external portal
  launch.

---

## 14. Overall Release 1.0 Readiness

**Composite estimate: ~65%.** Transparent breakdown (weighted by effort-to-1.0):

| Dimension | Readiness | Rationale |
|---|---|---|
| Epic 4 practice-management platform | ~90% | Functionally complete; portal functional gaps + engine debt |
| CRM / relationships / portfolio intelligence | ~85% | Complete; indexing/notes/CSV debt |
| Microsoft 365 integration | ~55% | Works, but H10 token security/refresh + single-account + volume gaps |
| Tax platform (Epic 5) | ~55% | 5.1–5.4 shipped; 5.5–5.8 remain |
| Security architecture | ~80% | Strong; hardened in 0.9.7/0.9.8; H10 + authz consolidation open |
| Database architecture | ~70% | Sound history/audit; indexing/constraints/jsonb/partitioning debt |
| API/UI consistency | ~55% | Versioned APIs exist; envelope/pagination/template fragmentation |
| Production-readiness gates | ~30% | 0.9.7 security done; OIDC/MFA/backup/pentest/accessibility/observability open |
| **Composite** | **~65%** | Feature-rich and architecturally sound; tax completeness + production hardening are the gaps |

Feature completeness alone is higher (~75%); the production-readiness gates and
the unscheduled performance/token-security debt pull the composite down.

---

## 15. Top 20 Remaining Features Before Version 1.0

1. Microsoft 365 OAuth token encryption at rest (H10).
2. Microsoft 365 token refresh + sync-health observability (H10).
3. Tax exceptions — extensions & estimated payments (Sprint 5.5).
4. Tax exceptions — notices & amendments (Sprint 5.5).
5. Wire the filing-provider abstraction + first real e-file adapter (Sprint 5.6).
6. Tax retention classes / legal hold (Sprint 5.6).
7. Tax delivery-package generation + compliance evidence (Sprint 5.6).
8. Wire or retire portal e-signature into e-file authorization (Sprint 5.6).
9. Portal returning-user login flow (currently invitation-only).
10. Portal password-reset delivery (token is generated but never sent).
11. Portal client document download (no retrieval path today).
12. Full client tax portal journey — delivery center, preferences (Sprint 5.7).
13. Tax production reporting & deadline calendar (Sprint 5.8).
14. Tax capacity/productivity views with scope-aware drill-down (Sprint 5.8).
15. Dashboard N+1 elimination + missing hot-path indexes (H15–H20).
16. Database CHECK/lookup constraints + `json`→`jsonb` (H21).
17. Shared API response envelope + pagination convention.
18. Production managed-OIDC + MFA validation (staff and portal).
19. Move advisor notes from flat files into the database.
20. Consolidate the three record-scope implementations into one service.

---

## 16. Top 20 Post-Version 1.0 Enhancements

1. Epic 6 — Drake acquisition adapter.
2. Epic 6 — UltraTax/Lacerte/CCH interface contracts.
3. Epic 6 — IRS transcript request/import + consent.
4. Epic 6 — governed AI document classification (replace the inert port).
5. Epic 6 — AI extraction of tax facts / OCR.
6. Epic 6 — AI meeting prep, client briefs, relationship-aware recommendations.
7. Seasonal capacity forecasting & workload balancing.
8. QuickBooks / revenue intelligence.
9. Additional custodians beyond Schwab; live acquisition adapters.
10. AssetMark and remaining historical import sources.
11. Bulk historical TaxDome/Drake data migration (resumable, reconciled).
12. Multi-mailbox Microsoft 365 (per-advisor accounts).
13. Real email/SMS/push notification delivery (providers are stubbed).
14. Event-table partitioning + retention/archival automation.
15. Materialized reporting/analytics warehouse.
16. Mobile-optimized / React front-end against the versioned APIs.
17. Configurable, browser-based template authoring (letters/organizers).
18. Advanced compound-condition questionnaire builder.
19. Firm-wide audit-log search UI (indexed by actor/entity).
20. Automated dependency/security scanning in CI.

---

## 17. Risks Remaining Before Production Deployment

- **High — Microsoft 365 plaintext tokens + no refresh (H10).** Live mailbox/
  drive credentials in plaintext; integration silently stops hourly. Security and
  reliability risk; must be fixed before any production reliance.
- **High — production identity not validated.** OIDC/MFA/session-device controls
  need a production-equivalent staff and portal walkthrough; the portal is not
  externally launchable (no login/reset/download).
- **Medium/High — performance at scale.** N+1 dashboards and missing indexes are
  benign at current data volumes but will surface before real firm scale,
  especially during tax season.
- **Medium — data durability.** Advisor notes in flat files are outside DB backup;
  no backup/restore rehearsal or DR evidence yet.
- **Medium — operational readiness.** No production observability, scheduler
  alerting, retention policy, or runbooks; the in-process scheduler duplicates
  work under multiple replicas (no leader election).
- **Medium — data integrity.** Free-text status/type columns without CHECK
  constraints allow silent drift; polymorphic `entity_type` values unvalidated.
- **Low/Medium — accessibility & pen-test.** No accessibility or penetration-test
  evidence yet (required 1.0 gates).
- **Low — dead code / confusion.** Orphaned filing provider, dead e-signature,
  unused Graph connector invite future misuse.

---

## 18. Production Deployment Checklist

Gate items (each needs an accountable owner and recorded evidence):

- [ ] Managed OIDC + MFA validated for staff and portal in a production-equivalent tenant.
- [ ] Microsoft 365 tokens encrypted at rest; refresh implemented; sync-health surfaced.
- [ ] Secrets management (no plaintext secrets/tokens; session secret required in all envs).
- [ ] CSRF hardening (token-based, not Origin-only fail-open).
- [ ] Performance/scale test at production-sized data; N+1 and missing indexes resolved.
- [ ] Migration timing/lock analysis at production volume; `CREATE INDEX CONCURRENTLY` plan.
- [ ] Backup and restore rehearsal; disaster-recovery runbook.
- [ ] Advisor notes and all client data inside the backed-up database.
- [ ] Production observability, scheduler leader-election, alerting, log retention.
- [ ] Accessibility (WCAG) review across staff and portal templates.
- [ ] Penetration test + dependency/security scan in CI.
- [ ] Portal launch gates: returning-user login, password-reset delivery, document download, rate limits, quarantine/scanning.
- [ ] Firm legal/tax approval of seeded client-facing content (letters/organizers/questionnaires).
- [ ] Data-retention, legal-hold, and transcript-authorization policies documented.
- [ ] Notification provider decision (email/SMS/push are stubbed today).
- [ ] One Alembic head; sequential upgrade from v0.9.x preserves production-equivalent data.
- [ ] Operational runbooks and on-call ownership defined.

---

## 19. Recommended Roadmap from Release 0.9.8 to Version 1.0

A pragmatic sequence balancing feature completion, the unscheduled hardening
debt, and the production gates:

1. **Release 0.9.9 — Platform Consolidation (Epic 7, part 1).** Fix H10 (M365
   token encryption + refresh + sync health); the highest-impact N+1/index fixes
   (H15–H20 batch 1); Microsoft Graph connector consolidation and dead-code
   removal; deployment-readiness scaffolding; move advisor notes into the DB.
   *Rationale:* retire the highest-risk security/reliability debt before adding
   more surface. See `docs/RELEASE_0.9.9_PLATFORM_CONSOLIDATION.md` and
   `docs/PRODUCTION_ARCHITECTURE.md`.
2. **Release 0.9.10 — Sprint 5.5 (Tax Exceptions).** Extensions, estimates,
   notices, amendments.
3. **Release 0.9.11 — Sprint 5.6 (Filing/Delivery/Compliance completion).** Wire
   the filing provider; retention/legal-hold; delivery packages; compliance
   evidence; resolve the dead e-signature module.
4. **Release 0.9.12 — Portal completion + production identity (Sprint 5.7 + gates).**
   Returning-user login, password-reset delivery, document download, MFA/rate
   limits — unblocking external portal launch.
5. **Release 0.9.13 — Sprint 5.8 (Tax Reporting & Capacity)** with SQL-side
   aggregation, plus Epic 7 part 2 (constraints/jsonb, API-envelope, schema
   consolidation, dead-code removal).
6. **Release 1.0 — Production Readiness.** Complete the §18 checklist: managed-OIDC/
   MFA validation, backup/restore rehearsal, production-scale performance, pen
   test, accessibility, observability, runbooks, and legal/content approval.

Epic 6 (provider/transcript integration, governed AI, forecasting) follows 1.0.

---

*Version 1.0 progress review. No application code was modified and nothing was
committed. Sprint 5.5 has not started.*
