# AI Assist Security & Minimization (Phase D.42)

See [`ADVISOR_AI_ASSIST.md`](ADVISOR_AI_ASSIST.md), [`AI_ASSIST_CONTEXT_CONTRACT.md`](AI_ASSIST_CONTEXT_CONTRACT.md),
[`ADR-047`](adr/ADR-047-advisor-ai-assist.md).

## Access control

Every AI Assist request enforces:

- **authentication** (via the standard `require_capability` dependency);
- **required capability** — briefs reuse `client.read`, work explanation reuses `work.read`, diagnostics
  reuse `observability.audit`;
- **record scope** — person/household briefs check `record_in_scope` and **404 out of scope**; the
  underlying summaries also return `None`/suppress out of scope;
- **section/source capability** — a source the principal cannot access is **omitted before context
  assembly** (no hidden or suppressed section data reaches the provider);
- **provider gate** — generation is gated by `runtime.consumption.feature_enabled("advisor.ai_assist",
  default=True)` (governed; no raw env fallback).

No new capability was added: the deterministic local provider carries no separate risk/cost that existing
data caps cannot express. If a real model provider is wired later and provider invocation must be
authorized separately from data access, a single non-sensitive `ai.assist` capability may be added
(additive — it must not grant data access).

## Data minimization

Only the fields required for the selected capability are sent to the provider; the compact snapshots
(counts/refs) are preferred over full section payloads. **Never sent:**

- raw credentials, secrets, API keys, tokens, passwords;
- SSNs, account numbers, bank routing data;
- full document contents; full audit logs;
- **protected note bodies** (`notes[].body`) — meeting prep sends the note **count** only;
- **contact PII** (`primary_email`, `primary_phone`) — client **name** only;
- unnecessary raw identifiers in user-facing text (deep links carry ids).

## Logging (no sensitive content)

AI Assist does **not** log complete prompts or complete sensitive responses. It records only safe
**in-process operational counters** (no DB write): request counts, refusal counts + categories, timeout /
provider-failure / malformed counts, unsupported-question count, citation count, and latency. These back
the low-cardinality analytics metrics and diagnostics. AI reads are **never** placed in the transactional
outbox and never write a domain event.

## Failure isolation (fail closed)

If generation is disabled or the provider is unavailable/times out/fails/returns malformed output, the
assistant **fails closed** to deterministic source facts + a "generation unavailable" label — it never
fabricates output, preserves navigation deep links, and never breaks the Advisor Workspace, Unified Work
Queue, Client 360, Household 360, meeting records, or authoritative workflows.

## Diagnostics

`GET /workspace/assist/diagnostics` (`observability.audit`) exposes provider availability, model, prompt
versions, registered capabilities, source adapters, and aggregate counters — **never** prompt contents,
secrets, or client-sensitive payloads.
