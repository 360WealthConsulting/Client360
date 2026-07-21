# ADR-006 — Advisor Intelligence as deterministic computation

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Advisor Intelligence); Compliance Architecture (governed
recommendations); Business Operations Owner (Michael Shelton).

## Context
Advisors need surfaced recommendations and opportunities. Doing this with AI/LLMs would introduce
non-determinism, unexplainability, and compliance exposure for regulated advice. The platform
instead needs recommendations that are explainable, reproducible, and tied to governing rules.

## Decision
Advisor Intelligence **must** be **deterministic computation** over structured facts and rule
definitions — **not** AI, an LLM, or machine learning.
- It **consumes** structured operational data and rule definitions and **produces** `Signal`
  records in categories (`recommendation`, `opportunity`, `review`, `exception`, `task`,
  `meeting`), with stable identifiers and, for recommendations, durable
  `RecommendationMeta(recommendation_type, governing_rule, rule_version, compliance_owner,
  approval_status)`.
- Consumer workspaces **must not** duplicate or re-implement recommendation generation.
- Recommendations **may** be grouped **only** by durable structured categories (e.g.
  `recommendation_type`); they **must not** be categorized by uncontrolled keyword matching.
- Because recommendations are recomputed at render time and carry **no durable timestamp**, they
  **must not** be treated as durable timeline history unless persisted elsewhere (ADR-009).

Annual Review and Business Owner Planning **reuse** Advisor Intelligence via `get_client_signals`
rather than recomputing it differently.

## Alternatives considered
1. **LLM/AI-generated recommendations.** Rejected: non-deterministic, unexplainable, and
   compliance-hostile for regulated advice.
2. **Per-workspace bespoke recommendation heuristics.** Rejected: forks logic, produces divergent
   recommendations, and defeats the governing-rule/compliance linkage.

## Reasons for the decision
Determinism gives explainability, reproducible ids, and a clean tie to the Rule Catalog and
Compliance — prerequisites for governed advice.

## Consequences
### Positive consequences
- Reproducible, explainable recommendations with governing-rule provenance.
- One generation path reused by all consumers; no divergence.

### Negative consequences and tradeoffs
- No durable recommendation history (recomputed each render) → excluded from the timeline.
- New recommendation types require new deterministic producers, not a prompt.

## Enforcement
- `app/services/advisor_intelligence.py` (`get_client_signals`, `Signal`, `RecommendationMeta`);
  no AI/LLM dependency.
- Consumers reuse it: `advisor_work.create_from_recommendation`, `annual_review`,
  `business_owner._group_recommendations` (groups by `recommendation_type` only).
- Golden regression pins serialized signals/panels: `tests/test_intelligence_refactor_regression.py`.
  Reuse-not-regenerate tests: `tests/test_annual_review.py`, `tests/test_business_owner.py`.

## Exceptions
None currently approved. Advisor Intelligence is not AI.

## Revisit conditions
If durable recommendation persistence is introduced (giving recommendations real timestamps), a
new ADR should define how they become timeline-eligible.

## References
- `app/services/advisor_intelligence.py`; `app/services/compliance/rule_catalog.py`
- `docs/PLATFORM_ARCHITECTURE.md` §12 (Advisor Intelligence architecture)
- `tests/test_intelligence_refactor_regression.py`, `docs/PHASE_D5*`
