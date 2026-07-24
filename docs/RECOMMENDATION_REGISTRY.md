# Recommendation Registry (Phase D.46)

`REGISTRY` in `app/services/recommendations/registry.py` is the **authoritative declarative catalog** of
every advisor recommendation type the Operational Intelligence layer can surface. See
[`ADR-051`](adr/ADR-051-operational-intelligence.md).

## Recommendation type record
Each `RecommendationType` declares:
- `key` — the recommendation type;
- `owner_service` — the authoritative owner of the underlying fact;
- `source_services` — the composed source(s) the engine reads;
- `default_severity`;
- `category` — attention | review | workload | opportunity | governed | pipeline | bizdev | firm |
  communication;
- `lifecycle` — active | experimental | deprecated | retired;
- `visibility` — all D.46 recommendations are internal (advisor/staff);
- `explanation_template` — the "why" used when the source does not supply its own;
- `evidence_kind` — what evidence supports the type;
- `deep_link_target` — the authoritative surface to open;
- `workflow_owner` — the authoritative service that owns resolution;
- `prerequisites`, `suppression` — governed prerequisites + suppression rules.

## Registered types
`client_attention` (exceptions), `review_cadence` (portfolio/annual reviews), `task_workload` (work queue),
`meeting_prep` (scheduling), `service_opportunity` (portfolio/insurance opportunities),
`governed_recommendation` (compliance-owned), `pipeline_health`, `bizdev_health`, `firm_health` (domain
observation sets), and `communication_followup` (D.44 engagement).

## Classification
`classify_signal(signal_dict)` deterministically maps an advisor_intelligence `Signal` onto a registered
type by its category + source-service/route keywords: `recommendation` → `governed_recommendation`;
`opportunity` → `service_opportunity`; `operational` → `task_workload` / `meeting_prep` / `review_cadence` /
`client_attention` (the default). No probabilistic classification.

## Explainability contract
Every recommendation the engine emits references authoritative evidence and deep-links to its workflow owner.
The registry supplies the fallback explanation template + the deep-link target + the workflow owner; the
authoritative source (the Signal or observation) supplies the concrete why + evidence. A recommendation
without a why + evidence + deep link is dropped (`Recommendation.is_explainable`).

## Onboarding a new recommendation type
Add a `RecommendationType` (via `_t(...)`) with its owner, sources, explanation template, evidence kind,
deep-link target, and workflow owner. If it derives from a new advisor_intelligence category/kind, extend
`classify_signal`. Governance verifies completeness + single ownership (no duplicate keys). Per
ADR-018/019/020, a genuinely new recommendation KIND is added to its authoritative owner first, then surfaces
here automatically.

## References
`app/services/recommendations/registry.py`, `app/services/recommendations/adapters/signals.py`,
`app/services/recommendations/governance.py`, `tests/test_operational_intelligence.py`, ADR-051.
