# Advisor AI Assist (Phase D.42)

> **D.44:** AI Assist now grounds on the unified engagement summary (recent-interaction / unread /
> action-required **counts only**, never message bodies), sourced from the composed Client 360 / Household
> 360 Communications sections — preserving the "reuse summaries, no raw domain fan-out" invariant. See
> [`COMMUNICATION_ARCHITECTURE.md`](COMMUNICATION_ARCHITECTURE.md) and ADR-049.
>
> **D.45:** AI Assist also grounds on the knowledge graph — the **connected-entity count only**, sourced from
> the composed Client 360 / Household 360 Knowledge section (never an unrestricted graph query; every
> explanation cites its authoritative service). See [`KNOWLEDGE_GRAPH.md`](KNOWLEDGE_GRAPH.md) and ADR-050.
>
> **D.46:** AI Assist also **summarizes** the operational-intelligence recommendations — the recommendation
> **count** + top title, sourced from the composed Client 360 / Household 360 Recommendations section. It
> only ever summarizes the recommendation contracts this layer emits; it never invents a recommendation. See
> [`OPERATIONAL_INTELLIGENCE.md`](OPERATIONAL_INTELLIGENCE.md) and ADR-051.
>
> **D.47:** For a compliance **supervisor** (a `compliance.supervise` holder), AI Assist also summarizes
> supervisory counts (open reviews / open exceptions) sourced from the composed Compliance Oversight section
> — counts only. It never approves, waives, suppresses, or invents a compliance finding, and it emits no
> supervisory facts for a non-supervisor. See [`COMPLIANCE_INTELLIGENCE.md`](COMPLIANCE_INTELLIGENCE.md) and ADR-052.

Advisor AI Assist is a governed, **read-only** briefing surface that consumes the deterministic,
scope-guarded summaries and snapshots already produced by D.38–D.41 (Advisor Workspace, Unified Work
Queue, Client 360, Household 360, meeting brief). It helps an advisor understand the day, prepare for a
meeting, summarize a client or household, identify open work, explain why an item matters, answer bounded
factual questions, and **navigate** to the authoritative workflow.

See also: [`ADR-047`](adr/ADR-047-advisor-ai-assist.md), [`AI_ASSIST_CONTEXT_CONTRACT.md`](AI_ASSIST_CONTEXT_CONTRACT.md),
[`AI_ASSIST_PROMPT_GOVERNANCE.md`](AI_ASSIST_PROMPT_GOVERNANCE.md), [`AI_ASSIST_SECURITY.md`](AI_ASSIST_SECURITY.md),
[`AI_ASSIST_PROVIDER_GUIDE.md`](AI_ASSIST_PROVIDER_GUIDE.md).

## Read-only architecture (the core invariant)

AI Assist may **summarize, explain, compare, and navigate only**. It is **not** a business-rules, policy,
workflow, recommendation, or mutation engine and is never a source of truth. It never creates, updates,
deletes, approves, assigns, files, submits, sends, or completes a business record — **every proposed
action is a deep link into an authoritative workflow**. It never mutates, **never writes to any database
(not even audit)**, and **never publishes to the outbox**.

Preserved invariants: the Runtime Engine is the sole evaluator; the Runtime Policy Engine is the sole
decision engine; Workflow Orchestration is the sole process coordinator; the authoritative domain
services are the sole mutation layer; the transactional outbox is the sole event bus; the Advisor
Workspace / Unified Work Queue / Client 360 / Household 360 remain the home, work, person, and household
surfaces.

## Capabilities (registry)

A closed registry — no free-form modes exist outside it. Each has a versioned prompt, input/output
contracts, a required data capability, source adapters, and a model config.

| Capability | Consumes | Data capability |
|---|---|---|
| Daily Advisor Brief | Advisor Workspace daily brief + Unified Work Queue summary | client.read |
| Client Brief | Client 360 snapshot | client.read |
| Household Brief | Household 360 snapshot | client.read |
| Meeting Prep Brief | minimized meeting brief + Client 360 snapshot | client.read |
| Work Explanation | Unified Work Queue item | work.read |
| Factual Question Answering | daily + client/household context | client.read |

## Grounding + citations

Every fact is a **GroundedFact** classed as `confirmed_platform_fact`, `derived_arithmetic`,
`model_generated_summary`, `missing_or_untracked`, or `recommendation_prohibited_by_scope`. An
unsupported inference is never presented as fact. Every response carries **internal citations** to its
source surfaces (Client 360, Household 360, Unified Work Queue, Advisor Workspace, Meeting Brief, …) with
deep links, and lists **limitations**. Missing data is stated as **Not tracked / Unavailable /
Insufficient data**. Required safety/provenance fields (citations, limitations, human_review) can never
be omitted.

## Human review

Every response is labelled **"Advisor Assist — Review Required"**. There is no one-click autonomous
execution, no "approve all", no "apply suggestions". The advisor must intentionally navigate into the
authoritative workflow to act.

## Prohibited / refused

Requests for trade recommendations, tax filing conclusions, legal advice, compliance/suitability
approval, autonomous actions, unsupported predictions, or unreviewed client communications are **refused**
with a constrained message and a suggested authoritative link. See [`AI_ASSIST_SECURITY.md`](AI_ASSIST_SECURITY.md).

## Provider + failure behavior

No LLM infrastructure exists, so the default provider is a **deterministic, offline `LocalProvider`**
that composes the structured brief from grounded facts — no network, no credentials (CI-safe). Generation
is gated by `runtime.consumption.feature_enabled("advisor.ai_assist", default=True)`. On disable, timeout,
provider failure, or malformed output it **fails closed** to deterministic source facts + a "generation
unavailable" label — never fabricated, and it never breaks the D.38–D.41 surfaces. See
[`AI_ASSIST_PROVIDER_GUIDE.md`](AI_ASSIST_PROVIDER_GUIDE.md).

## Routes (all read-only)

- `GET /workspace/assist` — advisor daily assist page (does not replace any widget or home).
- `POST /workspace/assist/query` — bounded factual Q&A (POST carries the body; creates no record).
- `GET /workspace/assist/diagnostics` — diagnostics + governance (`observability.audit`).
- `GET /client/{person_id}/brief` — client brief (404 out of scope).
- `GET /client/household/{household_id}/brief` — household brief (404 out of scope).
- `GET /workspace/meetings/{person_id}/brief` — meeting prep brief (404 out of scope).
- `GET /work/{item_type}/{item_id}/explain` — work explanation.

## Diagnostics / governance / analytics

Diagnostics report provider availability, model, prompt versions, registered capabilities, source
adapters, request/refusal/latency/citation counters — no prompt contents, secrets, or client payloads.
Governance verifies no mutation/outbox/audit-write/`rm_`/secret, prompt constraints, contract
completeness, summary reuse, the governed gate, and refusal coverage. Analytics exposes low-cardinality
operational metrics only (requests, refusals, success rate, latency, citation coverage, provider
failures).

## Capabilities / migration

**No migration, no new table, no new projection, no new capability.** Reuses `client.read` /
`work.read` / `observability.audit` + the runtime feature gate. Migration head unchanged.
