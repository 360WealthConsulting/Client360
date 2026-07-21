# ADR-008 — Compliance decision and reviewer-authority boundaries

## Status
Accepted

## Date
2026-07-21

## Decision owners
Compliance Architecture; Platform Architecture; Business Operations Owner (Michael Shelton —
workflow/operational requirements only). **Authorized compliance reviewer: Not yet designated**
(the repository does not name one; see below).

> **Ownership distinctions for this ADR:**
> - **Business owner** (Michael Shelton) — owns workflow/operational requirements; his approval is
>   **not** regulatory certification.
> - **System owner** — Platform Architecture, owns the code/enforcement.
> - **Authorized compliance reviewer** — a principal with recorded Reviewer Authority for a
>   rule/gate; **Not yet designated** in the repository.
> - **Regulatory sign-off authority** — the authorized compliance reviewer acting within their
>   authority; nobody else may certify compliance.

## Context
Regulated determinations (suitability, replacement/1035, licensing, CE, retirement-plan fiduciary,
insurance rule sets) must not be certifiable by an advisor completing work, by a business-owner
workspace, or by anyone lacking recorded authority. The system must separate governance,
review, decision, and authority so that operational convenience never becomes regulatory approval.

## Decision
Compliance **must** keep four concerns separate: **Rule Catalog** (governed rule definitions +
versions), **Compliance Review** (`compliance_reviews` + append-only `compliance_decisions`),
**Reviewer Authority** (`reviewer_authorities` + append-only `reviewer_authority_events`), and
**business/operational approval** — which is distinct from **regulatory sign-off**.
- Advisor completion of work or an annual-review checklist **is not** compliance approval.
- Business-owner or operational approval **is not** regulatory certification.
- Final approval **must** double-gate on `compliance.review.decide` **AND** a recorded Reviewer
  Authority for the rule/gate (and a Rule-Catalog version match); otherwise the review **moves to**
  `blocked_pending_authorized_reviewer` — never silently approved.
- Regulated rule sets (suitability, replacement/1035, licensing, CE, retirement-plan fiduciary,
  insurance) **require** an appropriately authorized compliance reviewer.
- Composition workspaces **may** display compliance **status/counts** but **must not** certify
  compliance, and **must not** expose comments/evidence without `compliance.review.read`.

The required sign-off artifact fields are: **rule-set version, reviewer, date, scope reviewed,
approval status, comments/exceptions.**

## Alternatives considered
1. **Single capability gate on decisions** (`compliance.review.decide` only). Rejected: capability
   alone does not prove rule-specific authority; D.7/D.8 added the recorded-authority requirement.
2. **Let operational/business approval stand in for compliance.** Rejected: conflates operational
   convenience with regulatory certification — explicitly prohibited.

## Reasons for the decision
Separating governance/review/authority and double-gating final approval prevents unauthorized
regulatory sign-off while still letting operations run day-to-day.

## Consequences
### Positive consequences
- No unauthorized regulatory approval; a clear, auditable sign-off artifact.
- Composition surfaces can show status without ever certifying.

### Negative consequences and tradeoffs
- With no reviewer authority yet recorded, regulated approvals remain blocked (by design) until an
  authorized reviewer is designated.
- Compliance reviews carry no business link today (documented limitation).

## Enforcement
- `app/services/compliance/reviews.py`: decision double-gate (`compliance.review.decide` +
  `reviewer_authority(...)`), `blocked_pending_authorized_reviewer` state; append-only
  `compliance_decisions` (migration `e7c8o9m1p2q3`). Reviewer Authority: `f8a9u1t2h3r4`.
- Composition shows counts only: `annual_review._compliance_summary`,
  `business_owner._compliance_summary`.
- Tests: `tests/test_compliance_review_ledger.py`, `tests/test_reviewer_authority_admin.py`.

## Exceptions
`administrator` holds all capabilities but still requires recorded authority to finalize a
regulated approval (the double-gate is not a capability-only bypass). No other exception approved.

## Revisit conditions
When an authorized compliance reviewer is designated, update this ADR's Decision owners. Any change
to regulated rule sets or approval authority **requires compliance sign-off** and a new/superseding
ADR.

## References
- `app/services/compliance/{reviews,rule_catalog,reviewer_authority}.py`
- migrations `e7c8o9m1p2q3_compliance_review_ledger.py`, `f8a9u1t2h3r4_reviewer_authority_admin.py`
- `docs/PLATFORM_ARCHITECTURE.md` §14 (Compliance architecture)
- `tests/test_compliance_review_ledger.py`, `tests/test_reviewer_authority_admin.py`, `docs/PHASE_D7*`, `docs/PHASE_D8*`
