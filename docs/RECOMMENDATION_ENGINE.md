# Recommendation Engine (Phase D.46)

The recommendation engine (`app/services/recommendations/service.py`) is a **read-only composition** over
the authoritative recommendation sources. It normalizes them into one explainable `Recommendation` contract,
deduplicates, suppresses, prioritizes, and aggregates for households. It never re-derives domain logic, never
mutates, and never invents a recommendation. See [`OPERATIONAL_INTELLIGENCE.md`](OPERATIONAL_INTELLIGENCE.md)
and [`ADR-051`](adr/ADR-051-operational-intelligence.md).

## Public API
- `client_recommendations(principal, person_id)` — explainable recommendations for one client. `None` when
  out of scope (route → 404); disabled envelope when gated off.
- `household_recommendations(principal, household_id)` — aggregated + deduplicated (by type + title across
  members) + household-prioritized.
- `workspace_recommendations(principal)` — the Advisor Workspace panel: book-scoped highest-priority
  recommendations + the domain observations + the work-queue workload distribution.
- `recommendation_summary(principal, *, person_id|household_id)` — counts by category/severity + top (backs
  the Client 360 / Household 360 sections + AI grounding).
- `explain_recommendation(principal, recommendation_id, *, person_id|household_id)` — the full explanation.

## Pipeline
1. **Gate + policy** — `gate.enabled()` + the surface flag + `gate.policy_ok(area)` (RBAC checked
   separately by the route).
2. **Scope** — `record_in_scope(principal, entity_type, id)`; out of scope → `None`.
3. **Compose** — normalize the authoritative signals/observations via the adapters:
   - `signals` — advisor_intelligence `Signal` objects → Recommendations (fields carried verbatim from the
     Signal's title/summary/explainability/evidence/route).
   - `observations` — pipeline/bizdev/firm observation dicts → Recommendations + the work-queue workload
     rollup.
   - `composed` — the one thin communication-followup rule over the D.44 engagement summary.
4. **Explainability gate** — drop any recommendation lacking why + evidence + deep link
   (`Recommendation.is_explainable`); count `missing_evidence`.
5. **Dedup** — by recommendation id (stable, source-qualified); households also collapse duplicates by
   (type, title) across members.
6. **Prioritize** — by priority rank (critical→informational) then id.
7. **Package** — `{enabled, recommendations, total, counts{by_category, by_severity}}` (+ `workload` for
   the workspace panel).

## Recommendation model
Frozen dataclass (`model.py`): recommendation_id, type, category, priority, severity, title, summary,
explanation, governing_rule, evidence, authoritative_source, workflow_owner, confidence, generated_at,
deep_link, recommended_next_action, visibility, related_person_id/household_id, metadata.
`priority_rank` orders; `is_explainable` (why + evidence + deep link) is the hard emit gate.

## Determinism (no ML)
Confidence is deterministic — `1.0` for operational signals, source-supplied otherwise; observations are
`1.0` (fixed thresholds). There is no probabilistic scoring, no model, no randomness. Governance forbids ML
imports and non-deterministic confidence.

## References
`app/services/recommendations/service.py`, `app/services/recommendations/adapters/*`,
`app/services/recommendations/model.py`, `tests/test_operational_intelligence.py`, ADR-051.
