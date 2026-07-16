# Release v0.10.0 — Insurance Operations: Architecture

**Status:** in progress — **Phases 0–6 implemented** (Phases 2–4 as non-regulated
operational skeletons; **Phase 5 commissions and Phase 6 exceptions/work-management/scheduled
scanning are non-regulated and complete for their scope**); Phases 7–10 not started; **all
regulated logic deferred behind the AD-5 gate**. Not yet released or tagged. (Current single
Alembic head `c9k0m1n2h3j4`; migration chain in §13. See `PROJECT_STATUS.md` for live status.)
**Scope:** individual **life insurance & annuities** (advisor-sold, in-force-managed).
Not group/employer benefits (that is 0.9.11), not P&C, not group life.
**Baseline:** built on the 0.9.11 platform (ADR-18) and the 0.9.13 test/CI/release
infrastructure. New migration chains off head `u1f9c0i9h8g7`.

> ### Implementation status at a glance — read before building on this document
>
> This is the design of record. What has actually shipped on `feature/insurance-operations`
> is a deliberately narrower, three-way split:
>
> - **Implemented (operational / non-regulated):** product catalog, `insurance_case`,
>   policies + coverages/riders/parties/values, policy lifecycle & Timeline/Audit events,
>   new-business pipeline & requirement tracking, in-force reviews state machine + obligation
>   calendar, producer licensing/CE **records**, expiry detectors, **commission ledger —
>   split-aware expected/received, carrier-statement import & reconciliation, variance/
>   outstanding exceptions, and revenue rollup (Phase 5)**, operational (counts-only)
>   reporting, UI, and JSON APIs. **Exceptions, work queues & scheduled scanning — one
>   `run_insurance_scan()` across all detectors, wired into the shared scheduler and Work
>   Management with organization-scoped, firm-internal exceptions (Phase 6).** Phases 0–6.
> - **Deferred (regulated logic — NOT built, NOT enabled):** suitability determination,
>   replacement/1035 recommendation logic, licensing/CE **validation**, sale/issue
>   **blocking**, automated compliance approvals, and any regulatory decision engine.
>   Tests assert these functions do **not** exist in the services and that scan results
>   carry no compliance field.
> - **AD-5 approval gate:** the deferred regulated logic stays blocked until a **qualified,
>   named compliance reviewer** is in place **and** an approved, dated sign-off artifact
>   exists for the relevant rule set (§AD-5). Business sign-off (Michael Shelton, operational
>   scope) is **not** regulatory certification. Missing/rejected artifact = phase blocked.
>   This gate is **not resolvable in code**.
>
> Phase-by-phase built-vs-deferred detail is in §§12a–12c; the gate itself is in §AD-5.

This document incorporates the seven approved design refinements. Where it says
"reuse," the platform system is used unchanged; a new table is introduced **only
where a regulated insurance concept cannot be accurately represented** by the
platform (Refinement 1).

---

## 1. Audit summary — what exists

The 0.9.11 foundation deliberately pre-cut the seams for insurance. Already seeded in
migration `r8c69f7e6d5c`:

- `insurance` **service line**; `insurance_commissions` **revenue category**.
- Relationship roles `primary_producer`, `secondary_producer`, `broker_of_record`,
  `renewal_owner`, `account_manager`, `service_rep`, `insurance_agent`.
- Provider types include `carrier` and `broker`.

Everything insurance-**specific** is greenfield: policies, annuities, in-force,
underwriting, 1035s, suitability, illustrations, licensing/CE, commissions.
**iPipeline and KaiZen appear nowhere** in the codebase. `app/importers/assetmark.py`
is a 0-byte empty file, and AssetMark is a TAMP (not insurance) — out of scope.

Reuse verdicts:

| Platform system | Verdict |
|---|---|
| people / households / organizations (`relationship_entities`) | reuse |
| engagements / service lines | reuse (see §3, InsuranceCase wraps it) |
| exception engine | reuse — add `domain='insurance'` |
| work management | reuse — add insurance to the `work_items` domain tuple + queues |
| scheduler | reuse — register one scan job (SLA sweep already domain-agnostic) |
| workflow automation | reuse — templates-as-data |
| documents / e-signature | reuse |
| portal (org/person-scoped grants) | reuse — employer-portal recipe |
| permissions / capabilities | reuse — add `insurance.*` caps + roles |
| reporting (auth-filtered-before-aggregation) | reuse pattern |
| audit / timeline | reuse |
| relationships graph | reuse — producer/appointment/agency hierarchy |
| obligations | reuse — materialize policy reviews |

---

## 2. Design principle (Refinement 1) — shared domain

Insurance is a **domain inside Client360**, never a separate application or subsystem.
The reuse-vs-new split:

**Reused as-is (no new model):** people, households, organizations, engagements,
workflows, work management, exception engine, scheduler, documents, e-signature,
portal, permissions, reporting pattern, audit/timeline, relationships, obligations.

**New models (each a regulated concept the platform cannot accurately represent):**
InsuranceCase; product catalog (carrier profile / product family / product version);
policies + coverages/riders/values; policy parties; policy producers; policy
relationships; suitability; replacements/1035s; licenses/CE; commissions; policy
reviews.

