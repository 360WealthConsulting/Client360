# Phase D.5C — Deterministic Advisor Opportunities

Seventh production slice of `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md` (§4, §7). Extends
Advisor Intelligence beyond operational status (D.5B) into **deterministic advisor
opportunities** — factual, evidence-backed reasons a client deserves advisor
attention. An opportunity is **not** advice, a recommendation, suitability,
compliance, or a required action. Implemented on `release/0.13.0`.

## Opportunity categories implemented
Three, each **fully supported by an existing authoritative service** (all
category `opportunity`, `PolicyGate.NONE`, Medium priority, deterministic
confidence 1.0):

| Opportunity (registry key) | Authoritative source read | Meaning |
|---|---|---|
| **Portfolio Review** (`portfolio_review_opportunity`) | `portfolio.accounts_review_approaching` *(new read)* | Annual portfolio review is **approaching** (within 30 days of the 365-day cadence, **not** yet overdue — disjoint from the D.5B overdue signal). |
| **Insurance Review** (`insurance_review_opportunity`) | `insurance.reviews_due_for_people` *(new read)* | An insurance servicing review is **due** (open review with a due date within the window). No coverage/replacement/suitability analysis. |
| **Beneficiary Review** (`beneficiary_review_opportunity`) | `portfolio.accounts_missing_required_beneficiary` *(new read)* | An **IRA account has no active beneficiary** — a missing *required* designation. Never inferred beyond that explicit predicate. |

### Deferred (not implemented) — no authoritative cadence read exists
- **Tax Planning Review** — `tax_domain.client_engagement_summary` returns only an
  active-engagement **count**; there is no authoritative "prior return complete /
  filing season approaching / annual planning review due" cadence read. Implementing
  it would require inventing tax cadence/date logic (a regulated domain) — out of
  scope. **Deferred.**
- **Retirement Plan Review** — `benefits_domain.client_benefits_summary` returns only
  an employment **count**; the retirement/benefits cadence that exists is
  organization-anchored renewal detectors/obligations, not a person/household-keyed
  "retirement review due" read. **Deferred.**

These two are intentionally **not registered** (asserted by test).

## Reused authoritative services
The Advisor Intelligence layer composes existing reads only; it queries no domain
tables directly. Three **smallest-possible read additions** were made to the owning
services (an exact person-scoped cadence read was missing):
- `portfolio.accounts_review_approaching(person_ids, *, cycle_days=365, within_days=30, today)`
  — reuses the same `accounts.last_review_date` cadence as `accounts_due_for_review`;
  deliberately disjoint from it (overdue excluded).
- `portfolio.accounts_missing_required_beneficiary(person_ids)` — reuses the **exact**
  IRA-without-active-beneficiary predicate the firm-portfolio metric already uses.
- `insurance.reviews_due_for_people(person_ids, *, within_days=45, today)` — reuses the
  existing `insurance_policy_reviews.due_date`/`status` cadence; resolves the client
  from the review's policy/case anchor.

All three mirror the existing scope contract (`None`=read_all, empty=`[]`).

## Explainability and evidence
Every opportunity carries a factual title, factual summary, `why`, authoritative
`source_service`, a `SourceRecord`, evidence fields sufficient to reproduce it,
deterministic `confidence=1.0`, `PolicyGate.NONE`, and a protected destination route
(`/people/{id}`). Factual language only — e.g. *"Annual portfolio review is
approaching."*, *"Insurance servicing review is due."*, *"Beneficiary information is
missing on a retirement account."* No "recommend/should/suitable/advice" language, no
scoring, no policy conclusions.

## Deterministic IDs and ordering
Each id is `f"{opportunity_type}:{source_record_type}:{source_record_id}"` (e.g.
`portfolio_review_opportunity:account:42`, `insurance_review_opportunity:insurance_review:7`).
Globally unique per underlying record → the same fact never duplicates; `_collect`
also dedupes by id. Ordering is `(priority.rank desc, id asc)` — deterministic, no
scoring.

## Authorization
Preserves the D.5B **scope-first** behavior. Scope is resolved **before** any
producer runs and producers read strictly by the resolved `person_ids`:
- `get_client_signals` / `get_household_signals` enforce record scope first; an
  inaccessible person/household returns `()` and **never reaches producer logic**
  (proven by tests: the same records produce for a `record.read_all` principal, so
  the empty result is the authorization gate, not absent data).
- `get_dashboard_signals` scopes to the advisor's book; household scope resolves to
  member person ids.
- Every opportunity is anchored to a record within the accessible `person_ids`, so
  evidence, routes, dates, and summaries can never reference an inaccessible record.
  Destination routes are independently authorization-protected.

## UI reuse
A single shared macro `templates/components/intelligence.html::signals_panel` renders
all signals (operational + opportunities), grouped by category, reusing the existing
`table.data`, `ui.badge`, `ui.empty`, links, and tokens. It is used by **one** panel
implementation in three places (no duplicate panel, no in-template generation):
- **Advisor Workspace dashboard** (`GET /workspace`) — full columns, grouped.
- **Client 360 workspace** (`/people/{id}`) — compact section reusing
  `get_client_signals`.
- **Meeting Workspace brief** (`/workspace/meetings/{id}`) — compact section reusing
  `get_client_signals`.
No scores, generated content, or approve/reject/dismiss/snooze/workflow/task-creation
controls are rendered. Empty state retained. (The pre-existing legacy "Advisor
Recommendations" sidebar widget on the person profile — `advisor_ai` — is a separate,
untouched feature.)

## Exclusions honored
No Roth conversions, tax-loss harvesting, investment/insurance recommendations,
suitability, fiduciary determinations, compliance decisions, rollover, Social
Security, estate planning, risk scores, AI/LLM/ML/embeddings/vector/predictive; no
workflow creation, notifications, task creation, or signal persistence. No new route
(route-count guards unchanged, 319).

## No persistence / action layer
Opportunities are computed **read-only** objects — no signal/history tables,
migrations, acknowledge/dismiss/snooze/assignment state, notifications, workflow/task
creation, or approval/audit records.

## Remaining technical debt
- **Tax Planning** and **Retirement Plan** opportunities remain deferred pending an
  authoritative cadence read (or a governed decision to add one to the owning domain
  service). No cadence logic will be invented in the intelligence layer.
- The set-scoped reads are person-keyed → **under-inclusive** for household-only
  (null person_id) / organization-anchored records (safe: never over-inclusive/
  leaking).
- Governed signal disposition (acknowledge/evidence ledger) and any `[Policy-gated]`
  regulated opportunity remain deferred to a governed, compliance-owned phase.
