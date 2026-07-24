# Client360 Platform Architecture

**Status:** Authoritative top-level architecture reference. Reflects the code as it exists
after **Phase D.44** on `release/0.13.0` (migration head `m4p5o6r7t8c9`, 877 routes, 159
seeded production capabilities). Phase documents (`docs/PHASE_D*.md`,
`docs/ADVISOR_WORKSPACE_ARCHITECTURE.md`, domain release docs) remain the historical,
phase-specific record and are not superseded.

A machine-readable companion, `docs/platform_architecture_manifest.yaml`, encodes the
verifiable facts here (route count, migration head, capabilities, module list, import
direction, schema registration). `tests/test_platform_architecture.py` validates the
manifest against the live code so this document cannot silently drift.

> This document separates **implemented architecture**, **known limitations**, **planned
> extension points**, and **prohibited patterns**. Anything under "Extension points" is *not*
> implemented. Client360 is **not** event-sourced and **not** an AI/LLM system, and has **no external
> message broker/queue**. Its internal event bus is the single **transactional outbox** (D.34 typed
> domain events flow over it — no second event table); workflow **orchestration** (D.33) is a
> deterministic coordination layer over existing services, not a generic engine.

---

## 1. Purpose and authority
This is the single top-level answer to: what domains exist, who owns each type of data, which
services are authoritative, which layers merely compose, what dependency directions are
allowed, which capabilities protect each domain, how scope is enforced, which domains emit
timeline events or create Advisor Work, what Annual Review and Business Owner Planning reuse,
what data is unavailable, and where future phases may extend without duplicating logic.

It is descriptive of current code, not aspirational. Where code does not support a principle,
the exception is documented rather than hidden.

## 2. Current platform overview
Client360 is a server-rendered (FastAPI + Jinja2) practice-management platform over SQLAlchemy
Core + PostgreSQL + Alembic, with capability-based RBAC, record-level scope, append-only audit
and domain ledgers, and deterministic (non-AI) advisor intelligence. Data flows from
authoritative **source domains** up through read-first **composition layers**:

```
                 Identity / Auth / Users / Principals / Capabilities / Record scope
                                          │
   ┌───────────────── source (authoritative) domains ─────────────────┐
   People · Households · Relationship entities/Organizations/Ownership ·
   Source contacts/links · Matching/Canonical merge · Accounts/Portfolio ·
   Tax · Retirement · Benefits · Insurance · Rule Catalog · Compliance ·
   Reviewer Authority · Advisor Work · Documents/Evidence · Exceptions/Ops ·
   Importers · Advisor Intelligence (deterministic producer)
   └───────────────────────────────────────────────────────────────────┘
                                          │  (read-only, capability-gated)
   ┌──────────────────── composition layers ──────────────────────────┐
   Client 360 / Meeting Workspace → Activity Timeline (projection) →
   Annual Review → Business Owner Planning
   └───────────────────────────────────────────────────────────────────┘
```

## 3. Architectural principles
Each verified against code for D.12A. ✅ = holds; ⚠️ = holds with a documented exception.

- ✅ **One authoritative owner per domain** (source-of-truth matrix, §5).
- ✅ **Composition layers consume, never duplicate** — Annual Review and Business Owner Planning
  add no business logic; they call owning services.
- ✅ **Server-side authorization** — `require_capability` dependencies + middleware; no
  client-side enforcement.
- ✅ **Scope-first reads** — services check `record_in_scope` before returning data.
- ✅ **Restricted ≠ missing** — e.g. EIN present-flag derives from ciphertext, not view
  permission.
- ✅ **No mutation during incidental rendering** — Business Owner Planning uses a *pure*
  ownership read (`list_person_business_ownership`) that never calls `ensure_person_entity`.
- ✅ **No fabricated history / calculations / relationships** — recommendations excluded from
  the timeline (no durable timestamp); owner comp / tax figures / valuation shown "Not
  available"; ownership never inferred from names/free text.
- ✅ **No hidden recommendation engine** — recommendations come only from `advisor_intelligence`
  (deterministic rules).
- ✅ **No hidden workflow engine** — Advisor Work is an explicit status set with an
  allowed-transition map; no automatic creation.
- ✅ **No second event table** — exactly one `timeline_events`; the Activity Timeline is a read
  projection over it plus existing domain ledgers.
- ✅ **Deterministic identifiers** — stable timeline event ids; deterministic recommendation ids.
- ✅ **Bounded queries / no uncontrolled N+1** — per-source caps, batched actor/owner name
  resolution.
- ✅ **Linear migrations** — single Alembic head (§21).
- ✅ **Additive reads belong to the owning service** — `person_work`, `person_reviews`,
  `business_engagements`, `business_policies`, `list_person_business_ownership` live on their
  owning services.
- ✅ **Regulatory approval stays inside authorized Compliance** — final approval double-gates on
  `compliance.review.decide` **and** a recorded Reviewer Authority.
- ⚠️ **Source-domain behavior stable under composition** — holds; the one nuance is that the
  pre-existing `organization_service.list_owned` performs an upsert side effect via
  `ensure_person_entity`, so composition layers use the new pure reads instead (documented in
  §17).

## 4. Domain map
Implemented domains (authoritative unless marked *composition*):

