# Phase D.12 — Business Owner Planning Workspace

## Objective
One advisor-facing workspace that answers *"what planning opportunities, risks, obligations,
and follow-up items exist across this client's businesses?"* — letting an advisor see a
business owner's full planning picture without hopping between contact, business, tax,
retirement, benefits, insurance, work, compliance, and annual-review screens. It reflects the
firm's tax-centered planning model for business owners. It is a **composition and
structured-data phase** — not a planning engine. It does not calculate tax returns, provide
legal conclusions, or automate recommendations / compliance / plan design / insurance
placement / client communications.

## Firm-specific planning model
Business owners are planned across entity structure, ownership, owner compensation, tax
status, retirement plans, group benefits, insurance, succession, buy-sell, key-person risk,
continuity, estimated-tax/QBI/entity-selection considerations, related businesses, and family
ownership — alongside outstanding Advisor Intelligence, Advisor Work, compliance reviews,
annual-review sessions, and recent activity. The workspace distinguishes **known / imported /
advisor-entered / calculated-presentation / observation / missing / conflicting** facts and
never displays assumptions as confirmed facts.

## Architecture
```
Client & Household · Business Entities · Tax · Retirement · Benefits · Insurance ·
Advisor Intelligence · Advisor Work · Activity Timeline · Compliance · Annual Review
                                  │  (read-only)
                                  ▼
                    Business Owner Planning Service  (app/services/business_owner.py)
                                  │
                                  ▼
     Business Owner Planning Workspace   /business-owner/{person_id}[/business/{business_id}]
```
**Dependency direction (test-enforced):** existing domains never import Business Owner
Planning; it reads existing services and persists only its own planning records; routes hold
no planning policy; templates compute no planning results; no source-domain record is mutated
by rendering.

## Source-domain inventory (audit result)
| Domain | Verdict | Reuse point |
|---|---|---|
| Business entity + ownership | **EXISTS** | `relationship_entities`(business) + `organization_profiles` (EIN Fernet-encrypted, `entity_form`, status) + ownership `relationships`/`relationship_ownership`; `organization_service` |
| Tax | **workflow/document only** | `tax_engagements`/`tax_engagement_returns` (year/form/filing/lifecycle). K-1, W-2 wages, guaranteed payments, distributions, QBI, S-election, estimated tax, accounting method — **ABSENT** |
| Retirement + Benefits | **EXISTS (org-scoped)** | `benefit_plans`(organization_id) + plan types + years + obligations; `benefits_domain.list_plans`. Contribution $, owner participation, CB/DB funding — **ABSENT** |
| Insurance | **EXISTS (life/annuity), business-linkable** | `insurance_policies.organization_id` + parties + carrier/face/premium/status. Policy **purpose** (key-person/buy-sell/…) — **ABSENT**; disability/LTC — **ABSENT** |
| Succession / buy-sell / valuation / continuity / key-person | **NOT FOUND** | Only a `buy_sell_agreement` relationship label — no structured home |
| AI / Work / Timeline / Compliance / Annual Review | **EXISTS** | `get_client_signals`, `person_work`, `client_timeline`, `person_reviews`, annual-review reads |
| Scope | person / household / **organization** | `record_in_scope` (entity-type-agnostic), `organization_in_scope` (team-aware). No `business` scope or `business_owner.*` cap existed |

## Additive owning-service reads (genuine gaps only)
- `organization_service.list_person_business_ownership(person_id)` / `list_household_business_ownership(household_id)` — **pure reads** of the ownership graph; unlike `list_owned` they never call `ensure_person_entity`, so rendering cannot create a person entity as a side effect.
- `tax_domain.business_engagements(relationship_entity_id)` — bounded engagement/return rows (form/year/filing/status only).
- `insurance.business_policies(principal, organization_id)` — scope-filtered business-owned policies.
- Benefits `list_plans(organization_id)` — **already existed**, reused directly.

Every existing function is behaviorally unchanged (D.5–D.11 + benefits/tax/insurance/business regression all green).

## Business & ownership model
Anchored to a **person**; a person may own zero/one/many businesses, directly or indirectly,
with family/household ownership; a household may contain multiple owners and businesses; a
business may have multiple owners. These are never flattened into a single business field.
Business-owner status is derived **only** from an active ownership edge — never from
occupation/employer/tax-document presence/free text.

