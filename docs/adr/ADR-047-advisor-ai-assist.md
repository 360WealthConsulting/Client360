# ADR-047 — Advisor AI Assist: Grounded, Read-Only Briefing Intelligence with Human Review

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Advisor Experience); Reliability / Operations; Security /
Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton). Authorized
compliance reviewer: Not yet designated.

## Context
D.38–D.41 produced deterministic, scope-guarded, AI-ready summaries and snapshots (Advisor Workspace
daily brief, Unified Work Queue summary, Client 360 snapshot, Household 360 snapshot, meeting brief).
Advisors wanted help understanding the day, preparing for meetings, summarizing a client/household,
identifying open work, explaining why an item matters, and navigating to the authoritative workflow —
without the platform becoming a second decision engine. The mandatory audit found **no LLM/model-provider
infrastructure of any kind** (no provider client, no prompt template, no AI config, no LLM dependency),
and that every "AI"/"intelligence" module is deterministic rule composition. It also confirmed the
governed hooks already exist: `runtime.consumption.feature_enabled` (the governed gate, no env fallback),
the policy engine (read-only explanation), the RBAC caps (`client.read` gates `/workspace`;
`observability.audit` is admin/compliance only, not advisor), the in-process-counter + analytics metric
pattern, and that reads must never touch the transactional outbox.

## Decision
Phase D.42 adds a governed, **read-only** Advisor AI Assist surface (`app/services/ai_assist/`) that
**consumes** the D.38–D.41 summaries and never re-queries domains or reads `rm_*` tables.

- **Why AI Assist is read-only.** It may summarize, explain, compare, and NAVIGATE only. It never
  creates, updates, deletes, approves, assigns, files, submits, sends, or completes a business record —
  every proposed action is a **deep link** into an authoritative workflow. The assistant never mutates,
  never writes to any database (not even audit), and never publishes to the outbox.
- **Authoritative input sources.** The context service reuses the scope-guarded compact summaries
  (`workspace.summaries.daily_brief`, `client360.get_workspace(...).snapshot`,
  `household.get_household_workspace(...).snapshot`, `work_queue.summary`, `summaries.meeting_prep`) —
  never the un-scoped raw `get_meeting_brief`/`get_client_snapshot`. Unauthorized/suppressed sources are
  omitted before assembly; sensitive fields (note bodies, contact PII, account numbers) are excluded.
- **Structured grounding + internal citations.** Every fact is a `GroundedFact` classed as confirmed
  platform fact / derived arithmetic / model summary / missing-or-untracked / prohibited-by-scope — an
  unsupported inference is never presented as fact. Every response carries internal citations to its
  source surfaces (with deep links) and lists limitations; missing data is stated as "Not tracked",
  "Unavailable", or "Insufficient data".
- **Human review.** Every response is labelled **"Advisor Assist — Review Required"**. There is no
  one-click autonomous execution, no "approve all", no "apply suggestions" — the advisor must
  intentionally navigate into the authoritative workflow.
- **Prohibited autonomous / regulated behavior.** Requests for trade recommendations, tax filing
  conclusions, legal advice, compliance/suitability approval, autonomous actions, unsupported
  predictions, or unreviewed client communications are **refused** with a constrained message and a
  suggested authoritative link. Required safety/provenance output fields can never be omitted
  (`validate_output` rejects an envelope lacking citations/limitations/human-review).
- **Provider abstraction.** No provider existed, so a minimal `AssistProvider` protocol + a
  **deterministic, offline `LocalProvider`** (default) compose the structured brief from the grounded
  context — no network, no credentials, so the suite runs offline. A real model provider can be slotted
  in behind the same contract, configured via Runtime/Configuration (never hard-coded secrets). The
  provider is gated by `feature_enabled("advisor.ai_assist", default=True)`; on disable, timeout,
  failure, or malformed output it **fails closed** to deterministic source facts + a "generation
  unavailable" label (never fabricated), never breaking the D.38–D.41 surfaces.
- **Security + minimization.** Each request enforces authentication + capability + record scope; sources
  the principal cannot access are omitted before assembly. Only the fields needed for the selected
  capability are included; the compact snapshots (counts/refs) are preferred. Diagnostics require
  `observability.audit` and expose no prompt contents/secrets/client payloads.
- **Diagnostics + governance.** In-process counters back low-cardinality analytics metrics + read-only
  diagnostics; governance statically verifies no mutation/outbox/audit-write/`rm_`/secret, prompt
  constraints, contract completeness, summary reuse, the governed gate, and refusal coverage.