Test applied to every new table: *"a generic engagement + a JSON blob cannot
represent this with the integrity, queryability, and auditability it requires."*

---

## 3. InsuranceCase determination (Refinement 2)

**Determination: the Engagement model is not sufficient on its own; a lightweight
`insurance_case` is warranted — a coordinator that references an Engagement, not a
replacement for it.**

Why Engagement alone falls short:
- An `engagement` is a single unit of service-line work (`open → closed`), anchored to
  one org/person/household, with a free-text `engagement_type` and a `metadata` JSON.
- The insurance lifecycle must group heterogeneous regulated artifacts (fact finder,
  illustrations, underwriting file, APS requests, requirements, suitability,
  replacements, 1035s) **and multiple proposed policies (1:N)** converging on one or
  more issued policies. Representing that in `engagement.metadata` JSON would violate
  Refinement 1 and cannot hold 1:N proposed policies with integrity.

Why the case must not duplicate the engagement:
- The engagement stays **canonical** for service line, revenue (`insurance_commissions`),
  work management, and workflow attachment. The case re-implements none of that.

**`insurance_case`** — `id, engagement_id (1:1 FK — the work/service-line/workflow
anchor), household_id, person_id, case_type (new_business | replacement | review |
servicing), status (open | fact_find | proposed | underwriting | issued | declined |
closed), objective, created_by_user_id, metadata`.

Attached to `case_id` as first-class rows: fact finder, illustrations (documents),
underwriting file, APS requests, requirements, suitability, replacements, 1035s,
**N proposed policies**, and the **issued policy/policies** (a case may issue more than
one). The workflow (application → underwriting → issue) runs on the engagement; the
case is what a producer opens and works. **One case ↔ one engagement**: the case
*coordinates*, the engagement *is the work*.

---

## 4. Data model

New tables, additive, off head `u1f9c0i9h8g7`, single head, reversible.

### 4.1 Product catalog (Refinement 5) — three levels, not hard-coded

```
insurance_carriers  →  insurance_product_families  →  insurance_product_versions
```

- **`insurance_carriers`** — carrier is a `relationship_entities` org node +
  **`insurance_carrier_profiles`** (`naic_company_code`, `am_best_rating`,
  `appointment_status`) for regulated insurer fields the org layer cannot hold.
- **`insurance_product_families`** — `carrier_id, name, product_type (term_life |
  whole_life | iul | vul | fixed_annuity | variable_annuity | fia), line (life |
  annuity), status`.
- **`insurance_product_versions`** — `family_id, version_label, effective_from,
  effective_to, state_availability, spec (JSON)`. **Policies pin a `product_version_id`**
  — product changes are versioned, never overwritten. KaiZen / premium-financed
  strategies are product families/versions, not code.

**Phase 1 refinement — carrier product evolution as first-class (migration `w3c4e5g6b7d8`).**
Validating "can Product Version represent long-term carrier evolution" surfaced that
carrier product codes, illustration identifiers, and rider compatibility were only
expressible via the untyped `spec` JSON — inadequate for integration-matching keys and a
queryable/validatable compatibility relationship. Resolved structurally, not with JSON:
- `insurance_product_versions` gains **`carrier_product_code`** and **`illustration_identifier`**
  (indexed for matching; not globally unique — carriers reuse codes and a family's versions
  share one, so matching is scoped by carrier).
- **`insurance_product_rider_compatibility`** (`product_version_id, rider_type, requirement
  [included|available|optional|excluded], carrier_rider_code, effective_from/to`, unique per
  `(product_version_id, rider_type)`) makes allowed/rejected riders queryable. A rider is
  compatible only if listed with an attachable requirement; absent or `excluded` = rejected.
- `spec` JSON is retained for genuinely unstructured carrier-specific attributes only.
Lookups live in `app/services/insurance_catalog.py`
(`find_by_carrier_product_code`, `find_by_illustration_identifier`, `compatible_riders`,
`is_rider_compatible`).

**Phase 1 — lifecycle events on the shared Timeline/Audit (no separate history model).**
`app/services/insurance.py` publishes each significant lifecycle transition into the
existing `timeline_events` + `audit_events`: case opened; application submitted;
underwriting status changed; policy issued / delivered / placed in force / lapsed /
reinstated / surrendered / replaced / death-claim; ownership changed; beneficiary changed.
(Requirement requested/satisfied arrive with the requirements model in a later phase.)
Payloads are proportional — role, status, and carrier reference only, never identifiers,
financials, or party PII. The policy status CHECK was widened (`issued`, `delivered`,
`reinstated`) in migration `x4d5f6h7c8e9`.

### 4.2 Policies and parties (Refinement 3) — no single-owner assumptions

- **`insurance_policies`** — subject anchor (person/household/org), `carrier_id`,
  `product_version_id`, `policy_number`, `status (applied | underwriting | in_force |
  lapsed | surrendered | replaced | death_claim)`, `issue_date`, `face_amount`,
  `premium_amount`, `premium_mode`, `case_id (FK)`, `metadata`.
- **`insurance_coverages`** / **`insurance_riders`** — base coverage + riders (child rows).
- **`insurance_policy_values`** — periodic as-of snapshots (`cash_value`,
  `surrender_value`, `death_benefit`) for in-force tracking.