## Workspace sections
**Person workspace** `/business-owner/{person_id}` (bounded — one ownership read, one batched
planning-profile read, one Advisor Intelligence call, one bounded timeline preview,
person-scoped work/compliance): (1) Owner & Household Snapshot, (2) Business Portfolio
(per-business entity facts + this client's stake + planning-status badges + data-quality +
detail link), (10) Tax & Planning Opportunities (reused recommendations grouped by durable
`recommendation_type`), (11) Outstanding Advisor Work, (12) Compliance Summary, (13) Annual
Review Summary, (14) Recent Activity, (15) Missing Information & Data Quality.

**Business detail** `/business-owner/{person_id}/business/{business_id}` (deep single-business):
Business facts, (3) Ownership Structure (all owners, %/voting/direct, incomplete-totals +
conflict detection), (4) Entity & Tax Profile (form/year/filing/status; K-1/W-2/QBI/etc.
explicitly "not tracked"), (5) Owner Compensation (all "Not available"), (6) Retirement Plan
Summary, (7) Group Benefits Summary, (8) Business Insurance & Risk (purpose "unconfirmed"),
(9) Succession & Continuity (the editable planning profile), Data Quality.

This person/detail split keeps the person workspace bounded while covering all 15 sections.

## Routes
- `GET /business-owner/{person_id}` — `business_owner.read`.
- `GET /business-owner/{person_id}/business/{business_id}` — `business_owner.read`.
- `POST /business-owner/{person_id}/business/{business_id}/planning` — `business_owner.planning_update`.
Route count 349 → **352**.

## Data provenance
Ownership edges carry `source` / `confidence_level` / `evidence_source`; the planning profile
carries `source_type` ∈ {advisor_entered, client_reported, document_derived}; tax/benefits/
insurance facts retain their domain provenance. Advisor-entered and imported data are labeled
distinctly; conflicting source facts are surfaced, never silently overwritten.

## Authorization
New capabilities `business_owner.read` (administrator/advisor/operations),
`business_owner.update` (reserved) and `business_owner.planning_update`
(administrator/advisor). `/business-owner/*` is outside the `^/(people|households)` RECORD_PATH,
so the **service enforces scope**: the person must be in record scope; a business is visible
only when validated-owned by the in-scope person **or** independently in the principal's
organization scope (blocks URL enumeration — never inferred from a name match). The workspace
never bypasses tax / benefits / insurance / advisor_work / timeline / compliance /
annual_review permissions.

## Redaction (server-side)
- **EIN** decrypted only with `benefits.sensitive.read`; otherwise `ein_present` is set but the
  value is withheld. The present-flag comes from the ciphertext, so **restricted ≠ missing**.
- **Policy numbers** shown only with `insurance.sensitive.read`.
- Tax / benefits / insurance / retirement / ownership sections are marked **restricted**
  (never exposed) without the owning capability (and, for benefits/ownership, org record scope).
- Compliance is shown as counts only — no comment/evidence exposure.

## Editing boundaries
Read-first. The **only** editable data is the D.12-owned planning profile (succession /
buy-sell / continuity / key-person status + valuation + notes). All source-domain facts are
edited in their own domains via source-record links (business name in Business/Benefits, tax
in Tax, plans in Benefits, policies in Insurance, work in Advisor Work, compliance in
Compliance, reviews in Annual Review).

## Missing-information logic
Deterministic and objective only (no AI): EIN null, entity type null, this owner's ownership %
missing, ownership totals not resolving to 100% / missing percentages, succession undocumented,
buy-sell/continuity/key-person status `unknown`. Uses the EIN present-flag (not view
permission), so restricted data is never mislabeled as missing.

## Tax-data limitations
The tax domain stores workflow/document metadata only. The workspace shows engagement
existence, tax year, return/form type, filing status, and lifecycle status — and explicitly
lists K-1 detail, W-2 wages, guaranteed payments, distributions, QBI facts, S-election, and
accounting method as **not tracked**. Nothing is calculated or inferred.

## Retirement-plan limitations
Plan type/name/sponsor/provider/status/renewal are shown; contribution amounts and plan-design
limits (incl. Cash Balance / Defined Benefit funding) are **not tracked and never calculated**.

## Benefits / Insurance integration
Benefits reuse the existing domain (`list_plans`) — no benefits logic reproduced; a "Manage in
Benefits →" link is provided. Insurance reuses `business_policies`; policy **purpose** is not
modeled and is surfaced as "unconfirmed" rather than guessed.