## Alternatives considered
1. **Wire a real LLM provider now.** Rejected: none exists, and CI must run offline; the deterministic
   local provider is the safe default, with the abstraction ready for a real provider later.
2. **Let the assistant take actions / apply suggestions.** Rejected: violates the read-only invariant;
   every action is a deep link into the authoritative workflow.
3. **A new `ai.assist` capability + migration.** Rejected as unnecessary: the deterministic local
   provider carries no separate risk/cost that existing data caps can't express; the surface reuses
   `client.read`/`work.read`/`observability.audit` — no new capability, no migration.
4. **Re-query domains for facts.** Rejected: the assistant consumes the D.38–D.41 summaries; duplicating
   domain reads would risk scope leaks and drift.
5. **Persist prompts/responses (a response-history table).** Rejected: no full prompts or generated
   responses are persisted; only in-process aggregate counters (no DB write).

## Reasons for the decision
Advisors need briefing help without weakening any invariant: no second policy/workflow/recommendation
engine, no mutation, no outbox writes, no scope bypass, no fabricated facts, no autonomous or regulated
determinations. Grounding on the scope-guarded summaries, refusing regulated requests, requiring
citations + limitations + human-review, and defaulting to a deterministic offline provider that fails
closed achieves that and preserves ADR-004/013/028/030/041–046.

## Consequences
### Positive consequences
- A governed read-only assistant: Daily Advisor Brief, Client Brief, Household Brief, Meeting Prep, Work
  Explanation, and bounded Factual Question Answering — each grounded, cited, limitation-bearing,
  human-review-labelled, and deep-linking into the authoritative workflow. Regulated requests refused.
  Offline + deterministic (CI-safe). Diagnostics + governance + low-cardinality analytics. Failure never
  breaks D.38–D.41.

### Negative consequences and tradeoffs
- The default provider is deterministic composition, not generative narrative — briefs are structured
  fact roll-ups, not free prose (safe + testable; a real model can be added later). Answers are bounded
  to facts present in the assembled context (unsupported questions are marked). Meeting prep is minimized
  (note bodies/contact PII/account numbers excluded), so it summarizes counts + safe context, not raw
  notes.

## Enforcement
- `app/services/ai_assist/` (contracts, grounding via context, prompts, registry, provider, context,
  refusal, assistant, diagnostics, governance, common); routes in `app/routes/ai_assist.py`
  (`/workspace/assist`, `POST /workspace/assist/query`, `/workspace/assist/diagnostics`,
  `/client/{id}/brief`, `/client/household/{id}/brief`, `/workspace/meetings/{id}/brief`,
  `/work/{type}/{id}/explain`); templates `app/templates/ai_assist/*`, `app/static/css/ai_assist.css`;
  analytics metrics in `sources.py`/`metrics.py`. **No migration, no new table, no new capability, no
  new projection** — migration head unchanged; reuses `client.read`/`work.read`/`observability.audit`
  and the runtime feature gate. The authoritative services, outbox, projection model, runtime/policy
  engines, and RBAC are untouched. Tests: `tests/test_advisor_ai_assist.py`; platform-architecture /
  route-count / ADR-count guards updated.

## Exceptions
Briefs reuse `client.read` + the underlying summaries' scope; work explanation reuses `work.read`;
diagnostics reuse `observability.audit`. The POST query endpoint is read-only (POST carries the body;
creates no record). The provider gate is `feature_enabled("advisor.ai_assist", default=True)`;
`administrator`/`record.read_all` scope bypass is unchanged (ADR-004). Regulated requests are refused.

## Revisit conditions
Wiring a real model provider (configured via Runtime/Configuration, with secrets management), adding a
persistent registry/prompt-version store (would justify a migration), adding an `ai.assist` capability
(if provider invocation must be authorized separately from data), or expanding beyond the registered
capabilities would each warrant a new or superseding ADR.

## References
- `app/services/ai_assist/*`, `app/routes/ai_assist.py`, `app/templates/ai_assist/*`,
  `app/static/css/ai_assist.css`, `app/services/analytics/{sources,metrics}.py`
- `docs/ADVISOR_AI_ASSIST.md`, `docs/AI_ASSIST_CONTEXT_CONTRACT.md`,
  `docs/AI_ASSIST_PROMPT_GOVERNANCE.md`, `docs/AI_ASSIST_SECURITY.md`, `docs/AI_ASSIST_PROVIDER_GUIDE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_advisor_ai_assist.py`; relates to ADR-004, ADR-013, ADR-028, ADR-030, ADR-038 through ADR-046