- **`insurance_policy_parties`** — the normalized ownership model:
  `policy_id, party_role (owner | insured | annuitant | payer | beneficiary |
  assignee), party_entity_type (person | household | organization), party_entity_id,
  share_percentage, designation (primary | contingent), is_primary_insured,
  relationship_to_insured, effective_date, inactive_date`.
  - **Household / trust / business ownership** → `party_entity_type='organization'`
    → `relationship_entities` (`entity_type` `trust`/`business`/`estate`, already in
    `ORG_ENTITY_TYPES`), or `household`, or `person`. One mechanism, all structures.
  - **Multiple insureds** (survivorship) → multiple `insured` rows.
  - **Multiple beneficiaries** → multiple `beneficiary` rows with designation + share.
  - **Multiple policies per client** → many policies per person/household, grouped by case.
- **`insurance_policy_relationships`** — `from_policy_id, to_policy_id, relation_type
  (replaces | funded_by_1035 | rider_of | successor | same_case), effective_date` —
  links 1035 source/target, replacements, and related coverage with integrity.

### 4.3 Producers (Refinement 4) — no single-producer assumption

- **`insurance_policy_producers`** (policy- and case-level) — `producer_entity_type
  (user | organization), producer_entity_id, producer_role (writing_agent |
  servicing_agent | broker_of_record | override), split_percentage, effective_date,
  inactive_date`.
  - **Split commissions / overrides** → multiple rows with `split_percentage`; commission
    ledger credits each producer per split; an `override` role credits an upline entity.
  - **Agency hierarchy** → the existing **relationships graph** between producer org
    entities (agency → sub-agency → agent), reusing seeded relationship types. No new
    hierarchy table.

### 4.4 Regulated events

- **`insurance_suitability`** — case/policy-scoped: findings, disclosures, reviewer,
  outcome. Compliance-visible.
- **`insurance_replacements`** — 1035/replacement events: from/to policy, type,
  disclosure and timing fields, suitability link.
- **`insurance_policy_reviews`** (Refinement 6) — **first-class business event**, not a
  task: `policy_id (or case_id), review_type (annual | inforce | suitability), status
  (due | scheduled | completed | deferred | declined | overdue), due_date,
  scheduled_date, completed_date, outcome, reviewer_user_id, next_review_date`. A proper
  state machine. The recurring obligation materializes a review as `due`; the scheduler
  detector flips lapsed ones to `overdue` and raises an insurance exception. Drives
  **reporting metrics**: completion rate, overdue/deferred/declined counts,
  time-to-complete.
- **`insurance_licenses`** / **`insurance_ce_records`** — producer (user) licensing:
  state, number, lines, status, expiry; CE credits/period/status. Expiry detectors.
- **`insurance_commissions`** — `policy_id, producer_ref, expected_amount,
  received_amount, schedule, status`; expected-vs-received reconciliation; rolls up to
  the `insurance_commissions` revenue category.

---

## 5. APIs

One `app/routes/insurance.py` (thin HTTP over services; `_run` error mapper), registered
in `app/main.py`. Dual surface, matching benefits:

- **JSON** `/api/v1/insurance/*` — carriers, product families/versions, cases (+ party,
  producer, proposed-policy sub-resources), policies (CRUD + lifecycle transitions),
  coverages/riders/parties/beneficiaries, suitability, replacements, reviews (with status
  transitions), licenses/CE, commissions.
- **HTML consoles** — `/insurance` (book), `/insurance/cases/{id}`,
  `/insurance/policies/{id}`, `/insurance/reviews`, `/insurance/licensing`,
  `/insurance/reporting`. Specific static paths declared before catch-alls.
- Pydantic v2 bodies (`model_dump()`), `Depends(require_capability(...))` per endpoint.

---

## 6. Workflows

Immutable published `workflow_templates` (category `insurance`), templates-as-data,
running on the case's engagement:

- **New business:** `application → suitability_review → underwriting → issue →
  delivery` (delivery triggers an e-sign `signature_request`). Suitability/replacement
  approval is a segregation-of-duty step (`work_approvals`, no self-approval).
- **Replacement / 1035:** `suitability → disclosure → surrender/exchange → new_issue`,
  compliance approval gate.
- **Annual review:** recurring obligation → materializes an `insurance_policy_reviews`
  row → review workflow.

Auto-launch via `automation_triggers` on insurance events (`process_event`). SLA via
`evaluate_sla` (reused).

---

## 7. UI

- New **Insurance nav group** in `base.html`, gated `'insurance.read' in caps`
  (hidden-not-403).
- Templates `app/templates/insurance/*.html` extending `base.html`, using the 0.9.12
  component library (`.stat-grid`, `table.data`, `.badge`, `.sev`, `.empty`, styled
  403/404).
- Screens: book/list; **case workspace** (fact finder, illustrations, underwriting,
  requirements, proposed policies, suitability, issued policy — the coordinator view);
  policy detail (coverages/riders/parties/values/producers/commissions/timeline);
  reviews board (by status); licensing dashboard; reporting.

---

## 8. Permissions

