# Enterprise Operational Intelligence (Phase D.46)

The Operational Intelligence layer produces **explainable advisor recommendations** by composing the
platform's existing authoritative recommendation data. It is a governed, read-only composition — **not** a
second recommendation, workflow, opportunity, CRM, analytics, reporting, or AI engine, and **no ML /
predictive / black-box scoring**. See [`ADR-051`](adr/ADR-051-operational-intelligence.md).

## Where it lives
`app/services/recommendations/` — `registry.py`, `model.py`, `service.py`, `gate.py`, `stats.py`,
`metrics.py`, `diagnostics.py`, `governance.py`, `adapters/{signals,observations,composed}.py`. Routes:
`app/routes/recommendations.py`.

## Composition over duplication — authoritative source map
| Recommendation source | Authoritative owner | How the layer reads it |
| --- | --- | --- |
| Client/household/dashboard signals | `advisor_intelligence` (D.5A–D.5D) | `get_client_signals` / `get_household_signals` / `get_dashboard_signals` |
| Pipeline observations | `opportunity.intelligence` | `pipeline_intelligence(principal)` |
| Business-development observations | `bizdev.intelligence` | `business_development_intelligence(principal)` |
| Firm-level observations | `analytics.intelligence` | `firm_intelligence(principal)` |
| Workload distribution | Unified Work Queue | `work_queue_summary(principal)` |
| Communication follow-up | D.44 engagement | `engagement_summary(principal, …)` |

`advisor_intelligence` is the platform's existing deterministic, propose-only recommendation engine; D.46
composes it (and the domain observation sets kept out of its seam per ADR-018/019/020). It never re-derives
domain logic and never mutates.

## What it answers
Which clients need attention · which households have missing planning opportunities · which workflows are
overdue · which reviews are approaching · which client requests are stalled · which compliance items require
action · which service opportunities exist — and, for every item, **why**.

## Recommendation contract
Each recommendation carries: id, type, priority, severity, title, summary, explanation (why), governing
rule, supporting evidence, authoritative source, workflow owner, deterministic confidence, generated
timestamp, deep link, and recommended next action. See
[`RECOMMENDATION_ENGINE.md`](RECOMMENDATION_ENGINE.md) and
[`RECOMMENDATION_REGISTRY.md`](RECOMMENDATION_REGISTRY.md).

## Explainability
Every recommendation answers: why was this generated · which rule generated it · which authoritative
services contributed · which evidence supports it · which workflow owns resolution · which page to open. A
recommendation without an explanation + evidence + deep link is **never emitted** (`Recommendation.is_
explainable` is a hard gate in the model and the engine).

## Runtime & policy governance
Gated through the Runtime Engine (`recommendations.enabled` + workspace/household/ai flags; no env fallback)
AND the Policy Engine (`policy.evaluate("recommendations.*")` composed alongside RBAC — never bypassing
either). Reads require `client.read`; diagnostics require `observability.audit`.

## Rationale: deterministic rules, not opaque ML
Advisors and compliance must be able to answer "why?" for every recommendation. A deterministic composition
over authoritative facts gives a verifiable, reproducible answer (governing rule + source + exact evidence)
with none of the opacity, drift, or model-risk burden of ML — which would add infrastructure for no benefit
in a rule-expressible domain. Confidence is deterministic (1.0 operational; source-supplied otherwise), never
probabilistic. See [`ADR-051`](adr/ADR-051-operational-intelligence.md).

## Integration
Advisor Workspace gains an **Operational Intelligence panel**; Client 360 + Household 360 gain a
**Recommendations** section; AI Assist **summarizes** recommendation counts (it never invents
recommendations). The client portal is unchanged (D.43 reuse only — no recommendation generation). See
[`RECOMMENDATION_GOVERNANCE.md`](RECOMMENDATION_GOVERNANCE.md).

## Relationship to supervisory compliance (D.47)
The D.47 Compliance Intelligence layer composes over the `governed_recommendation` category this layer emits
(among the authoritative compliance engines) to build the supervisor-only view. The advisor-visible
compliance TASKS surfaced in the Advisor Workspace are exactly this layer's governed recommendations —
supervisory findings are never exposed to advisors. See
[`COMPLIANCE_INTELLIGENCE.md`](COMPLIANCE_INTELLIGENCE.md) and ADR-052.

## Relationship to executive reporting (D.48)
The D.48 Executive Reporting layer composes this layer's `workspace_recommendations` output as its
`operational_health` widget (among the authoritative firm reads) to build the operational + executive
dashboards — read-only, never a second analytics engine. See
[`EXECUTIVE_REPORTING.md`](EXECUTIVE_REPORTING.md) and ADR-053.

## References
`app/services/recommendations/*`, `app/routes/recommendations.py`, `docs/platform_architecture_manifest.yaml`,
`tests/test_operational_intelligence.py`, ADR-051.