| # | Domain | Kind |
|---|--------|------|
| 1 | Identity / Authentication / Users / Principals | platform |
| 2 | Capabilities / Roles / Record assignments | platform |
| 3 | People (clients) | source |
| 4 | Households | source |
| 5 | Relationship entities / Organizations / Businesses | source |
| 6 | Ownership relationships | source |
| 7 | Source contacts / Source links | source |
| 8 | Matching / Canonical merge | source |
| 9 | Accounts / Portfolio | source |
| 10 | Tax (engagements/returns/intake/lifecycle/documents) | source |
| 11 | Retirement plans (benefit_* retirement line) | source |
| 12 | Employee Benefits | source |
| 13 | Insurance (life/annuity) | source |
| 14 | Advisor Intelligence | source (deterministic producer) |
| 15 | Rule Catalog | source |
| 16 | Compliance Review | source |
| 17 | Reviewer Authority | source |
| 18 | Advisor Work | source |
| 19 | Documents / Evidence | source |
| 20 | Exceptions / Operations | source |
| 21 | Importers (Schwab/AssetMark/Wealthbox/Dave Ramsey) + Microsoft 365 | integration |
| 22 | Audit log | platform |
| 23 | Client 360 / Meeting Workspace | **composition** |
| 24 | Activity Timeline projection | **composition** |
| 25 | Annual Review | **composition** |
| 26 | Business Owner Planning | **composition** |
| 27 | Notifications / Outbox | platform (dispatch infra) |
| 28 | Workflow instances (tax/practice ops) | source (task orchestration, not a generic engine) |
| 29 | Opportunity & Pipeline (business development) | source (authoritative sales pipeline — D.13) |
| 30 | Campaigns (marketing) | source (authoritative campaign domain — D.14) |
| 31 | Referral Sources (business development) | source (authoritative referral-partner domain — D.14) |
| 32 | Enterprise Analytics / KPI warehouse | **read-model** (owns no business data — D.15) |
| 33 | Documents / Knowledge Repository | source (authoritative artifact domain — extended in D.16) |
| 34 | Workflow Automation / Orchestration | source (process engine + D.17 orchestration layer) |
| 35 | Communications & Client Engagement | source (authoritative communication-metadata domain — D.18) |
| 36 | Scheduling & Meeting Management | source (authoritative scheduling-metadata domain — D.19) |
| 37 | Enterprise Operations (projects, tasks, capacity) | source (authoritative firm-operations domain — D.20) |
| 38 | Enterprise Reporting (dashboards, reports, BI) | **composition layer** (owns reporting metadata; composes Analytics; never a source of truth — D.21) |
| 39 | Enterprise Automation (jobs, schedules, runs) | source (authoritative execution-metadata domain; orchestration layer — D.22) |
| 40 | Data Governance (quality, lineage, MDM, retention) | source (authoritative governance-metadata domain; references canonical records — D.23) |
| 41 | Enterprise Integration (connectors, webhooks, API, events) | source (authoritative integration-metadata domain; reuses providers/outbox — D.24) |
| 42 | Enterprise Security (policies, providers, secrets, certificates, incidents, findings) | source (authoritative security-metadata domain; reuses auth/RBAC/crypto/audit — D.25) |
| 43 | Enterprise Observability (services, health, diagnostics, telemetry, alerts, reliability) | source (authoritative platform-operations-metadata domain; reuses health/scheduler/logging — D.26) |
| 44 | Enterprise Configuration (categories, items, features, editions, preferences, changes) | source (authoritative platform-configuration-metadata domain; reuses runtime config/env — D.27) |
| 45 | Runtime Configuration Engine (resolution, snapshots, cache, feature evaluation) | runtime evaluation layer (evaluates D.27 metadata deterministically; owns only immutable snapshots + ledger; never edits metadata — D.28) |
| 46 | Distributed Runtime Coordination (workers, generations, convergence) | runtime coordination layer (cluster-safe convergence over the transactional outbox; owns only worker/generation/coordination metadata; never evaluates or edits metadata — D.29) |
| 47 | Runtime Consumption (behavioral adoption of the runtime engine) | consumption layer (application behavior consumes the engine via a standardized behavior-preserving API; owns only the behavioral-migration registry; never evaluates — D.30) |
| 48 | Runtime Authority & Governance (authoritative behavior, legacy retirement, metadata governance) | authority/governance layer (the engine is authoritative for migrated behavior via seeded D.27 metadata; validates runtime metadata; never evaluates or edits metadata — D.31) |
| 49 | Runtime Policy Engine (declarative business decisions, centralized decision services, policy governance) | policy layer (centralizes business decisions — eligibility/routing/gating/visibility — behind a declarative engine that **consumes `RuntimeContext`**; the runtime engine remains the sole evaluator; policies never bypass RBAC; a governed policy registry — D.32) |
| 50 | Workflow Orchestration Engine (declarative workflows, deterministic state management, replay & simulation, governance) | orchestration layer (centralizes multi-stage process coordination behind a declarative, deterministic engine that **consumes `RuntimeContext`** for behavior and the **Runtime Policy Engine** for routing; the runtime engine stays the sole evaluator, the policy engine the sole decision engine; coordinates existing services, never duplicating domain behavior; deterministic replay + dry-run simulation; a governed workflow registry — D.33) |
| 51 | Enterprise Domain Event Model (typed contracts, versioning, publishing, governance, diagnostics) | event-model layer (a typed, versioned, governed domain-event model **over the existing transactional outbox** — the sole bus; **no second event table**; producers publish contract-validated, **references-only** envelopes; orchestration + the major business domains publish domain FACTS; reuses the outbox delivery guarantees / dead-letter / envelope versioning; a governed contract + subscription registry with producer-adoption governance — D.34, producer adoption across 11 business domains D.35) |
| 52 | Read Models & Projection Engine (disposable read models, projection framework, rebuild/replay, governance) | read-model layer (consumes the D.34/D.35 domain events from the outbox to build fast, query-optimized, **disposable** read models — 12 `rm_*` tables; the write side stays the sole authoritative mutation layer; **no CQRS write model / no second event log / no event sourcing / no shadow state**; read models contain no business logic and never read authoritative tables; replay rebuilds them deterministically — D.36) |
| 53 | Read Surface Adoption (adopt projections into read surfaces, graceful fallback, adoption governance) | read-model layer (12 read surfaces consult the projections via a read-only helper before the authoritative read; a projection is served ONLY when healthy + fresh AND on the firm-wide `record.read_all` path — scoped principals always get the authoritative scoped read, so **RBAC is never bypassed**; every adopted read **falls back to the unchanged authoritative read**, so behavior is unchanged until an operator enables + rebuilds; **READS ONLY** — writes stay authoritative, the outbox stays the sole bus; no CQRS/second log/shadow logic — D.37) |
| 54 | Advisor Workspace Home (personalized, projection-backed advisor home; widget grid + presets + AI-ready summaries) | advisor-experience layer (extends `/workspace` with a 12-widget grid — reorder/hide/pin/saved presets — plus a TODAY summary, a deterministic PRIORITIES view, and five AI-ready summary models; count widgets read the D.37 projection-backed sources with authoritative fallback; personalization is **view state only** in `workspace_preferences`/`workspace_presets`, self-service, gated by `workspace.personalize`; every widget is capability-gated — never shown-then-403 — and RBAC/record-scope is never bypassed; no business mutation, the outbox stays the sole bus — D.38) |
| 55 | Unified Work Queue (cross-domain composition surface at `/work`; adapters + normalized UnifiedWorkItem + action dispatch + saved views + diagnostics/governance) | work-execution layer (`GET /work` composes actionable work from 10 authoritative services — tasks/workflow/exceptions via the existing `work_management.work_items`, plus advisor-work/compliance/documents/tax/insurance/opportunity/meeting adapters — into a normalized, references-only UnifiedWorkItem; **not** a second task/workflow/exception/assignment engine and never the source of truth; every action **delegates** to the authoritative owning service (which scopes + audits + publishes to the outbox); **no new projection** — counts reuse the D.37 adoption fallback, never reading `rm_*` directly; deterministic sort, built-in + per-user saved views (presentation state only, `work_queue.saved_views`), constrained bulk (claim/assign/acknowledge, per-item, honest partial results); RBAC/record-scope preserved, adapters fail closed; workspace widgets deep-link into filtered views via a shared summary — D.39) |
| 56 | Client 360 Workspace (master client record at `/client/{id}`; 12-section composition + snapshot + relationship graph + deep-link quick actions + diagnostics/governance) | client-record layer (`GET /client/{id}` composes a person/household's full picture — summary, financial, tax, insurance, benefits, opportunities, documents, meetings, compliance, activity timeline, relationships — read-only from the authoritative services; **not** a second client database and never the source of truth; record scope is verified ONCE at the boundary (404 out of scope) then sections fan out, each capability-gated (never shown-then-403) + fail-closed; the workspace **never mutates** — every edit deep-links into the authoritative create workflow; financial figures reuse the single `aggregate_portfolio` math and are presented **side by side, never summed**; unmodelled concepts (banking/retirement/outside-assets/liabilities/net-worth, status/tier/risk) are surfaced as "not tracked", never fabricated; **no migration, no new table, no new projection, no new capability**; a compact snapshot (+AI-ready JSON) + read-only relationship graph — D.40) |
| 57 | Household 360 Workspace (upgrades `/client/household/{id}`; household context + member directory + member-level rollups + household relationship graph + snapshot + diagnostics/governance) | client-record layer (`GET /client/household/{id}` composes a household's combined operational picture — member directory, financial/tax/insurance/benefits/opportunities/documents/meetings/compliance/work rollups by member, a deduped household timeline, a cycle-protected relationship graph — read-only; **not** a second household database, no shadow record, no duplicate person model; record scope verified ONCE at the household boundary (404) then members gated by `accessible_person_ids` (household-inheriting, team-aware) — out-of-scope members **suppressed (fail closed)**; the household total **reuses the single `get_household_portfolio` aggregation** (never re-summed) and incompatible figures are **never summed** — no fabricated net worth, no inferred filing/dependency; household work **reuses D.39** `compose_queue`; the workspace **never mutates** — quick actions deep-link into the authoritative workflow; reciprocal person↔household nav; **no migration/table/projection/capability** — D.41) |
| 58 | Advisor AI Assist (grounded, READ-ONLY briefing intelligence; context service + registry + deterministic offline provider + grounding/citations + refusals + diagnostics/governance) | advisor-experience layer (`GET /workspace/assist` + client/household/meeting **briefs** + work **explanation** + bounded factual **Q&A**; consumes the D.38–D.41 scope-guarded summaries — never re-queries domains, never reads `rm_*`; may **summarize/explain/compare/navigate only** — never creates/updates/deletes/approves/assigns/files/submits/sends/completes; every proposed action is a **deep link**; NEVER mutates, NEVER writes any DB (not even audit), NEVER publishes to the outbox; every fact is a **GroundedFact** with a class + internal **citations** + **limitations**, required fields never omitted; labelled **"Advisor Assist — Review Required"**; **refuses** regulated requests (trade/tax/legal/compliance/suitability/autonomous); no LLM infra exists → a **deterministic offline provider** (CI-safe) gated by `feature_enabled("advisor.ai_assist")`, failing closed to source facts; **no migration/table/projection/capability** — D.42) |
| 59 | Secure Client & Household Portal (hardens the EXISTING external portal; declarative visibility registry + runtime/compliance gates + consent ledger + masked financial summary + offline identity provider + diagnostics/governance + appointment-request delegation + internal admin) | client-experience layer (external `/portal/*` surfaces + internal `/admin/client-portal/*`; a **governed external composition + delegated-action surface** over the existing `app/portal/` — **not** a second CRM/identity/document/messaging/scheduler/task/workflow/policy engine or event bus; a **declarative field-level visibility registry** is the sole source of external-exposure decisions and NEVER exposes internal notes/assignments/compliance reasoning/audit/advisor work/AI briefs/work-queue/net worth; every external capability is gated through the Runtime Engine **OFF by default** with no env fallback, and external production access is **BLOCKED** until a compliance sign-off gate (`portal.production_signed_off`) is recorded; grant-based scope (`portal_scope`, never `record.read_all`, household access ≠ every member, fail closed); account numbers **masked** to last-4; consent/electronic-delivery ledger (**one** new table `portal_consents`); activation via external IdP (deterministic offline provider registered non-production only, never auto-links by email); every mutation **delegates** to the authoritative owner; internal admin is capability-guarded + record-scoped with **no impersonation** and never returns the activation token; **no new outbox contract, no new RBAC capability**, single migration head `m4p5o6r7t8c9` — D.43) |
| 60 | Unified Communications & Client Engagement (governed composition over the authoritative communication subsystems; declarative interaction registry + classifier + normalized interaction model + read-only adapters + engagement timeline/search/summary + Client 360 / Household 360 sections + AI grounding + analytics/diagnostics/governance) | client-experience layer (`/engagement` + `/api/v1/engagement/*` + portal `/portal/engagement`; one interaction history across every channel — secure messages, staff communications, email, appointments, documents, requests, signatures, workflow milestones, notes, notifications — **without** a second messaging/notification/timeline/document/scheduling/audit/event system and **without copying content** (previews are derived snippets, bodies stay in the source); a **declarative interaction registry** is the single catalog + the classifier mapping authoritative timeline `(source, event_type)` onto governed interaction types; the advisor spine **delegates to the authoritative `activity_timeline` projection** (record-scoped, deduped, redacted) and classifies it — not a second timeline; the client spine reuses the D.43 portal scoped reads; every interaction stays **owned by its authoritative subsystem**, read live, with a **deep link** (never inline mutation); reads gated through the Runtime Engine (no env fallback), external portal timeline **OFF by default**; AI Assist grounds on the composed summary (**counts only**); **no migration/table/capability/outbox contract** — D.44) |

## 5. Source-of-truth matrix
"Mutation from composition layer?" is **No** for every source datum — composition layers link
to the owning service instead.

| Data / responsibility | Authoritative domain | Authoritative service | Key tables | Primary capabilities | Scope | Composition consumers | Limitations |
|---|---|---|---|---|---|---|---|
| Client identity | People | `people` service | `people`, `source_contacts`, `person_source_links` | `client.read/write` | person | Client360, Annual Review, Business Owner | — |
| Household membership | Households | households routes/service | `households`, `household_relationships` | `client.read/write` | household | Client360, Business Owner | historical membership windows not modeled |
| Business identity | Organizations | `organization_service` | `relationship_entities`(business), `organization_profiles` | `organization.read/write`, EIN via `benefits.sensitive.read` | organization | Business Owner | no state_of_formation / formation dates |
| Business ownership | Organizations/Relationships | `organization_service` | `relationships`, `relationship_ownership` | `organization.read/write` | organization + validated person relationship | Business Owner | free-text ownership_type; unique-edge prevents cross-source conflict rows |
| Accounts | Accounts/Portfolio | `portfolio` | `accounts`, `account_registrations`, `account_holdings` | `client.read` | person/household | Client360, Annual Review, Business Owner (snapshot) | no business↔account link |
| Portfolio values | Portfolio | `portfolio.get_person_portfolio` | holdings/positions | `client.read` | person/household | Client360, Annual Review | current values only |
| Tax engagement metadata | Tax | `tax_domain` | `tax_engagements`, `tax_engagement_returns` | `tax.read/write/review/...` | office/team + subject | Business Owner (`business_engagements`) | — |
| Tax-return financial content | — | — | — | — | — | — | **Not currently modeled** |
| Retirement plans | Benefits (retirement line) | `benefits_domain` | `benefit_plans`, `benefit_retirement_plan_details` | `benefits.read/write` | organization | Business Owner | contribution/limit amounts not modeled |
| Employee benefits | Benefits | `benefits_domain` | `benefit_*` | `benefits.read/write/enroll/compliance`, `benefits.sensitive.read` | organization | Business Owner | employer-contribution amounts not modeled |
| Insurance policies | Insurance | `insurance` | `insurance_policies`, `insurance_policy_parties` | `insurance.read/write`, `insurance.sensitive.read` | insurance record scope | Business Owner (`business_policies`) | life/annuity only |
| Insurance policy purpose | — | — | — | — | — | — | **Not currently modeled** |
| Succession planning | Business Owner Planning | `business_owner` | `business_planning_profiles` | `business_owner.planning_update` | person + validated business | (owns) | prospective only, no legal validation |
| Advisor Intelligence recommendations | Advisor Intelligence | `advisor_intelligence` | none (deterministic, in-memory) | `client.read` | person | Annual Review, Business Owner, Advisor Work, Compliance | no durable timestamp |
| Advisor Work | Advisor Work | `advisor_work` | `advisor_work_items`, `advisor_work_events` | `advisor_work.read/create/assign/update` | person/household (book) | Annual Review, Business Owner | no business link on items |
| Compliance reviews | Compliance | `compliance/reviews` | `compliance_reviews`, `compliance_decisions` | `compliance.review.*` | person/household | Annual Review, Business Owner (counts) | no business link |
| Reviewer authority | Reviewer Authority | `compliance` (authority) | `reviewer_authorities`, `reviewer_authority_events` | `compliance.authority.read/manage` | firm governance | Compliance | — |
| Timeline | Activity Timeline | `activity_timeline` | projects `timeline_events` + ledgers | `timeline.read` | person/household | Annual Review, Business Owner | missing actors on older rows |
| Annual-review sessions | Annual Review | `annual_review` | `annual_review_sessions` | `annual_review.read/create/update` | person | Business Owner (link) | — |
| Business-planning profiles | Business Owner Planning | `business_owner` | `business_planning_profiles` | `business_owner.planning_update` | person + validated business | (owns) | controlled-vocab statuses |
| Opportunities / sales pipeline | Opportunity | `opportunity.service` | `opportunities`, `opportunity_stages`, `opportunity_events`, `opportunity_activities`, `opportunity_work_links`, `opportunity_attributions` | `opportunity.view/edit/delete/assign/close/report/forecast` | advisor-book + target-client record scope | Annual Review, Business Owner Planning (read-only) | references People/Orgs/campaigns/referral-sources, never owns them |
| Campaigns / marketing | Campaign | `campaign.service` | `campaigns`, `campaign_events`, `campaign_activities`, `campaign_documents` | `campaign.view/edit/delete/report/archive/manage_budget/manage_roi` | firm assets (capability-gated); revenue scoped by pipeline | Business Development dashboard | budget/ROI sensitive; performance computed from attributed opportunities |
| Referral sources | Referral | `referral.service` | `referral_sources`, `referral_source_advisors`, `referral_source_events` | `referral.view/edit/delete/report` | advisor-book scope | Business Development dashboard | metrics computed (never stored) |
| Analytics KPIs / scorecards | Analytics (**read-model**) | `analytics.*` services | `analytics_targets`, `analytics_snapshots`, `analytics_dashboards`, `analytics_dashboard_widgets` | `analytics.view/executive/export/manage_targets/manage_dashboards` | book-scope via `accessible_person_ids`; firm-wide needs `analytics.executive` | (top of stack) | **owns no business data**; metrics computed on read; snapshots prospective (no backfill) |
| Documents / artifacts | Documents (authoritative) | `documents.py` + `document_platform.*` | `documents` (extended), `document_versions`, `document_folders`, `document_relationships`, `document_retention_policies`, `document_events` | `document.read/write` (legacy) + `documents.*` (platform) | person/household/organization + relationship + record scope | Annual Review, Business Owner, Opportunity, Campaign, Referral, Compliance (read-only); Analytics (stats) | every domain references documents; never duplicated |
| Audit records | Audit | `security.audit` | `audit_events` (append-only) | `audit.read` | firm (sensitive) | — | — |

## 6. Dependency architecture
Verified from actual imports (D.12A audit). Allowed direction is **consumer → producer**;
producers never import consumers or composition layers.

```
composition:  business_owner ─┐
                              ├─► annual_review ─► advisor_workspace / activity_timeline
              business_owner ─┼─► organization_service, tax_domain, benefits_domain,
                              │    insurance, compliance/reviews, advisor_work, activity_timeline
              annual_review ──┴─► advisor_intelligence, advisor_work, activity_timeline, compliance
producers:    advisor_work, annual_review, business_owner, compliance/{reviews,rule_catalog}
                                 └─► advisor_intelligence   (read recommendations)
```

Verified invariants (all clean):
- `advisor_intelligence` imports **none** of its consumers (advisor_work / annual_review /
  business_owner / activity_timeline).
- Source producers (`advisor_intelligence`, `advisor_work`, `compliance/reviews`) do **not**
  import `activity_timeline` — the Timeline adapters depend on source domains, not the reverse.
- No source domain imports `annual_review` or `business_owner`.
- `business_owner` sits at the top of the stack (imported by no service).
- Compliance logic stays in `compliance/*`; Benefits in `benefits_domain`; Tax in `tax_domain`;
  business ownership in `organization_service`.

No accidental circular or upward dependencies were found. The single intentional
composition-consumes-composition edge is `business_owner → annual_review` (higher layer reads
the latest review), which is downward and expected.

## 7. Composition layers
| Layer | Consumes | Owns / persists | Must not mutate | Mandatory capabilities | Source of truth? |
|---|---|---|---|---|---|
| Client 360 / Meeting Workspace | portfolio, insurance, tax, benefits, tasks, exceptions, timeline | nothing (meeting outcomes route to owning services) | any source datum | `client.read` (+ owning caps per panel) | No |
| Activity Timeline | `timeline_events`, `advisor_work_events`, `compliance_reviews/decisions` | nothing (pure projection, no table) | source rows | `timeline.read` + per-source caps for detail | No |
| Annual Review | Client360 brief, Advisor Intelligence, Advisor Work, Timeline, Compliance, portfolio | `annual_review_sessions` (advisor-activity: notes + checklist) | source domains | `annual_review.read/create/update` | No |
| Business Owner Planning | Organizations/ownership, Tax, Benefits, Insurance, Advisor Intelligence, Advisor Work, Timeline, Compliance, Annual Review | `business_planning_profiles` (succession/continuity facts) | source domains | `business_owner.read` (+ owning caps per section) | No |
| Enterprise Reporting (D.21) | Analytics (KPI read-model), Operations, Scheduling, Communications, Workflow, Advisor Work, Compliance, Opportunity, Campaign, Referral, Annual Review, Business Owner, Timeline | reporting definitions/config only (templates, definitions, dashboards/widgets, scorecards, KPI groups, saved views, schedules, export profiles, report runs) — **no KPI values** | any source datum; must not recalculate KPIs | `reporting.view/manage/templates/audit*/admin*` (KPI values via Analytics `compute_metric`, which enforces executive gating + scope) | No |

## 8. Identity and relationship model
Business entities are **not** a separate table: an organization is a `relationship_entities`
row (`entity_type='business'`) with a 1:1 `organization_profiles`. People and households are
promoted into `relationship_entities` (via `ensure_person_entity`/`ensure_household_entity`)
only when they participate in the relationship graph. Ownership is a `relationships` edge
(`owns`, category `ownership`) with a 1:1 `relationship_ownership` detail (percentages,
`is_direct`, evidence). The `relationships` table enforces a unique `(from, to, type)` edge.

## 9. Authorization and capability model
Capability-based RBAC. `Principal(user_id, email, display_name, capabilities: frozenset)` is
built per request from active role→capability grants. Routes gate with
`require_capability(code)` dependencies; middleware additionally maps route families to a
required capability with GET→`.read` / mutation→`.write` inference. **63 production
capabilities** are seeded across domain migrations (each migration inserts its capabilities and
grants them to roles). No single seed file; the capability table is the runtime source of
truth. (The shared test database may also contain ephemeral `e2_2.cap.*` fixtures — these are
test artifacts, not production capabilities.)

Capability inventory by domain (exact codes; `*` = sensitive):

- **Identity/admin:** `identity.manage*`, `role.manage*`, `team.manage`, `assignment.manage`,
  `record.read_all`, `record.write_all`, `audit.read*`.
- **Client:** `client.read`, `client.write`, `task.read`, `task.write`, `document.read`,
  `document.write`, `communication.read`, `communication.write`.
- **Work (legacy task/workflow):** `work.read`, `work.write`, `work.approve*`, `capacity.read*`.
- **Organization:** `organization.read`, `organization.write`.
- **Benefits/Retirement:** `benefits.read`, `benefits.write`, `benefits.enroll`,
  `benefits.compliance*`, `benefits.sensitive.read*`.
- **Insurance:** `insurance.read`, `insurance.write`, `insurance.suitability`, `insurance.scan`,
  `insurance.commissions.read`, `insurance.commissions.write`, `insurance.licensing.read`,
  `insurance.licensing.write`, `insurance.sensitive.read*`.
- **Tax:** `tax.read`, `tax.write`, `tax.review*`, `tax.deadline.manage*`, `tax.intake.read`,
  `tax.intake.write`, `tax.document.review*`.
- **Exceptions:** `exception.read`, `exception.write`, `exception.resolve*`,
  `exception.compliance*`.
- **Advisor Work:** `advisor_work.read`, `advisor_work.create`, `advisor_work.assign`,
  `advisor_work.update`.
- **Compliance / Reviewer Authority:** `compliance.review.read`, `compliance.review.submit`,
  `compliance.review.assign*`, `compliance.review.decide*`, `compliance.authority.read`,
  `compliance.authority.manage*`.
- **Timeline:** `timeline.read`.
- **Annual Review:** `annual_review.read`, `annual_review.create`, `annual_review.update`.
- **Business Owner Planning:** `business_owner.read`, `business_owner.update`,
  `business_owner.planning_update`.
- **Opportunity & Pipeline:** `opportunity.view`, `opportunity.edit`, `opportunity.delete*`,
  `opportunity.assign`, `opportunity.close`, `opportunity.report`, `opportunity.forecast*`.
- **Campaigns:** `campaign.view`, `campaign.edit`, `campaign.delete`, `campaign.report`,
  `campaign.archive`, `campaign.manage_budget*`, `campaign.manage_roi*`.
- **Referral Sources:** `referral.view`, `referral.edit`, `referral.delete`, `referral.report`.
- **Analytics:** `analytics.view`, `analytics.executive*`, `analytics.export`,
  `analytics.manage_targets`, `analytics.manage_dashboards`.
- **Documents:** `document.read`/`document.write` (legacy) + `documents.view/edit/delete/version/
  approve/archive/restore/export/manage_retention` (D.16 platform).
- **Workflow:** `work.read/write/approve` (legacy engine) + `workflow.view/edit/execute/cancel/
  template_manage/admin*/audit*` (D.17 orchestration).
- **Communications:** `communications.view/send/manage_templates/audit*/admin*` (D.18 platform;
  distinct from the legacy `communication.read/write` capabilities that gate the Microsoft 365 UI).
- **Scheduling:** `scheduling.view/manage/templates/audit*/admin*` (D.19 platform; the Microsoft 365
  calendar sync/review UI remains gated by the legacy `communication.read`).
- **Operations:** `operations.view/manage/templates/audit*/admin*` (D.20 firm-operations platform;
  distinct from client `task.read/write` and from Advisor Work `advisor_work.*`, which remain the
  authoritative client-task and client-work domains).
- **Reporting:** `reporting.view/manage/templates/audit*/admin*` (D.21 composition layer; KPI values
  are composed from Analytics — executive gating (`analytics.executive`) and record scope are
  inherited from the Analytics compute layer, never re-implemented).
- **Automation:** `automation.view/manage/execute/audit*/admin*` (D.22 orchestration layer; jobs
  dispatch to existing services via the `job_type` map — never duplicating business logic; scheduled
  runs execute with a system principal; `automation.execute` gates triggering).
- **Governance:** `governance.view/manage/review*/audit*/admin*` (D.23 governance domain; references
  canonical records, reuses the matching/merge + document-retention infra, never performs an unsafe
  merge or hard delete; merge apply / legal holds / deletion approval require `governance.review`).
- **Integration:** `integration.view/manage/execute/audit*/admin*` (D.24 integration domain; reuses
  importers/M365-OAuth/outbox/Fernet, never duplicates provider logic, stores no plaintext secret,
  no external broker; sync/verify/publish require `integration.execute`).
- **Security:** `security.view/manage/execute/audit*/admin*` (D.25 security domain; owns security
  metadata only — policies, providers, secret/certificate references, incidents, findings — reuses
  the existing authentication/RBAC/record-scope/Fernet-crypto/audit, never replaces login/OAuth,
  never stores a plaintext secret; policy approval / secret rotation / certificate renewal / incident
  & exception decisions / running reviews require `security.execute`).
- **Observability:** `observability.view/manage/execute/audit*/admin*` (D.26 platform-operations
  domain; owns observability metadata only — services, health/diagnostic checks, telemetry, alerts,
  runtime snapshots, reliability incidents/findings — reuses the existing health endpoints, scheduler
  snapshot, logging, and notification ledger, never replaces runtime health/logging/exception
  handling, never delivers a notification; scans / snapshot capture / alert ack-resolve / service &
  incident lifecycle require `observability.execute`; sensitive diagnostic detail requires
  `observability.audit`).
- **Configuration:** `configuration.view/manage/execute/audit*/admin*` (D.27 platform-configuration
  domain; owns configuration governance metadata only — categories/sets/items/versions, environment
  overrides, tenant/org/user preferences, feature groups/flags/rollouts, editions/edition-capabilities/
  license-policies/edition-assignments, platform options, administrative policies, runtime-setting
  references, snapshots, changes — reuses the runtime config `app.config` (references it, never
  re-reads env or replaces it), references RBAC `capabilities` and `organization_profiles`/`users`,
  has no runtime feature-toggle engine; set/policy/change approval, feature activation, edition
  assignment, and reviews require `configuration.execute`; sensitive item values require
  `configuration.audit`).
- **Runtime:** `runtime.view/manage/execute/audit*/admin*` (D.28 Runtime Configuration Engine; the
  runtime evaluation layer over D.27 metadata — deterministic resolution precedence, immutable
  effective-config snapshots, an in-process versioned cache, and feature/edition/rollout evaluation.
  It evaluates only and never edits configuration metadata; hydration is guarded so a config failure
  never blocks startup; every request gets one immutable runtime context. Refresh / snapshot build /
  cache rebuild require `runtime.execute`; the safety report requires `runtime.audit`; emergency
  overrides require `runtime.admin`).
- **Runtime cluster:** `/runtime/cluster` reuses the D.28 `runtime.*` capabilities (D.29 distributed
  coordination — makes the runtime engine cluster-safe using the transactional outbox as the sole
  coordination bus; a worker registry + heartbeats, a runtime generation/version history, and
  pull-based convergence off the persisted generation as the single source of truth. The engine
  remains the sole evaluator; coordination never edits metadata; coordinated refresh requires
  `runtime.execute`; diagnostics/event-history require `runtime.audit`; worker administration &
  emergency synchronization require `runtime.admin`).
- **Runtime consumption:** `/runtime/behavior` reuses the D.28 `runtime.*` capabilities (D.30
  behavioral adoption — application behavior consumes the runtime engine through a standardized,
  behavior-preserving consumption API (`RuntimeContext.config/feature_enabled/edition/license/
  capabilities`, `app/services/runtime/consumption.py`). Migrated switches: automation dispatch,
  analytics executive metrics, benefits detector windows, reporting optional modules, notification
  channels, Microsoft 365 sync + SharePoint scope — each with a legacy default so behavior is
  unchanged until a runtime value is defined. Infrastructure (DB/secrets/OAuth/crypto/logging/
  scheduler-registration/M365 credentials) stays a startup concern. Adoption is tracked in the
  `runtime_behaviors` registry; recording a behavior migrated/retired requires `runtime.admin`).
- **Runtime authority & governance:** `/runtime/behavior/governance` reuses the D.28 `runtime.*`
  capabilities (D.31 — the engine is the **authoritative** source for migrated behaviors via seeded
  D.27 metadata (behavior-preserving); the fixed legacy fallbacks are retired to documented
  compatibility shims; two per-instance shims remain by policy. Governance validation checks the
  runtime metadata — missing/orphan/deprecated definitions, invalid edition mappings, orphan
  capabilities, definition coverage; the report requires `runtime.audit`, running validation requires
  `runtime.admin`. Current: 100% adoption, 71.4% runtime authority, 100% definition coverage).
- **Runtime policy:** `/runtime/policy` reuses the D.28 `runtime.*` capabilities (D.32 — the Runtime
  Policy Engine centralizes business decisions (eligibility/routing/gating/visibility) behind a
  declarative, governed policy registry. Every policy **consumes `RuntimeContext`** (the runtime engine
  remains the sole evaluator) and never bypasses RBAC — capability/scope enforcement stays at the call
  site. Nine policies are evaluated by the engine (their call sites rewired through it, behavior-
  preserving); four are registered `in_domain` (compliance approval / the frozen F5.5 notification
  module / deterministic document & scheduling behavior — enforcement stays in the owning domain).
  Registry/graph reads require `runtime.view`; the governance report + diagnostics + events require
  `runtime.audit`; running validation requires `runtime.admin`. Current: 100% decision-area coverage,
  100% adoption, 100% definition coverage, 0 governance issues).
- **Workflow orchestration:** `/orchestration` reuses the D.17 `workflow.*` capabilities (D.33 — the
  Workflow Orchestration Engine centralizes multi-stage process coordination behind a declarative,
  deterministic engine (seven canonical states pending/active/waiting/completed/cancelled/failed/
  compensated). It **consumes `RuntimeContext`** for behavior and the **Runtime Policy Engine** for
  routing (a transition may declare a policy) — the runtime engine stays the sole evaluator, the policy
  engine the sole decision engine — and coordinates existing services, never duplicating domain
  behavior. Two definitions are engine-driven (`automation.dispatch`, `workflow.review`, their call
  sites coordinated through the engine, behavior-preserving); thirteen mature domain lifecycles are
  registered `in_domain` (the workflow-template engine, compliance approval, operations/scheduling/
  advisor/tax/exception/campaign/document/communications/notification — authoritative in-domain).
  Deterministic replay + dry-run simulation are pure reads that never mutate production state.
  Registry/instances/diagnostics require `workflow.view`; governance/replay require `workflow.audit`;
  simulation requires `workflow.execute`; running validation requires `workflow.admin`. Current: 100%
  domain coverage, 100% adoption, 0 governance issues).
- **Domain events:** `/events` reuses the D.26 `observability.*` capabilities (D.34 — the Enterprise
  Domain Event Model: a typed, versioned, governed domain-event layer **over the existing transactional
  outbox** (the sole internal event bus). It adds **no second event table** — a domain event is a
  contract-validated `Envelope` (`app/platform/events.py`) written to `outbox_events`; delivery
  guarantees, idempotency, dead-letter, and envelope versioning are reused, not rebuilt. Producers
  publish through a standardized API validated against a typed contract; the orchestration engine
  publishes `orchestration.lifecycle` (additive, best-effort, dark-launched). Governance validates the
  model (unregistered/orphan contracts, orphan subscriptions, producers without consumers, schema
  violations, version drift, deprecated references). Contract/subscription reads require
  `observability.view`; governance/diagnostics/dead-letters/replay require `observability.audit`;
  running validation requires `observability.execute`. Current: 5 contracts across 3 domains, 100%
  domain/consumer/producer coverage, 0 governance issues).
  **(D.35 producer adoption)** the major business domains publish typed, past-tense, **references-only**
  domain FACTS at their authoritative write boundaries (people/households, opportunity/referral,
  operations, exceptions, documents, compliance, tax, insurance, benefits — 31 contracts across 11
  domains). Publishing is additive + behavior-preserving (after the mutation, transactional where a
  `conn` is available, via `publish_safe`; consumers dark-launched, so behavior is unchanged), payloads
  are references-only (a payload-safety layer rejects PII/secrets/financials/health/tax/document
  contents at publish time + in governance), and authoritative/regulatory ledgers are unchanged (events
  are added after the write; consumers never become sources of truth). Governance additionally detects
  producer-without-publishing-site, unregistered-publish-site, sensitive-field violations, duplicate
  semantic contracts, and deprecated-contract-published. Producer-adoption diagnostics at
  `GET /events/producers`. Current: 100% producer adoption, 0 stale producers, 0 governance issues).
- **Read models / projections:** `/projections` reuses the D.26 `observability.*` capabilities (D.36 —
  the Read Models & Projection Engine consumes the D.34/D.35 domain events from the outbox to build
  fast, query-optimized, **disposable** read models (12 `rm_*` tables). It changes no business behavior:
  the domain services remain the sole authoritative mutation layer and the outbox stays authoritative;
  read models hold no business logic/state, never read authoritative tables, and are rebuildable
  deterministically from events (replay). No CQRS write model, no second event log, no event sourcing.
  Full rebuild / incremental / reset / replay / validate; per-event failure isolation; the incremental
  tick is dark-launched (`PROJECTIONS_ENABLED`, off by default). Registry/health/diagnostics require
  `observability.view`; the governance report + full diagnostics require `observability.audit`;
  rebuild/reset/replay require `observability.execute`. Current: 12 projections, 100% event coverage,
  0 governance issues).
- **Read-surface adoption:** `GET /projections/adoption` (reuses `observability.audit`) reports the D.37
  adoption of projections into 12 read surfaces (Activity Timeline, Opportunity Pipeline, Compliance
  Queue, Tax/Insurance/Benefits dashboards, Operational Task Lists, Exception Dashboard, Project/Document
  dashboards, Household/People summary). Each adopted read consults `projections/adoption.py` first and
  is served from the projection ONLY when it is healthy + fresh (lag ≤ 100) AND on the firm-wide
  (`record.read_all`) path — a record-scoped principal always gets the authoritative scoped read, so RBAC
  is never bypassed; every adopted read **falls back to the unchanged authoritative read** otherwise, so
  behavior is unchanged until an operator enables + rebuilds. READS ONLY (writes stay authoritative);
  adoption governance detects unused/unadopted/mixed/bypass/duplicate/stale (currently 0 issues). See
  `docs/READ_SURFACE_ADOPTION.md`, `docs/PROJECTION_USAGE_GUIDE.md`, `docs/READ_OPTIMIZATION.md`, ADR-042.
- **Advisor Workspace home:** `/workspace` (gated by `client.read`) is the personalized advisor home
  (D.38, extending D.1–D.12). It renders a greeting, a TODAY summary, a deterministic PRIORITIES view,
  and a **12-widget grid** (Today's Calendar, Active Clients, Workflow Exceptions, Operational Tasks,
  Recent Activity, Revenue Pipeline, Compliance Queue, Tax / Insurance / Benefits pipelines, Document
  Review, Team Workload). Count widgets read the D.37 projection-backed sources (projection when
  healthy+fresh on the firm-wide path, else authoritative scoped fallback); every widget is
  capability-gated (never shown-then-403) and a widget that errors is isolated. **Personalization is
  view state only** — `workspace_preferences` (order/hidden/pinned/filters, one row per user) +
  `workspace_presets` (named saved layouts); self-service, gated by `workspace.personalize`; reorder /
  hide / pin / reset / presets are POST-form (POST-redirect-GET, no JS framework) at
  `/workspace/customize|presets|reset`. Five **AI-ready summary models** (Daily Brief, Client Snapshot,
  Meeting Prep, Opportunity Summary, Compliance Summary) are exposed as JSON at `/workspace/summaries/*`
  (record-scope enforced). No business mutation; the authoritative services + outbox are untouched. See
  `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md`, ADR-043.
- **Unified Work Queue:** `GET /work` (D.39, `work.read`) is the cross-domain execution surface — a
  read-only COMPOSITION over 10 authoritative work services (tasks/workflow/exceptions via
  `work_management.work_items`; advisor-work/compliance/documents/tax/insurance/opportunity/meeting
  adapters). Items are normalized into a references-only UnifiedWorkItem (source status preserved;
  `status_group`/`sla_state` are display-only). It is **not** a second task/workflow/exception/assignment
  engine: every action delegates via `work_queue.dispatch` to the authoritative owning service (which
  scopes + audits + publishes to the outbox); assignment reuses `work_management.assign_work`.
  Deterministic sort (overdue → SLA-breached → priority → due → age → key), built-in + per-user saved
  views (`work_queue_saved_views`/`work_queue_preferences`, presentation state only, `work_queue.saved_views`),
  constrained bulk (claim/assign/acknowledge, per-item, honest partial results). No new projection —
  counts reuse the D.37 adoption fallback (never reading `rm_*` directly); adapters fail closed; RBAC/
  record-scope preserved. `POST /work/action|bulk-action|views*`, `GET /work/summary` (AI-ready),
  `GET /work/diagnostics` (`observability.audit`, + governance). See `docs/UNIFIED_WORK_QUEUE.md`,
  `docs/WORK_QUEUE_ADAPTER_GUIDE.md`, `docs/WORK_QUEUE_ACTIONS.md`, `docs/WORK_QUEUE_GOVERNANCE.md`, ADR-044.
- **Client 360 Workspace:** `GET /client/{id}` (D.40, `client.read`) is the master client record — a
  read-only COMPOSITION of 12 domain sections (summary, financial, tax, insurance, benefits,
  opportunities, documents, meetings, compliance, activity timeline, relationships, work) for a person
  or household. **Not** a second client database: record scope is verified ONCE at the boundary (404 out
  of scope), then sections fan out, each capability-gated (never shown-then-403) + fail-closed; the
  workspace **never mutates** — the 9 quick actions (Schedule Meeting, Upload Document, Add Note, Create
  Task, Start Tax Return, Create Opportunity, Start Insurance Case, Send Secure Message, Generate Meeting
  Prep) deep-link into the authoritative create workflow. Financial reuses the single `aggregate_portfolio`
  math (side by side, **never summed**); unmodelled concepts (banking/retirement/outside-assets/
  liabilities/net-worth, status/tier/risk) are "not tracked", never fabricated. A compact snapshot
  (`/client/{id}/snapshot`, AI-ready JSON) + a read-only relationship graph (family/business/
  professional/estate). `GET /client/{id}/diagnostics` (`observability.audit`) reports composition
  timings + governance. No migration, no new table/projection/capability. See
  `docs/CLIENT360_WORKSPACE.md`, `docs/CLIENT360_WORKSPACE_ADAPTERS.md`, `docs/CLIENT360_WORKSPACE_ACTIONS.md`,
  `docs/CLIENT360_WORKSPACE_GOVERNANCE.md`, ADR-045.
- **Household 360 Workspace:** `GET /client/household/{id}` (D.41, `client.read`) upgrades the household
  path into a full workspace: household context, a first-class member directory (each member deep-links
  to `/client/{person_id}`), member-level rollups (financial/tax/insurance/benefits/opportunities/
  documents/meetings/compliance/work), a deduped household activity timeline, and a cycle-protected
  household relationship graph. Record scope is verified ONCE at the household boundary (404); members
  are gated by `accessible_person_ids` (household-inheriting, team-aware) — out-of-scope members are
  suppressed (fail closed). The household portfolio total **reuses the single `get_household_portfolio`
  aggregation** (never re-summed); insurance/opportunity/benefit/tax figures are **never summed** into
  assets; banking/retirement/liabilities/net-worth are "not tracked" (never fabricated). Household work
  **reuses D.39** `compose_queue(filters={household_id})`; the household timeline **reuses
  `household_timeline`** (dedups by event_id). The workspace **never mutates** — quick actions deep-link
  into the authoritative create workflow; person↔household navigation is reciprocal.
  `GET /client/household/{id}/snapshot` (AI-ready JSON) + `/diagnostics` (`observability.audit`, +
  governance). No migration/table/projection/capability. See `docs/HOUSEHOLD360_WORKSPACE.md`,
  `docs/HOUSEHOLD360_WORKSPACE_ADAPTERS.md`, `docs/HOUSEHOLD360_WORKSPACE_ACTIONS.md`,
  `docs/HOUSEHOLD360_WORKSPACE_GOVERNANCE.md`, ADR-046.
- **Advisor AI Assist:** `GET /workspace/assist` (D.42, `client.read`) is a governed, **read-only**
  briefing surface that consumes the D.38–D.41 scope-guarded summaries (Advisor Workspace daily brief,
  Unified Work Queue summary, Client 360 / Household 360 snapshots, minimized meeting brief) — it never
  re-queries domains and never reads `rm_*`. Capabilities: Daily Advisor Brief, Client Brief, Household
  Brief, Meeting Prep, Work Explanation, and bounded Factual Q&A (a closed registry). It may
  **summarize/explain/compare/navigate only** — every proposed action is a **deep link**; it **never
  mutates, never writes any database (not even audit), never publishes to the outbox**. Every fact is a
  **GroundedFact** (confirmed / derived / model-summary / missing-untracked) with internal **citations**
  + **limitations**; required safety/provenance fields can never be omitted; every response is labelled
  **"Advisor Assist — Review Required"**. Regulated requests (trade / tax / legal / compliance /
  suitability / autonomous) are **refused**. No LLM infra exists → a **deterministic offline
  `LocalProvider`** (CI-safe) is the default, gated by `runtime.consumption.feature_enabled("advisor.ai_assist")`,
  **failing closed** to deterministic source facts on disable/timeout/failure/malformed. Sensitive fields
  (note bodies, contact PII, account numbers) are excluded; only in-process aggregate counters are
  recorded (no DB write). Routes: `/workspace/assist`, `POST /workspace/assist/query` (read-only),
  `/workspace/assist/diagnostics` (`observability.audit`), `/client/{id}/brief`,
  `/client/household/{id}/brief`, `/workspace/meetings/{id}/brief`, `/work/{type}/{id}/explain`. No
  migration/table/projection/capability. See `docs/ADVISOR_AI_ASSIST.md`,
  `docs/AI_ASSIST_CONTEXT_CONTRACT.md`, `docs/AI_ASSIST_PROMPT_GOVERNANCE.md`, `docs/AI_ASSIST_SECURITY.md`,
  `docs/AI_ASSIST_PROVIDER_GUIDE.md`, ADR-047.
- **Secure Client & Household Portal:** external `/portal/*` + internal `/admin/client-portal/*` (D.43) is a
  governed external composition + delegated-action surface over the **existing** `app/portal/` — not a
  second system. A **declarative field-level visibility registry** (`app/portal/visibility.py`) is the sole
  source of external-exposure decisions and never exposes internal notes/assignments/compliance
  reasoning/audit/advisor work/AI briefs/work-queue/net worth. Every external capability is gated through
  the Runtime Engine **OFF by default** (no env fallback); external **production access is BLOCKED** until a
  compliance sign-off gate (`portal.production_signed_off`) is recorded (see
  `docs/CLIENT_PORTAL_COMPLIANCE_GATE.md`). Grant-based scope (`portal_scope`, never `record.read_all`,
  household ≠ every member, fail closed); account numbers **masked** to last-4; a consent/electronic-delivery
  ledger (**one** new table `portal_consents`, migration `m4p5o6r7t8c9`); activation via an external IdP
  (deterministic offline provider registered non-production only, never auto-links by email); every mutation
  **delegates** to the authoritative owner; internal admin is capability-guarded + record-scoped with **no
  impersonation** and never returns the activation token. **No new outbox contract, no new RBAC capability.**
  See `docs/CLIENT_PORTAL_ARCHITECTURE.md`, `docs/CLIENT_PORTAL_SECURITY.md`,
  `docs/CLIENT_PORTAL_IDENTITY_AND_SCOPE.md`, `docs/CLIENT_PORTAL_VISIBILITY_REGISTRY.md`,
  `docs/CLIENT_PORTAL_DOCUMENTS.md`, `docs/CLIENT_PORTAL_REQUESTS.md`, `docs/CLIENT_PORTAL_MESSAGING.md`,
  `docs/CLIENT_PORTAL_OPERATIONS.md`, `docs/CLIENT_PORTAL_GOVERNANCE.md`,
  `docs/CLIENT_PORTAL_COMPLIANCE_GATE.md`, ADR-048.
- **Unified Communications & Client Engagement:** `/engagement` + `/api/v1/engagement/*` + portal
  `/portal/engagement` (D.44) is a governed **composition** over the authoritative communication subsystems
  — one interaction history across every channel with **no second messaging/notification/timeline/document/
  scheduling/audit/event system** and **no copied content**. A **declarative interaction registry**
  (`app/services/communications/engagement/registry.py`) is the single catalog and the classifier mapping
  authoritative timeline `(source, event_type)` onto governed interaction types. The advisor spine
  **delegates to the authoritative `activity_timeline` projection** (record-scoped, deduped, redacted) and
  classifies it — not a second timeline; the client spine reuses the D.43 portal scoped reads. Every
  interaction stays owned by its subsystem, read live, with a **deep link** (never inline mutation). Reads
  are gated through the Runtime Engine (no env fallback); the external portal timeline is OFF by default.
  Client 360 + Household 360 gain a **Communications** section; AI Assist grounds on the composed summary
  (**counts only**, no bodies); three low-cardinality analytics metrics + internal diagnostics + a
  governance checker enforce the invariants. **No migration/table/capability/outbox contract.** See
  `docs/COMMUNICATION_ARCHITECTURE.md`, `docs/ENGAGEMENT_TIMELINE.md`, `docs/COMMUNICATION_REGISTRY.md`,
  `docs/COMMUNICATION_GOVERNANCE.md`, ADR-049.

Role seeding (as currently seeded; `administrator` holds all): advisor gets client/work/
advisor_work/annual_review/business_owner/timeline; operations gets a read-leaning subset;
compliance gets client-read + compliance.* + audit.read + record.read_all; benefits_* and
insurance_* roles get their domain capabilities (sensitive reads only to the *compliance*
variant). Exact grants are in `docs/platform_architecture_manifest.yaml` and the DB.

## 10. Record-scope model
Scope is enforced **in services** (scope-first), with middleware covering common families:

- **Entity types that grant access:** `person`, `household`, `organization` (via
  `record_assignments`). `record_in_scope(principal, entity_type, id, *, write)` is
  entity-type-agnostic; `organization_in_scope` and `accessible_person_ids` are team-aware.
- **Middleware RECORD_PATH** `^/(people|households)/(\d+)` enforces person/household scope for
  those families; `/organizations`, `/benefits`, `/insurance`, `/tax`, `/documents`, `/work`
  map to a required capability via the RULES table (GET→`.read`).
- **Routes OUTSIDE shared scope middleware — service enforces scope itself:** `/advisor-work`,
  `/annual-review`, `/business-owner`, `/compliance` (these match no middleware RULE, so each
  handler uses `require_capability` + the service checks `record_in_scope`).
- **Validated-relationship fallback:** Business Owner Planning grants business visibility when
  the in-scope person has a **validated ownership relationship** to the business, or the
  business is independently in `organization_in_scope`. Never inferred from a name/free text.
- **Prohibited:** name-based or free-text scope inference; business-owner status from
  occupation/employer/tax-document presence.

## 11. Sensitive-data and redaction model
All redaction is server-side; templates receive already-redacted data. Restricted (lacks
capability) is distinguished from missing (no data).

| Datum | Capability to view | Behavior without it |
|---|---|---|
| EIN | `benefits.sensitive.read` | value withheld; `ein_present` flag from ciphertext (Fernet-encrypted at rest) so restricted ≠ missing |
| Policy numbers / values | `insurance.sensitive.read` | number withheld; presence flag retained |
| Benefits PHI / retirement PII | `benefits.sensitive.read` | omitted |
| Tax content | `tax.read` (+ review caps) | tax section marked *restricted* |
| Compliance comments / evidence | `compliance.review.read` | timeline shows "Additional details are restricted."; workspaces show counts only |
| Advisor-work notes (in timeline) | `advisor_work.read` | redacted summary |
| Documents / evidence | `document.read` | omitted |
| Client / planning notes | owning cap | omitted |

No secrets or encryption keys appear in code paths that reach templates or documentation.

## 12. Advisor Intelligence architecture
Deterministic, **not AI/LLM**. `get_client_signals(principal, person_id)` (scope-first) reads
existing operational data and emits `Signal`s in categories `recommendation`, `opportunity`,
`review`, `exception`, `task`, `meeting`. Recommendations carry a durable
`RecommendationMeta(recommendation_type, governing_rule, rule_version, compliance_owner,
approval_status)` and deterministic ids; policy gates are display-only placeholders. Signals
are recomputed at render time and have **no durable timestamp**, which is why the Activity
Timeline excludes them (including them would fabricate history). Consumers: Advisor Work
(create-from-recommendation), Compliance (submit review), Annual Review, Business Owner
Planning (grouped only by durable `recommendation_type` — never keyword-invented categories).

## 13. Advisor Work architecture
`advisor_work_items` + append-only `advisor_work_events`. Creation is **explicit** — either a
user action or `create_from_recommendation` (idempotent: at most one OPEN item per
recommendation/person/rule). **No automatic creation** from observations, renders, or
missing-information. Lifecycle is an explicit allowed-transition map (new → assigned →
in_progress/waiting → completed/cancelled/archived) — **not** a workflow engine. Completion
records operational activity only; it never suppresses or alters the underlying recommendation
or its id. Items anchor to person/household (no business link today). Separate from the legacy
`/work` + `work.*` task system.

## 14. Compliance architecture
Three parts: **Rule Catalog** (governed rule definitions + versions), **Compliance Review**
(`compliance_reviews` + append-only `compliance_decisions`), and **Reviewer Authority**
(`reviewer_authorities` + append-only `reviewer_authority_events`). Submit → assign → decide.
Final approval **double-gates** on `compliance.review.decide` **and** a recorded Reviewer
Authority (and a Rule-Catalog version match); without them, approval is blocked, never silently
granted. Advisor completion of work or an annual-review checklist is **not** regulatory
approval. Business Owner Planning and Annual Review consume compliance **status/counts** only —
never comments/evidence, and never make or certify a decision.

## 15. Activity Timeline and audit architecture
Distinct record types:

- **Activity Timeline** — a read **projection** (`activity_timeline`) over `timeline_events`
  plus domain ledgers via per-domain adapters. Deterministic `(occurred_at desc, stable-id
  desc)` ordering, stable event ids, bounded per-source (≤500) and page (≤100), server-side
  redaction. **No table of its own; no second event table.**
- **Domain timeline events** — `timeline_events` (one row per durable domain event; some older
  rows lack an actor).
- **Administrative audit log** — `audit_events` (append-only, `audit.read`) — a separate
  security record, not the advisor-facing timeline.
- **Append-only ledgers** — `advisor_work_events`, `compliance_decisions`,
  `reviewer_authority_events`, `exception_events`, `workflow_events`, tax `*_events`.
- **Mutable records** — `annual_review_sessions`, `business_planning_profiles`.

Business-planning profile changes emit durable events through the shared `add_timeline_event`
writer (creation / status change / valuation update), anchored to the owning person — **not** a
new event table. Client360 is **not** event-sourced.

## 16. Annual Review architecture
`annual_review_sessions` (mutable advisor-activity record: notes + presentation-only
checklist; lifecycle draft → in_progress → completed → archived; idempotent start via a
partial-unique OPEN guard). It composes Client360 brief, Advisor Intelligence, Advisor Work,
Timeline, Compliance, and portfolio, each gated on its owning capability. It changes no source
record. Routes `/annual-review/{person_id}`, `/annual-review/session/{id}`.

## 17. Business Owner Planning architecture
Anchored to a person; reaches businesses through the ownership graph via a **pure read**
(`list_person_business_ownership`) that never calls `ensure_person_entity` (no write on
render). Business-owner status derives only from an active ownership edge. Sole persistence:
`business_planning_profiles` (succession/continuity/buy-sell/valuation/key-person, controlled
status vocabulary) — the audit proved these facts have no other home. Additive owning-service
reads: `tax_domain.business_engagements`, `insurance.business_policies`,
`organization_service.list_person_business_ownership`/`list_household_business_ownership`.
Business scope = person-in-scope AND (validated ownership OR `organization_in_scope`) — blocks
URL enumeration. Routes `/business-owner/{person_id}` and `/business/{business_id}` (+ planning
POST). See `docs/PHASE_D12_BUSINESS_OWNER_PLANNING_WORKSPACE.md`.

## 18. Benefits, retirement, insurance, and tax boundaries
- **Retirement** is the retirement line of the Benefits domain (`benefit_plans` + retirement
  details), org-scoped. Contribution/limit amounts and Cash Balance/DB funding are **not
  modeled**; nothing is calculated.
- **Benefits** owns group health/retirement plans, plan years, obligations, enrollments.
  Employer-contribution amounts are not modeled. `benefits.sensitive.read` gates PHI/PII.
- **Insurance** is life/annuity only; disability/LTC and structured **policy purpose** are not
  modeled. `insurance.sensitive.read` gates policy numbers/values.
- **Tax** tracks engagements/returns/intake/lifecycle/documents (metadata + workflow) — **no
  return financial content** (K-1, W-2 wages, guaranteed payments, distributions, QBI,
  S-election, accounting method are not modeled). Tax scope is office/team + subject.

## 19. Importers and external-source boundaries
Do not claim live sync where only import infrastructure exists.

| Source | Status | Notes |
|---|---|---|
| Schwab | importer implemented | `app/importers/schwab.py` (portfolio) |
| AssetMark | importer implemented | `app/importers/assetmark.py` |
| Wealthbox | importer implemented | `app/importers/wealthbox.py` (CRM contacts) |
| Dave Ramsey / SmartVestor | importer implemented | `app/importers/dave_ramsey.py` |
| Microsoft 365 | integration | OAuth + calendar/mail/documents routes (near-live) |
| TaxDome | not implemented | SOP reference only |
| Drake | not implemented | SOP reference only |
| Betterment | stub | disabled recordkeeper provider stub (retirement) |
| Guideline / Gusto | not implemented | comment-only / absent |

Imported records flow through source contacts/links and matching → canonical merge
(`person_merge`) into `people`. This is **import + reconciliation**, not continuous
synchronization.

## 20. Routes and application surfaces
**Verified total: 432 routes** (`python -c "from app.main import app; print(len(app.routes))"`;
guarded by `tests/test_f4_8_workflow_api.py` and `tests/test_f4_7_workflow_evidence.py`). Route
families: `/people`, `/households`, `/organizations` + `/api/v1/organizations`, `/benefits` +
`/api/v1/benefits`, `/insurance`, `/tax` (+ `/tax/intake`, `/tax/returns`, `/tax/documents`),
`/compliance`, `/advisor-work`, `/people/{id}/timeline` + `/households/{id}/timeline`,
`/annual-review`, `/business-owner`, `/opportunities` (+ `/opportunities/reports`), `/campaigns`,
`/referral-sources`, `/business-development`, `/analytics`, `/documents` (legacy) +
`/document-library` (platform), `/workflows` (legacy engine) + `/workflow-automation`
(orchestration), `/communications` (D.18 client engagement), `/scheduling` (D.19 meetings &
appointments), `/operations` (D.20 firm projects/tasks/capacity), `/reporting` (D.21 dashboards & BI),
`/automation` (D.22 jobs/schedules/runs), `/governance` (D.23 quality/lineage/retention),
`/integration` (D.24 connectors/webhooks/API/events), `/security` (D.25 policies/providers/secrets/
certificates/incidents/findings), `/observability` (D.26 services/health/diagnostics/telemetry/alerts/
reliability), `/configuration` (D.27 settings/features/editions/preferences/changes), `/runtime` (D.28 runtime
engine — effective config/features/snapshots/cache), `/runtime/cluster` (D.29 workers/versions/
convergence/diagnostics), `/runtime/behavior` (D.30 consumption/adoption registry + D.31 governance/authority), `/runtime/policy`
(D.32 policy registry/governance/dependency-graph/diagnostics), `/orchestration` (D.33 workflow
registry/governance/instances/diagnostics/replay/simulation), `/events` (D.34 domain-event
contracts/subscriptions/governance/diagnostics/replay), `/projections` (D.36 read-model
registry/health/diagnostics/governance/rebuild/replay), `/workspace`
(meeting), `/portfolio` +
`/wealth`, `/admin` (+ `/admin/audit`, rule-catalog, roles), `/microsoft365`, `/auth`, and JSON
`/api/v1/*`.

## 21. Database and migration architecture
- **Engine:** SQLAlchemy Core; `app/db.py` reflects the live schema; declared schema lives in
  `app/database/*_tables.py` registered via `define_*_tables(metadata)` in
  `app/database/schema.py` (29 registered modules: advisor_work, analytics, annual_review,
  automation, business_planning, campaign_referral, communication, compliance, configuration,
  document_platform, event, governance, identity, integration, observability, operations, opportunity,
  orchestration, outbox, portfolio, projection, reporting, runtime, runtime_behavior,
  runtime_coordination, runtime_policy, scheduling, security, work — plus core tables inline in
  `schema.py`).
- **Alembic:** 83 migrations, **single head `l3q4v5w6x7y8`**; `alembic current == heads`.
  Recent chain: D.28 `z0a1b2c3d4e5` → D.29 `z2c3d4e5f6a7` → D.30 `z4e5f6a7b8c9` → D.31
  `z8a9b0c1d2e3` → D.32 `z9b0c1d2e3f4` → D.33 `za0b1c2d3e4f` → D.34 `zb1c2d3e4f5a` → D.35
  `zc2d3e4f5a6b` → D.36 `zd3e4f5a6b7c` (D.37 is code-only — no migration; head unchanged) → D.38
  `k2w3s4p5r6f7` (advisor-workspace personalization tables + `workspace.personalize`) → D.39
  `l3q4v5w6x7y8` (unified-work-queue saved-view tables + `work_queue.saved_views`).
- **Capability-seeding pattern:** each domain migration inserts its capabilities and grants
  `role_capabilities` idempotently.
- **Downgrade expectations:** every recent migration is reversible (down removes its
  table(s)/index(es) and capabilities); verified for D.9–D.12.
- **Prohibition:** no parallel heads unless intentionally merged; no squashing/renaming in
  D.12A.

## 22. Testing and architectural enforcement
- **Route-count guards:** `tests/test_f4_7_workflow_evidence.py`, `tests/test_f4_8_workflow_api.py`.
- **Golden regression:** `tests/test_intelligence_refactor_regression.py` (D.5) pins serialized
  signals + rendered panels.
- **Dependency-direction tests:** each composition phase asserts source domains don't import it
  (D.10/D.11/D.12).
- **Platform enforcement (new):** `tests/test_platform_architecture.py` validates the manifest
  against live code — route count, migration head, capability existence, module existence,
  import direction, schema registration, single head, and required doc sections.

## 23. Current limitations
Consolidated, honest register (D.9–D.12):
- Advisor Intelligence recommendations have **no durable timestamp** → excluded from the
  Timeline (not fabricated).
- Advisor Work items and Compliance reviews carry **no business link** (person/household only).
- **No per-client servicing-advisor field** — the "advisor" shown is the current principal.
- **Tax return financial content, owner compensation, insurance policy purpose, disability/LTC,
  retirement contribution amounts, business valuation** are **not modeled** (shown "Not
  available").
- **Succession/continuity data is prospective only** (no backfill — would fabricate).
- **Historical household-membership windows** are not modeled (current membership only).
- Some **older `timeline_events` lack an actor**.
- Ownership "conflicts across sources" cannot be represented while `relationships` enforces
  unique `(from, to, type)` edges (detector retained for future).
- Household ownership summary does one ownership read per member (bounded by household size).
- Betterment/Guideline/Gusto provider integrations are stubs; TaxDome/Drake are SOP references
  only.

No route/capability inconsistencies or upward-dependency defects were found in the D.12A audit.

## 24. Extension points
Not implemented; documented so future phases don't duplicate logic.

| Extension | Likely owner | Should consume | Must not duplicate | Prerequisite | Timing |
|---|---|---|---|---|---|
| Opportunity/sales pipeline | ~~future~~ **implemented D.13** (source domain) | Advisor Intelligence, Advisor Work | recommendation/work logic | done — see ADR-018 | done |
| Household relationship intelligence | Relationships | relationship graph | ownership/relationship tables | richer relationship types | D.13+ |
| Estate planning | new source domain | People/Households, documents | none | new structured tables | later |
| Executive compensation | Benefits or new domain | Benefits, Organizations | benefits/plan tables | comp schema | later |
| Advanced retirement strategies | Benefits (retirement) | benefit plans | plan tables | contribution schema (currently absent) | later |
| Tax-return structured data | Tax | tax engagements | engagement metadata | new return-content tables + import | D.13+ |
| Business valuation | Business Owner Planning or new | business entities | ownership/profile tables | valuation schema | later |
| Exit planning | Business Owner Planning | succession/valuation | planning profile | valuation prerequisite | later |
| Document intelligence | Documents | documents/evidence | evidence tables | classification infra | later |
| Client portal / notifications / integrations | Portal / Outbox | existing services | domain data | already partial infra | later |

## 25. Prohibited patterns
- Composition layers persisting or mutating source-domain data.
- Client-side security enforcement; template-only redaction of sensitive data.
- Inferring ownership or business-owner status from names/occupation/free text.
- Fabricating history, calculations, tax figures, contribution limits, valuations, or
  insurance needs.
- A second recommendation engine, AI/LLM, or keyword-invented recommendation categories.
- An external message broker/queue, a second event log, or automatic Advisor Work creation. (The
  sanctioned internal event bus is the single transactional outbox — D.34 domain events flow over it;
  workflow orchestration is the D.33 deterministic coordination layer, not a generic engine.)
- A second timeline-event table or treating Client360 as event-sourced.
- Upward/circular service imports (producer importing a consumer or composition layer).
- Write side effects during page-render reads.
- Additive reads placed outside their owning service.
- Regulatory approval outside authorized Compliance workflows.

## 26. Architecture change process
1. Audit code first; do not document aspirationally.
2. Keep the owning service authoritative; add only *additive reads* there.
3. New composition layers consume, never duplicate; persist only genuinely-new data proven to
   have no home.
4. Add capabilities per domain migration; enforce server-side; gate sensitive data.
5. Keep migrations linear (single head); make them reversible.
6. Update `docs/platform_architecture_manifest.yaml` **and** this document together; keep
   `tests/test_platform_architecture.py` green.
7. Bump route-count guards deliberately when adding routes.

## 27. Glossary
- **Source domain** — authoritative owner of a data type.
- **Composition layer** — read-first assembler that consumes source domains; never a source of
  truth.
- **Principal** — the authenticated user + capability set for a request.
- **Record scope** — per-record access via `record_assignments` (person/household/organization),
  team-aware for some helpers.
- **Append-only ledger** — a table protected by a mutation-blocking trigger (corrections add a
  new row).
- **Projection** — a read-only view assembled from other tables (Activity Timeline).
- **Restricted vs missing** — restricted = data exists but the principal lacks the capability;
  missing = no data recorded.

## 28. References
- **`docs/adr/README.md`** — **Architecture Decision Records** (the *why* behind the decisions
  described here; ADR-001…ADR-017). This document explains *what exists*; the ADRs explain *why*.
- `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md` — advisor-workspace evolution history (D.1–D.12).
- `docs/PHASE_D6..D12_*.md` — per-phase design records.
- `docs/AUTHORIZATION.md`, `docs/OBJECT_SECURITY.md`, `docs/FIELD_SECURITY.md` — auth/scope/field
  security.
- `docs/AUDIT_LOG.md`, `docs/EVENTS.md` — audit and event model.
- `docs/DATABASE.md` — schema/migration conventions.
- `docs/RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md`, `docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md`
  — benefits/insurance domains.
- `docs/platform_architecture_manifest.yaml` — machine-readable companion (test-validated).
