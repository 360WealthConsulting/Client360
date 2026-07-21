# Phase D.5D — Compliance-Governed Advisor Recommendations

Eighth production slice of `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md` (§4, §7). Extends
the Advisor Intelligence framework to emit **governed advisor recommendations** from
approved deterministic rules. Implemented on `release/0.13.0`.

A recommendation is **advisor-facing, deterministic, evidence-backed, explainable,
and policy-gated**. It is **not** a client communication, automated advice, an
automated decision, workflow execution, or a compliance/suitability determination.
Recommendations are informational — **nothing is executed, blocked, or persisted.**

## Architecture
Reuses `app/services/advisor_intelligence.py` — no second recommendation engine. The
same `Signal` model and producer registry now carry three separated families, all
rendered together but grouped:

- **Operational Signals** (D.5B) — factual operational status.
- **Advisor Opportunities** (D.5C) — factual reasons a client deserves attention.
- **Advisor Recommendations** (D.5D) — governed, advisor-facing "X may be appropriate
  based on …", each with a governing rule + policy gate.

A `Signal.group` property maps each category to its family for the UI.

## Deterministic recommendation model
The `Signal` model gains an immutable, optional `RecommendationMeta` (present only on
`category == "recommendation"`):

| Field | Meaning |
|---|---|
| `recommendation_type` | e.g. `annual_portfolio_review` |
| `governing_rule` | stable rule id, e.g. `RULE-PORTFOLIO-REVIEW-CADENCE` |
| `rule_version` | e.g. `1.0.0` |
| `compliance_owner` | the accountable role (see below) |
| `approval_status` | `approved` \| `pending_compliance_review` |
| `created_from_rule` | the registry key of the producing rule |

`policy_gate`, `evidence`, and `explainability` are the `Signal`'s own fields (shared
by all families). All recommendation metadata is **immutable and display-only**; there
is **no persistence**.

## Approved recommendation types (only where a deterministic rule exists)
| Recommendation | Governing rule (version) | Source read | Policy gate | Approval status |
|---|---|---|---|---|
| **Annual Portfolio Review** | `RULE-PORTFOLIO-REVIEW-CADENCE` (1.0.0) | `portfolio.accounts_review_approaching` | `NONE` | `approved` |
| **Insurance Review** | `RULE-INSURANCE-REVIEW-CADENCE` (1.0.0) | `insurance.reviews_due_for_people` | `LICENSE_REQUIRED` | `pending_compliance_review` |
| **Beneficiary Review** | `RULE-BENEFICIARY-DESIGNATION-PRESENT` (1.0.0) | `portfolio.accounts_missing_required_beneficiary` | `COMPLIANCE_REQUIRED` | `pending_compliance_review` |

**Deferred (not implemented, not registered):** Annual Tax Planning Meeting and Annual
Retirement Plan Review — no authoritative deterministic cadence rule exists (only
active-count summaries). No thresholds are invented; nothing is inferred.

Each recommendation reuses an **existing** authoritative read (no new engine, no
recreated cadence logic). Wording is advisor-facing and non-instructing — e.g.
*"Annual portfolio review may be appropriate based on the client's review cadence."*,
*"Beneficiary review may be appropriate because required beneficiary information is
absent."* Never "recommend Roth", "client should …", "advisor should …", suitability,
or any regulated conclusion.

## Registry
`register_signal` and `RegisteredSignal` were extended with optional
`governing_rule`, `rule_version`, `compliance_owner`, and `approval_status`. Each
registered recommendation records its rule id, version, policy gate, compliance owner,
approval status, and its deterministic producer (attached to the shared `_PRODUCERS`
seam). Recommendations are never produced outside the registry.

## Policy gates
Reuses `PolicyGate` (`NONE / COMPLIANCE_REQUIRED / LICENSE_REQUIRED /
SUITABILITY_REQUIRED`). Every recommendation **explicitly declares** one. Gates are
**display-only**: no gate enforcement, no approval workflow, no automatic blocking. A
`pending_compliance_review` recommendation is still displayed (informational) with its
pending status visible — the governance boundary is surfaced, not enforced.

## Compliance ownership
- `NONE`-gated (operational cadence) recommendations are owned by `advisor_operations`
  and are `approved`.
- Policy-gated recommendations name the accountable role
  `compliance_reviewer (unassigned — GOV-2/PD-4)` and are `pending_compliance_review`.
  No individual is fabricated: a real owner must be assigned before any gate is ever
  turned into enforcement (`V1_RISK_REGISTER.md` GOV-2, `PRODUCT_DECISIONS.md` PD-4).

## Explainability & evidence
Every recommendation carries a factual title/summary, a `why`, the authoritative
`source_service`, a `SourceRecord`, deterministic evidence (including the governing
rule id and version), deterministic `confidence=1.0`, its policy gate, and a protected
destination route (`/people/{id}`). The evidence is sufficient to reproduce the
recommendation.

## Deterministic IDs & ordering
Each id is `f"{recommendation_type}:{source_record_type}:{record_id}"` (e.g.
`annual_portfolio_review_recommendation:account:42`) — distinct prefixes from signals/
opportunities, so no collision; `_collect` dedupes by id. Ordering is
`(priority.rank desc, id asc)`.

## Authorization
Preserves the D.5C **scope-first** behavior. Scope is resolved before any producer
runs; producers read strictly by the resolved `person_ids`. An inaccessible person/
household returns `()` and **never reaches producer logic** (proven by tests: the same
records produce for a `record.read_all` principal). Dashboard scopes to the book;
household scope resolves to member ids. A recommendation can never be generated for, or
leak evidence/routes of, an inaccessible record.

## UI
The shared renderer `components/intelligence.html::signals_panel` now groups into the
three fixed-order buckets (Operational Signals → Advisor Opportunities → Advisor
Recommendations), reused by the dashboard, Client 360, and Meeting Workspace. The
Recommendations table shows title, summary, priority, **policy gate, governing rule,
rule version, and approval status**, and a destination link. **No** approve / reject /
execute / workflow / task-creation / notification controls are rendered.

## Exclusions honored
No Roth/harvesting/allocation/investment/insurance-replacement recommendations,
suitability, fiduciary/compliance/licensing determinations, Social Security, estate
planning, risk scoring, AI/LLM/ML/embeddings/vector/predictive; no workflow execution,
task creation, notifications, recommendation persistence, acknowledgements, or approval
workflow. No new route (route-count guards unchanged, 319); no new tables; no
migrations.

## Remaining technical debt
- Annual Tax Planning and Annual Retirement Plan recommendations remain deferred
  pending an authoritative deterministic cadence rule (or a governed decision to add
  one to the owning domain service).
- Enabling any policy gate into actual enforcement, and moving a
  `pending_compliance_review` recommendation to `approved`, requires an assigned
  compliance owner (GOV-2/PD-4) — a governed, out-of-band decision.
- Governed recommendation disposition (acknowledgement/decision evidence ledger) is
  intentionally out of scope for this slice.
