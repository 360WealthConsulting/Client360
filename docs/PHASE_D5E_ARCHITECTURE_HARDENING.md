# Phase D.5E — Advisor Intelligence Architecture Hardening

Engineering refactor of `app/services/advisor_intelligence.py`. **Behavior is
unchanged** — no user-visible capability, no new signal/opportunity/recommendation
type, no new rule type, no compliance/AI/workflow/notification/persistence. This
phase consolidates the architecture so the three families (Operational Signals,
Advisor Opportunities, Advisor Recommendations) are consumers of one shared
deterministic rule framework. Implemented on `release/0.13.0`.

## Previous architecture
Each of the 10 producers hand-built its `Signal` inline: computed a deterministic id
via `_signal_id`, constructed a `SourceRecord` (re-stating the record type/id already
in the id), hand-built an evidence tuple of `"key=value"` strings, and constructed an
`Explainability` (re-passing `evidence`, always `confidence=1.0`, mirroring the policy
gate). The registry was already unified but declared in three separate tuples. The
shared template owned the bucket-grouping logic (a fixed list + `selectattr`).

### Duplication removed
| Duplication | Before | After |
|---|---|---|
| Signal construction | 7 inline `Signal(...)` blocks (~15 lines each) + `_recommendation` | one `_emit(...)` builder |
| Explainability construction | 8 identical inline blocks | built once inside `_emit` |
| Evidence tuples | 10 hand-built `(f"k=v", …)` tuples | `_evidence(**pairs)` helper |
| ID + SourceRecord restated | record type/id written twice per producer | id derived from the `SourceRecord` inside `_emit` |
| `confidence=1.0` / gate mirroring | repeated in every producer | owned by `_emit` |
| Registration | `_OPERATIONAL_SIGNALS` + `_OPPORTUNITY_SIGNALS` + `_RECOMMENDATION_SIGNALS` | one unified `_RULES` |
| Grouping logic | fixed list + `selectattr` in the template | `group_signals()` in Python |

## New architecture — the `Rule → Evidence → Signal → Renderer` pipeline
```
authoritative read (the Rule's governing read)
        │  producer loops rows, derives rule-specific priority/title/summary
        ▼
_evidence(**pairs)                         →  deterministic ("key=value", …) tuple
        ▼
_emit(key, category, source_record, …)     →  the single Signal + Explainability + id factory
        ▼
group_signals(signals)                     →  ordered display buckets (Python)
        ▼
components/intelligence.html::signals_panel →  renders only
```

### Shared builder — `_emit(...)`
The one place a rule result becomes a `Signal`. It owns: the deterministic id (from
`key` + `source_record`), the `Explainability` (evidence + deterministic
`confidence=1.0` + policy gate), source-record passthrough, serialization
(`Signal.to_dict`, unchanged), and recommendation-metadata passthrough. A producer
supplies only its **governing read, title, summary, rule-specific evidence**, priority,
gate, and route. `explain_source` (the detailed read path recorded in the
explainability) is kept separate from the short `source_service` shown on the signal —
preserving the pre-refactor distinction exactly (recommendations pass the same short
name for both, as before).

### Unified registry
One model (`RegisteredSignal`) and one `_RULES` sequence of `(metadata, producer)`.
Operational/opportunity rules simply omit the governance fields
(`governing_rule`/`rule_version`/`compliance_owner`/`approval_status`); recommendation
rules populate them. A single `register_operational_signals()` loop registers metadata
and attaches producers — no per-category branching.

### Rule pipeline & extension points
Adding a future rule now means: (1) write a `_producer(ctx)` that calls its
authoritative read and, per row, calls `_emit(...)` with `_evidence(...)`; (2) add one
`(metadata, producer)` entry to `_RULES`. No new template, no id/explainability/
serialization boilerplate, no grouping change. Recommendation rules additionally set
the governance fields and pass `recommendation=RecommendationMeta(...)` (via the
`_recommendation` convenience wrapper, which now delegates to `_emit`).

### Rendering
Business grouping moved to `group_signals()` (fixed bucket order, empty buckets
dropped), exposed to the shared renderer as the `signal_groups` Jinja global (wired on
the workspace and people template envs — both already import the module; no
`templating.py` change, no import cycle). The template only iterates and renders. **No
visual change** (proven byte-identical, normalized).

## Authorization — unchanged
Scope-first behavior, producer isolation, protected routes, household member
resolution, and book scoping are untouched. Producers still run only after
`get_client_signals` / `get_household_signals` / `get_dashboard_signals` resolve the
record scope onto `ctx.person_ids`; an inaccessible record still yields `()` and never
reaches a producer.

## Behavior verification / regression strategy
A golden snapshot (`tests/fixtures/d5e_golden.json`) was captured from the
**pre-refactor** code and the refactored code must reproduce it exactly (numbers
normalized so the golden is primary-key independent):
`tests/test_intelligence_refactor_regression.py` asserts identical **serialized
signals**, **rendered HTML**, and **registry contents**, plus deterministic ordering/
ids and preserved scope-first authorization. On top of that, the existing 57
Advisor-Intelligence tests (D.5A–D.5D) and the full suite (1339 passed) all pass
unchanged. Signal IDs, evidence, ordering, priority, policy gates, recommendations,
opportunities, operational signals, rendered HTML, and serialization are all identical
before → after.

## Performance
No new authoritative reads and no new scans were introduced; each producer performs the
same single read it did before. No caching and no persistence were added (explicitly
out of scope).

## Exclusions honored
No D.6 compliance, no new recommendations/opportunities/signals/rule types, no
workflow, notifications, persistence, AI/ML, suitability, or compliance/licensing
automation. No new routes, no new tables, no migrations. Route-count guards unchanged
(319).

## Future D.6 integration
The `_emit` builder and unified `_RULES` registry are the extension points a future
governed-disposition phase (D.6) plugs into: a rule's `RegisteredSignal` already
carries its governing rule/version/compliance owner/approval status, and every emitted
`Signal` carries evidence + explainability + policy gate — the metadata a disposition/
evidence-ledger layer needs, without touching producer internals.

## Remaining technical debt
- Tax-planning and retirement recommendations/opportunities remain deferred (no
  authoritative cadence rule) — unchanged from D.5C/D.5D.
- Per-rule priority derivation (e.g. severity map, materiality thresholds) stays inside
  each producer by design (rule-specific, not shared) — a future phase could formalize
  a priority policy if more rules share patterns.
- Governed signal/recommendation disposition (acknowledgement/decision evidence ledger)
  remains out of scope, to be built on the extension points above.