## Succession & continuity model
The single new table `business_planning_profiles` (1:1 per business) holds
succession/continuity/buy-sell/valuation/key-person facts with a **controlled status
vocabulary** (unknown / not_started / in_progress / documented / review_needed / complete /
not_applicable) and a source label. No legal validation of agreements is performed.

## Advisor Intelligence / Advisor Work / Compliance / Annual Review / Activity Timeline reuse
- **Advisor Intelligence** — reused via `get_client_signals`; recommendations grouped only by
  their durable `recommendation_type` (no second engine, no keyword matching, no invented
  categories).
- **Advisor Work** — reused via `person_work` (person-scoped; work items carry no business link
  in the current model, stated honestly).
- **Compliance** — reused via `person_reviews`, counts only.
- **Annual Review** — reused via `open_session_for` / `list_completed_sessions`; the two
  workspaces link but never merge.
- **Activity Timeline** — a bounded preview via `client_timeline`; the workspace **emits durable
  events** for planning-profile creation / status change / valuation update through the shared
  `add_timeline_event` writer (no second event table), anchored to the owning person. Nothing is
  emitted for page renders, observations, or merely-displayed source data.

## Client 360 & household integration
Client 360 (`people/workspace.html`) gains a "Business owner planning →" link gated
`business_owner.read`; the empty-state workspace states *"No validated business ownership
relationships are currently recorded."* and never auto-creates a business. The household
profile gains an optional, bounded "Business ownership" summary (count, owning members, links)
gated by `business_owner.read`.

## Performance design
Person workspace: one ownership read, one batched planning-profile read, one Advisor
Intelligence call, one bounded timeline preview (5), person-scoped work/compliance reads — no
per-business heavy domain queries. Business detail: bounded per-single-business reads. EIN and
names resolved without N+1. Bounded by the (small) number of businesses a person owns.

## Migration decision
**One migration** (`j0b1u2s3o4w5`, down `i9a1n2r3e4v5`): creates `business_planning_profiles`
(the only new persistence — the audit proved these facts have no authoritative home) with its
CHECK-constrained status/source vocabulary and a unique `business_id`; and seeds the three
`business_owner.*` capabilities. It duplicates **no** business/ownership/insurance/retirement/
benefits/tax/work/compliance/annual-review data. **No backfill** (there is no source of truth
to backfill from — that would fabricate facts); data is prospective only. Upgrade/downgrade
verified.

## Testing
`tests/test_business_owner.py` (17): person-workspace composition, zero-business empty state,
inactive-ownership status, person scope-first, business-scope enumeration block, EIN
restricted-vs-missing, EIN/entity-type missing flags, per-section capability gating, business
detail + retirement reuse, ownership incomplete-totals + missing percentage, planning-profile
lifecycle + controlled vocabulary + durable timeline event, planning scope enforcement,
Advisor Intelligence reuse (no second engine), household integration, Client 360 link,
route auth/render, dependency direction. Full D.5–D.11 + benefits + tax + insurance + business
regression green; **full suite 1492 passed, 5 skipped**; route count 352; ruff clean; migration
round-trips.

## Exclusions honored
No new recommendation engine, AI/LLM/ML, predictive scoring, vector search, OCR, tax
preparation/extraction/filing/projection/savings, QBI/reasonable-comp calculation, retirement
plan-design/Cash-Balance/DB actuarial engine, ERISA/nondiscrimination testing, Form 5500,
benefits/insurance quoting or illustration, replacement analysis, business valuation, legal
drafting/review, CRM/calendar sync, email/SMS/Slack, notifications/reminders, automatic
work/review/compliance creation, trade execution, money movement, document generation, client
portal, mobile, workflow engine, event bus, queues, webhooks, firm-wide dashboard, or
prospect/sales pipeline.

## Limitations & remaining technical debt
- "Servicing advisor" in the snapshot is the current principal — the data model has no per-
  client owning-advisor field.
- Advisor Work and Compliance carry no business link, so their sections are person-scoped
  (stated in the UI).
- Owner compensation, policy purpose, disability/LTC insurance, and tax return content are not
  tracked upstream and are shown as "Not available / not tracked."
- Ownership "conflicts across sources" cannot be represented while the `relationships` table
  keeps a unique `(from, to, type)` edge; the detector remains for the day that constraint or a
  second source is introduced.
- Household business-ownership summary does one ownership read per member (bounded by household
  size).
- Cash Balance / Defined Benefit funding, employer-contribution amounts, and plan limits remain
  untracked upstream — no calculations were added.
