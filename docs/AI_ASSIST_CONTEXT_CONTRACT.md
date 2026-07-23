# AI Assist Context Contract (Phase D.42)

How the read-only context service (`app/services/ai_assist/context.py`) assembles the facts handed to the
provider. See [`ADVISOR_AI_ASSIST.md`](ADVISOR_AI_ASSIST.md), [`ADR-047`](adr/ADR-047-advisor-ai-assist.md).

## Sources (reuse, never re-query)

The context service consumes ONLY the scope-guarded D.38–D.41 summaries — it never re-queries domains and
never reads `rm_*` tables:

| Capability | Source functions |
|---|---|
| daily_brief | `workspace.summaries.daily_brief` + `work_queue.summary.work_queue_summary` |
| client_brief | `client360.get_workspace(person_id=…).snapshot` |
| household_brief | `client360.household.get_household_workspace(household_id).snapshot` |
| meeting_prep | `workspace.summaries.meeting_prep` (minimized) + client snapshot |
| work_explanation | `work_queue.compose_queue` (item by domain + id) |
| factual_question_answering | daily + optional client/household context |

**Never call raw `get_meeting_brief` / `get_client_snapshot`** — they do not self-enforce record scope.
Always the scope-guarded wrappers/orchestrators (which return `None`/suppress out of scope).

## GroundedFact

```
GroundedFact(source_type, source_label, fact_key, fact_value,
             fact_class, source_id, freshness, deep_link, security_context, available)
```

`fact_class` ∈ `confirmed_platform_fact` | `derived_arithmetic` | `model_generated_summary` |
`missing_or_untracked` | `recommendation_prohibited_by_scope`. Missing facts use `unavailable(...)` with a
`Not tracked` / `Unavailable` reason and `available=False` — a value is **never fabricated**.

## Minimization (what is sent)

Only the fields needed for the selected capability are included; the **compact snapshots (counts/refs)**
are preferred. The following are **never** included in context:

- **note bodies** (`notes[].body`) — meeting prep sends the note **count** only;
- **contact PII** — `person.primary_email` / `primary_phone` (name only);
- **account numbers**, SSNs, credentials, secrets, full document contents, protected records;
- unnecessary raw identifiers (deep links carry ids; user-facing text does not).

Every field type included in a provider request is documented here and in
[`AI_ASSIST_SECURITY.md`](AI_ASSIST_SECURITY.md).

## Suppression + authorization

- Unauthorized sources are **omitted before assembly** (a section suppressed on the Client 360 snapshot —
  e.g. `revenue=None` without `opportunity.view` — becomes an `Unavailable` fact, never raw data).
- Out-of-scope person/household → the orchestrator returns `None` → the context bundle is empty +
  `unavailable`, and the route 404s.
- The bundle carries `sources_used`, `suppressed_sources`, `unavailable`, and a `context_size`.

## Output envelope

The assistant wraps the provider result in a validated envelope with required fields (`kind`,
`human_review`, `citations`, `limitations`, `generated_at`) plus `provider`, `sections`, `facts`,
`navigation`, `unavailable`. `validate_output` rejects an envelope missing any required
safety/provenance field.