New capabilities (dotted, `sensitive` flag on PII/financial): `insurance.read`,
`insurance.write`, `insurance.suitability` (compliance-gated review),
`insurance.commissions.read`, `insurance.licensing.read/.write`,
`insurance.sensitive.read`. New roles: `insurance_agent`, `insurance_operations`,
`insurance_compliance` (+ `administrator` gets all). Seeded via the migration `grant()`
recipe. Add `(r"^/insurance|^/api/v1/insurance", "insurance.read")` to middleware
`RULES` **after** any segregation-of-duty carve-outs (so the `.read → .write`
inference cannot lock out suitability reviewers). Record-scope enforced in services
(org/person anchor), like benefits.

---

## 9. Portal

Policyholder surface via the employer-portal recipe:
`portal_access_grants.organization_id`/`person_id` scope +
`require_org_scope(permission="insurance")`. Routes `/portal/insurance/*`, templates
`portal/insurance_*.html`. A policyholder can: view "my policies" summary (read-only),
see review/servicing action-needed, upload/confirm requested documents, secure-message.
Out-of-scope → 404.

---

## 10. Reporting

`app/services/insurance_reporting.py` mirroring `benefits_reporting`: `_scoped_ids`
(firm-wide for `record.read_all`, else assigned orgs/teams), `scoped()` closure on every
query, gate `insurance.read`, fold in `exception_report(principal, domain="insurance")`.
Dashboards: **book & in-force** (count, face, premium, by carrier/product);
**new-business pipeline** (by case/workflow stage); **reviews** (completion rate,
overdue/deferred/declined — from `insurance_policy_reviews`); **commissions** (expected
vs received, aging, by producer/split); **compliance** (suitability, replacements,
license/CE expiries). Proportional disclosure ("6 of 128 organizations").

---

## 11. Integrations

All as **disabled provider ports** (Protocol + registry + stubs, honest
`not_connected` outcomes), following `app/services/benefits_providers.py`:

- **iPipeline** — e-app / illustration / in-force data port (stub).
- **Carrier feeds** — in-force / commission data port (stub).
- **KaiZen** — modeled as a product family/version + metadata; no live integration.

Live integration is a **future release** (its own vendor contracts, credentials, and
compliance review). No live I/O ships in v0.10.0.

---

## 12. Implementation phases

Follows the project cadence: phased, stop-for-review at each boundary, merge-commit, RC
gate before "implemented."

| Phase | Scope | Exit gate |
|---|---|---|
| **0** | Product catalog (carrier profile/family/version) + `insurance_case` + policy/party/producer schema; capabilities/roles; register `insurance` in exception engine (`SUPPORTED_DOMAINS`, CHECK) + work management (`work_items` tuple) | migration reversible, single head; caps/roles seeded; suite green |
| **1** | Policies core + coverages/riders/parties/values; policy CRUD API + book/detail UI | policy lifecycle CRUD; multi-owner/insured/beneficiary; record-scope |
| **2** | New business — **case** as container: applications (engagement) + workflow + fact finder + illustrations (docs) + proposed policies + suitability + e-sign. **BLOCKED (AD-5): no suitability rules/recommendations/approvals until the compliance reviewer is named, or a non-regulated skeleton is explicitly authorized.** | a case coordinates ≥2 proposed policies → an issued policy; **and** an approved compliance sign-off artifact exists for the suitability rule set |
| **3** | In-force — servicing, **reviews as first-class** (state machine + metrics), replacements/1035, obligation calendar | review lifecycle drives completion/overdue metrics; 1035 with suitability |
| **4** | Licensing & CE — records + expiry detectors | expiring-license exception raised |
| **5** | Commissions — expected/received ledger, splits/overrides, reconciliation, revenue rollup | split-commission producers credit correctly; variance surfaced |
| **6** | Exceptions + detectors + queues + `run_insurance_scan` job | detectors idempotent; queues populated |
| **7** | Portal policyholder surface | org/person-scoped; out-of-scope 404 |
| **8** | Reporting + dashboards | auth-filtered; proportional disclosure |
| **9** | Integration ports (disabled stubs) | ports registered, inert |
| **10** | RC validation + release v0.10.0 | RC PASS; tagged |

---

## 12a. Phase 2 scope split — non-regulated (built) vs compliance-gated (deferred)

With the compliance reviewer unassigned (AD-5), Phase 2 ships the **non-regulated
new-business plumbing only**; every regulated determination stays behind the gate.

**Non-regulated — built in Phase 2 (`y5e6g7i8d9f0` + services/routes/UI):**
application pipeline & case progression (case status transitions), requirement tracking
(`insurance_requirements`: requested → satisfied — an operational checklist, not a
determination), underwriting-**status** tracking (`insurance_policies.underwriting_status`
— records the carrier's status; the platform does not decide it), document collection
(reuses the shared `documents` table via `requirement.document_id`), carrier-communication /
process orchestration (reuses workflow automation), shared Timeline/Audit events
(case status, requirement requested/satisfied, underwriting status changed), operational
reporting (`insurance_reporting.pipeline_report` — pipeline counts only, no compliance
metrics), UI (case workspace, pipeline), and the JSON APIs.

**Compliance-gated — NOT built / not enabled** (behind AD-5): suitability determination,
replacement recommendations, 1035 recommendation logic, licensing validation, CE
determination, automated compliance approvals, and any regulatory decision engine. No
function evaluates, scores, recommends, validates, or concludes a regulated matter (a test
asserts none exist in the service).

## 12b. Phase 3 scope split — non-regulated (built) vs compliance-gated (deferred)

Phase 3 (in-force servicing) straddles the AD-5 line: the doc's own exit gate reads
"review lifecycle drives completion/overdue metrics; **1035 with suitability**." The
review-lifecycle half is operational and ships; the suitability-bearing half stays gated.

**Non-regulated — built in Phase 3 (`z6f7h8j9e0g1` + services/detector/routes/UI):**
policy reviews as a first-class **state machine** (`insurance_policy_reviews`: due →
scheduled → in_progress → completed / deferred / overdue / cancelled), the **obligation
calendar** (annual reviews materialize their next occurrence on completion; a scheduled/
manual scan flips past-due reviews to `overdue` and raises `INS_REVIEW_OVERDUE` through the
**shared Exception Engine** — no second engine, idempotent, auto-resolving), operational
**review metrics** (`insurance_reporting.review_report`: completion rate, overdue/deferred
counts), the reviews board UI + JSON APIs, and the shared Timeline/Audit review events.
`review_type` is a scheduling category (`annual | inforce | servicing`) and a review's
`outcome_note` is a free-text servicing summary — never a determination. Live cron wiring of
the scan is deferred to Phase 6 (`run_insurance_scan` job); the callable + manual endpoint ship now.

**Compliance-gated — NOT built / not enabled** (behind AD-5): suitability determination
(and the `suitability` review type / `insurance.suitability` capability stay reserved),
replacement recommendations, **1035** recommendation logic, licensing/CE determination, and
any compliance approval or regulatory decision engine. Tests assert no such function exists
in the review/detector/reporting modules and that the scan result carries no compliance field.

## 12c. Phase 4 scope split — non-regulated (built) vs compliance-gated (deferred)

Phase 4 (Licensing & CE) is named in the AD-5 gated list, so — like Phases 2 and 3 — only
the non-regulated skeleton ships; the determination logic stays gated. Extensibility was
re-verified first: the Phase 3 schema supports multiple concurrent cases, multiple policies
per case, replacements, partial 1035s (structurally; economics are a future nullable
column), split ownership, multiple beneficiaries, business-/trust-owned policies, multiple
producers, carrier product evolution, and future provider ports **without redesign**.

**Non-regulated — built in Phase 4 (`a7g8i9k0f1h2` + services/detectors/routes/UI):**
producer **licensing records** (`insurance_licenses`) and **CE records** (`insurance_ce_records`)
— firm-internal, capability-gated (`insurance.licensing.read`/`.write`, seeded in Phase 0),
audited, staff-entered; **date-driven expiry reminders** (`detect_licenses_expiring` /
`detect_ce_period_ending` raise `INS_LICENSE_EXPIRING` / `INS_CE_PERIOD_ENDING` through the
**shared Exception Engine** — idempotent, auto-resolving, firm-level/unanchored so they
surface to oversight roles); operational **licensing counts** (`licensing_report`); the
licensing dashboard UI + JSON APIs. Live cron wiring of the scan is Phase 6; the callable +
manual endpoint ship now.

**Compliance-gated — NOT built / not enabled** (behind AD-5): licensing **validation**
(whether a producer may sell a product in a state), CE **satisfaction determination**
(whether a CE requirement is met), sale/issue **blocking** on licensing status, and any
compliance approval or regulatory decision engine. The stored `credits_required` /
`credits_completed` are staff-entered figures — the platform draws no conclusion from them.
Tests assert no validation/determination function exists in the licensing/detector/reporting
modules and that the scan result carries no compliance field.

## 13. Dependencies

- Builds on the 0.9.11 platform and the 0.9.13 test/CI/release infrastructure (isolated
  test DB, migration reversibility, guarded release tooling all now protect this build).
- Single Alembic head (`u1f9c0i9h8g7`); the new migration chains off it.
- **Regulatory/domain SME input required** from the firm: suitability and
  replacement/1035 disclosure rules, state licensing/CE rules, commission schedules.
  This is a real external dependency, modeled as reviewable data (obligation/exception
  templates), not code.
- No dependency on live iPipeline/KaiZen (ports are stubs).

---

## 14. Risks

| # | Risk | Sev | Mitigation |
|---|---|---|---|
| R1 | Regulatory correctness (suitability, replacement/1035 disclosures, CE, NAIC) — a compliance liability, not a bug. More load-bearing now that reviews/suitability/replacements are first-class | **High** | Domain-SME sign-off per phase; rules as reviewable data; compliance-visible exceptions; SoD approval gates |
| R2 | PII/financial sensitivity (SSN, financials, beneficiaries) | High | `sensitive` capability gating; encryption pattern; `insurance.sensitive.read` |
| R3 | Scope sprawl (11 build phases) | Med | Phase gates; integrations deferred to stubs; underwriting = tracking, not decisioning |
| R4 | Carrier data variance (no standard feed) | Med | Manual entry first; ports stubbed; structured `metadata`/`spec` JSON for carrier specifics |
| R5 | Commission complexity (splits, overrides, chargebacks) | Med | Start with expected-vs-received + splits/overrides; defer chargebacks if needed |
| R6 | `v1.0.0` tag collision (roadmap endpoint) | Low | Unrelated to this release; resolve the stray tag before 1.0 |

---

## 15. Acceptance criteria

- **Compliance gate (regulated phases 2–4):** an approved, dated compliance sign-off
  artifact (§17 AD-5) from the named licensed reviewer exists for that phase's rule sets
  (suitability / replacement-1035 / licensing / CE). No regulated phase passes its RC gate
  without it. **Phase 2 is BLOCKED until the reviewer is named or a non-regulated skeleton
  is explicitly authorized.**
- Policy lifecycle drives applied → in-force via the workflow; record-scope enforced.
- A **case coordinates ≥2 proposed policies** converging to an issued policy, without
  duplicating engagement functionality.
- Trust/business/household ownership and multiple insureds/beneficiaries are
  representable and scope-enforced.
- Producer **splits/overrides** credit correctly; agency hierarchy expressible via
  relationships.
- Product is **versioned** (a policy pins a `product_version`); nothing hard-coded.
- **Policy reviews** move through due/scheduled/completed/deferred/declined/overdue and
  produce completion/overdue **metrics**.
- Suitability + replacement/1035 recorded with compliance-visible exceptions and
  approval gates (no self-approval); license/CE expiry raises an exception.
- Exception engine registers `domain='insurance'`; detectors idempotent; SLA sweep
  covers insurance; `work_items` surfaces insurance work.
- Portal policyholder surface is org/person-scoped; out-of-scope → 404.
- Reporting is authorization-filtered-before-aggregation.
- Integration ports present and **inert** (no live I/O).
- Single Alembic head; migration reversible; **all v0.9.13 gates hold** (isolated-DB
  suite green, Ruff gate, CI); **no behavior regression** in tax/benefits/wealth.
- RC document PASS.

---

## 16. Estimated effort

Comparable to 0.9.11 (migrations r→u, ~8 phases). **~6–9 weeks** single-engineer,
phase-gated. The party/producer normalization adds tables but replaces the JSON-blob
approach rather than adding risk. Reuse of the 0.9.11 platform compresses this ~2–3×
versus a standalone build.

---

## 17. Architecture decisions (final)

All five decisions are final for v0.10.0. Each states a recommendation, rationale,
rejected alternatives, long-term tradeoffs, migration implications where the decision is a
one-way schema door, and whether the architecture document needs to change.

### AD-1 — Carrier is an Organization node + `insurance_carrier_profiles` (1:1), not a standalone carrier table

**Recommendation.** Model a carrier as a `relationship_entities` node
(`entity_type='insurance_carrier'`) with a 1:1 `insurance_carrier_profiles` row for
regulated insurer fields (NAIC company code, AM Best rating, appointment status).
Downstream tables reference it by a stable `carrier_id`.

**Why.** A carrier IS an organization the firm has relationships with — appointments,
broker-of-record, agency hierarchy — all of which are already the relationships graph.
Carrier-as-org reuses org naming/address/profile, `organization_in_scope` authorization,
and audit/timeline, and mirrors exactly how employers are modeled
(`relationship_entities` + `organization_profiles`). Regulated fields that the org layer
can't hold go in the profile table, not a JSON blob.

**Alternatives considered.** (A) Standalone `insurance_carriers` table — cleaner queries
and self-contained, but reinvents appointments/BoR (a parallel `insurance_appointments`
table), reinvents org profile/address, bypasses org auth, and adds a second "kind of
organization" the platform doesn't know about (violates Refinement 1). (B) Carrier-as-org
with NAIC/rating in `relationship_entities.details` JSON — rejected: regulated fields must
be structured/queryable. (C) Standalone table referencing an org node — two sources of
truth, over-engineered.

**Long-term tradeoffs.** Pro: appointments/BoR/hierarchy and record-scope "just work";
consistent with employer modeling; new carrier fields = extend the profile. Con: requires
adding `insurance_carrier` to the two `ORG_ENTITY_TYPES` frozensets
(`organization_service.py`, `relationships.py`); "all carriers" queries filter by
`entity_type` + join profile; carriers (counterparties) share `relationship_entities` with
client-orgs, mitigated by the `entity_type` discriminator and `insurance.*` (not
`organization.*`) capability gating.

**Migration implications if reversed later.** Moving to a standalone table would: create
`insurance_carriers`, backfill from `relationship_entities`+profiles, repoint the
`carrier_id` FKs on `insurance_policies` and `insurance_product_families`, migrate
appointment relationships into a new table, then drop the carrier entity nodes. Bounded and
mechanical (data migration + FK repoint), but touches multiple FKs. **De-risked now** by
referencing carriers everywhere via a stable `carrier_id` — so only carrier *resolution*
changes if reversed, not every downstream table. This is the more expensive of the two
one-way doors and is the reason AD-1 gets explicit scrutiny.

**Architecture-document impact.** None — §4.1 already specifies carrier-as-org + profile.
This decision ratifies the written design.

### AD-2 — InsuranceCase ↔ Engagement is 1:1

**Recommendation.** Each `insurance_case` references exactly one `engagement` (unique
`engagement_id`); each engagement backs at most one case.

**Why.** A case and its engagement are two facets of one thing: the engagement is the
platform's work/revenue/workflow record; the case is the domain's regulated-artifact
coordinator for that same work. 1:1 keeps them one logical unit and keeps revenue
(`insurance_commissions`), `work_items`, and workflow attribution unambiguous — a single
engagement owns the money and the work for a single case. Multiple *proposed policies*
already live under one case, so a "term + annuity" case is one case with two proposed
policies and one engagement, not two engagements.

**Alternatives considered.** (C) N:1 (case spans multiple engagements) — only needed if the
firm bills per-policy-line separately; today billing is at engagement granularity, so not
required. (B) 1:N (one long-lived engagement, many cases over time) — rejected because an
engagement is scoped work (`open→closed`), not a durable relationship; the durable
container is the household/person, under which many cases group. (E) case with no
engagement — rejected, loses service-line/revenue/work/workflow reuse. (F) engagement only,
no case — rejected in the Refinement-2 determination.

**Long-term tradeoffs.** Pro: unambiguous revenue/work attribution; simplest join; matches
"engagement = scoped work." Con: creating a case always creates an engagement (two rows —
cheap, and it buys real reuse); no shared long-lived container across cases — served
instead by the household/person anchor.

**Migration implications if reversed later.** 1:1 is the low-regret default because
**relaxing it is additive and cheap**: to allow N:M, add an `insurance_case_engagements`
join table, backfill from the `engagement_id` column, drop the column — no data loss, no
merge. Tightening the reverse direction (N:M → 1:1) would be expensive (must resolve/merge),
which is exactly why we start strict. AD-2 is a one-way door that is inexpensive to walk
back through.

**Architecture-document impact.** None — §3 already specifies the 1:1 coordinator. This
decision ratifies the written design.

### AD-3 — iPipeline / KaiZen / carrier integrations ship as disabled provider ports; live integration deferred

**Recommendation.** Ship interfaces + registry + disabled stubs (honest `not_connected`),
following `benefits_providers.py`. No live I/O in v0.10.0; live adapters are a later release.

**Rationale.** Live integration carries vendor-contract, credential, and compliance risk
that must not gate the domain build. Ports let the domain be built and tested against stable
interfaces now, and turned on later without reshaping the domain. iPipeline/KaiZen are pure
greenfield (absent from the codebase), so there is nothing to preserve by rushing them.

**Alternatives rejected.** (A) Build live integration in v0.10.0 — couples the release to
vendor onboarding, credentials, and carrier data variance; expands the compliance surface
before the domain is even proven. (B) Omit the ports entirely and add integration as an
afterthought — rejected: designing the port boundary now keeps live adapters from later
forcing domain-model changes.

**Long-term tradeoffs.** Pro: the domain ships and is usable via manual entry; enabling a
port later is isolated. Con: until a port is live, in-force/commission data is entered by
hand (acceptable at launch scale; the ports are the migration path off manual entry).

**Migration implications.** Enabling a provider later = a new class + a registry row +
config; **no schema change**. Very low lock-in.

**Architecture-document impact.** None — §11 already specifies disabled ports. This decision
ratifies it.

### AD-4 — v0.10.0 is individual life & annuities only; underwriting = tracking, not automated decisioning

**Recommendation.** Limit v0.10.0 to **individual life insurance and annuities**. Model
underwriting as **status/requirements/APS tracking** and illustrations as **documents +
structured metadata** — not automated underwriting decisions or illustration generation.

**Rationale.** Two-part decision. (1) *Scope:* group/employer benefits already ship in
0.9.11; P&C and group life are different products, parties, and regulation — folding them in
would multiply the data model and the regulatory surface (R1) without a shared lifecycle.
Individual life + annuities is one coherent lifecycle (case → application → underwriting →
issue → in-force → review). (2) *Underwriting layer:* the firm is an advisory/brokerage, not
a carrier — underwriting *decisions* and illustration *generation* are carrier/iPipeline
functions. Building them would reimplement carrier/actuarial systems at the wrong layer.

**Alternatives rejected.** (A) Include P&C or group life now — rejected: separate lifecycle,
parties, and regulation; large scope-and-risk multiplier for no reuse gain. (B) Build
underwriting decisioning / illustration generation — rejected: actuarial, carrier-specific,
and duplicative of iPipeline; out of layer.

**Long-term tradeoffs.** Pro: a tight, provable domain with a single regulated lifecycle;
tracking-only keeps Client360 as the system of *record and coordination*, not a carrier
engine. Con: producers still generate illustrations and receive underwriting decisions in
carrier tools — Client360 stores and coordinates, it does not originate them (this is the
correct boundary for a brokerage).

**Migration implications.** Adding P&C/group life later is additive (new product families
+ domain tables); richer structured underwriting/illustration data is additive
(columns/child tables on the existing records). Low lock-in in both directions.

**Architecture-document impact.** None structurally — the scope line at the top and §11/AD-4
already state this. This section now records the scope-limit explicitly as a ratified
decision.

### AD-5 — Regulatory sign-off is a named-owner + per-phase-gate process (recommended); the owner is the firm's to name

**Recommendation.** Adopt this process, and name the owner before Phase 2:
1. **Named accountable owner** — a licensed compliance principal / supervisory officer at
   the firm (not engineering) owns regulatory correctness for suitability, replacement/1035,
   licensing, and CE.
2. **Rules as reviewable data** — every regulated rule set (suitability criteria,
   replacement/1035 disclosure requirements, CE requirements, license-line rules) is modeled
   as seeded/config data, not code, so the owner can read and approve it without reading
   source.
3. **Per-phase sign-off gate** — the phases that introduce regulated logic
   (Phase 2 suitability/new-business, Phase 3 replacements/1035/reviews, Phase 4
   licensing/CE) **do not pass their RC gate without a recorded sign-off** from the owner on
   that phase's rule-set data. The sign-off is captured as a dated artifact (e.g. a checked
   line in that phase's RC document).

**Rationale.** R1 (regulatory correctness) is the top release risk and a compliance
liability, not a bug class — engineering cannot self-certify it. A named owner + reviewable
data + a hard phase gate converts an open-ended risk into an auditable, blocking checklist
item.

**Alternatives rejected.** (A) Engineering self-certifies — rejected: outside engineering's
authority and competence; creates undisclosed liability. (B) One big compliance review at
release end — rejected: defers discovery of rule errors to the most expensive moment and
can't unwind merged phases. (C) No formal gate — rejected: R1 unmanaged.

**Long-term tradeoffs.** Pro: compliance defensibility; rule changes are data edits the owner
re-approves, not code changes. Con: the per-phase gate adds a human dependency to Phases 2–4
that can stall a phase if the owner is unavailable — which is the correct failure mode
(better a stalled phase than an unreviewed compliance rule).

**Architecture-document impact.** This section is the change — it records the recommended
process. The remaining input is the firm naming the owner; §13 (dependencies) already flags
this as a release dependency.

### AD-5 — RESOLUTION (2026-07-16): compliance owner status → **Phase 2 BLOCKED**

**Business owner (recorded).** **Michael Shelton** is the business owner for **workflow and
operational requirements** — what the process should do, how cases and reviews flow, what
staff see. His business approval is authoritative for that scope.

**Business approval is NOT regulatory certification.** Neither engineering nor Michael's
business sign-off constitutes regulatory certification of suitability, replacement/1035,
licensing, or CE rule sets — **unless** Michael is himself the appropriately licensed
compliance principal responsible for these rules (see the checklist below). That has not
been established, so:

**Accountable compliance reviewer: NOT YET NAMED → Phase 2 is BLOCKED** for any regulated
logic (suitability rules, recommendations, approvals, replacement/1035 disclosure logic,
CE/licensing rules, or compliance conclusions). Phase 2 does not begin until either (a) the
reviewer below is named, or (b) the requester explicitly authorizes a **non-regulated
skeleton phase** containing none of the above.

**Reviewer qualification & authority checklist** (the person named must satisfy all):
- [ ] **Actively licensed** — resident life & annuity producer license in the firm's
      operating state(s), in good standing.
- [ ] **Supervisory authority** — a designated compliance principal / supervisory officer
      (e.g., the firm's CCO or a licensed supervisor) with authority to approve firm
      procedures and to **block a release**.
- [ ] **Securities registration if variable products are in scope** — VUL / variable
      annuities are securities; a FINRA-registered principal (e.g., Series 26/24 over
      Series 6/7) must supervise. If v0.10.0 stays non-variable, note the exclusion.
- [ ] **Annuity suitability competence** — current on the NAIC *Suitability in Annuity
      Transactions* model reg (and the applicable state adoption / best-interest standard).
- [ ] **Replacement / 1035 competence** — current on the NAIC *Life Insurance & Annuities
      Replacement* model reg, state replacement disclosure timing, and IRC §1035 exchange
      rules.
- [ ] **CE / licensing competence** — current on state CE and license-line/appointment rules.
- [ ] **Accountable & independent** — named, dated sign-off; not the same person who authored
      the rule set, where the firm's structure allows separation.

**Sign-off artifact** (recorded per regulated phase before its RC gate passes; suggested
`docs/compliance/INSURANCE_SIGNOFF_<phase>.md` or a checked block in the phase's RC doc):

| Field | Content |
|---|---|
| Rule-set version | the version/hash of the reviewed rule-set data (e.g. seed migration revision + rule-set id) |
| Reviewer | name + role + license/registration numbers |
| Date | ISO date of review |
| Scope reviewed | which rule sets (suitability / replacement-1035 / licensing / CE) and product lines |
| Approval status | approved \| approved-with-exceptions \| rejected |
| Comments / exceptions | required conditions, carve-outs, or follow-ups |

**Compliance gate.** A regulated phase (Phase 2 suitability/new-business, Phase 3
replacements/1035/reviews, Phase 4 licensing/CE) **cannot pass its RC gate without a
completed, approved sign-off artifact** from the named reviewer for that phase's rule sets.
Missing or rejected artifact = phase blocked.
