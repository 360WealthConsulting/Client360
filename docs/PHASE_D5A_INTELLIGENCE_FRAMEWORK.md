# Phase D.5A — Advisor Intelligence Framework

Fifth production slice of `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md` (§4, §7), building
on D.1–D.4. Implemented on `release/0.13.0`. This phase ships the **framework that
will host Advisor Intelligence — and only the framework.** It generates **no
signals, no recommendations, no advice, no AI, no compliance logic.**

## What was implemented
A new orchestration service `app/services/advisor_intelligence.py` containing
deterministic signal **infrastructure** and a thin, record-scoped composition layer:

| Component | What it is |
|---|---|
| **Priority model** | `Priority` enum — `CRITICAL / HIGH / MEDIUM / LOW / INFORMATIONAL` with a fixed, deterministic ordering (`.rank`, `sort_key`). **No scoring algorithm.** |
| **Policy gates** | `PolicyGate` enum — `NONE / COMPLIANCE_REQUIRED / LICENSE_REQUIRED / SUITABILITY_REQUIRED`. **Display metadata only; enforces nothing.** |
| **Explainability model** | `Explainability(why, source_service, evidence, confidence, policy_gate)`. `confidence` is a **deterministic** placeholder (`0.0`) — never a probabilistic/AI score. Placeholders populated only. |
| **Signal model** | `Signal` (+ `SourceRecord`) with every required field — `id, category, severity, priority, title, summary, source_service, source_record, evidence, created_at, explainability, policy_gate, route, status` — and a JSON-safe `to_dict()`. `created_at` is **caller-supplied** (ISO string), never generated, so the model stays deterministic. Informational only. |
| **Registry** | `register_signal(...)` / `list_registered_signals()` record a rule's **metadata** (`RegisteredSignal`); duplicate keys raise. **The registry holds no executable body and the composition layer never runs a rule in this phase.** |
| **Composition layer** | `get_client_signals(principal, person_id)`, `get_household_signals(principal, household_id)`, `get_dashboard_signals(principal)`. Each resolves/enforces record scope, then returns **`()`** (empty) via an empty producer seam. |
| **UI** | Advisor Intelligence panel on the existing Advisor Workspace dashboard, showing the placeholder empty state: *"No advisor signals — Advisor Intelligence coming in Phase D.5B."* No scores, no recommendations, no generated content. |

## Architecture & orchestration model
Advisor Intelligence is an **orchestration layer** (`ADVISOR_WORKSPACE_ARCHITECTURE.md`
§7), exactly like `advisor_workspace.py`. It composes existing authoritative services
and **must never become** a portfolio, tax, insurance, benefits, workflow, or
compliance engine. In this phase it composes only the security layer
(`accessible_person_ids`, `record_in_scope`) — no domain engine is touched and no
business rule is embedded in the route layer.

### The producer seam (D.5B extension point)
Governed deterministic rules will attach in D.5B as
`(SignalContext) -> Iterable[Signal]` callables on an internal producer seam
(`_PRODUCERS`, empty in this phase). The single dispatch point (`_collect`) runs
producers **only after** the caller's record scope is resolved, so a future rule can
never read a record outside the advisor's book. Because the seam is empty today,
every accessor deterministically returns `()`.

## Registry
Rules register **metadata** (key, category, source service, default priority, policy
gate, description) so the framework knows a rule *exists*; it does not execute it.
`list_registered_signals()` returns the set ordered by key (deterministic).
`register_signal` rejects duplicate keys so two rules can never silently share an id.
`clear_registry()` exists for test isolation (the framework has no persistence).

## Explainability
Every future signal must carry an `Explainability` recording **why it exists**,
**which service produced it**, the **evidence used**, a **deterministic confidence**,
and its **policy gate**. The model is created now and populated with placeholders
only — there is no reasoning/scoring logic in this phase.

## Priority model
Five fixed levels with a deterministic ordering used to sort signals most-urgent-first.
There is **no scoring algorithm** — a future rule assigns a level; the framework never
computes one.

## Policy gates
Four placeholder gates for display. The framework **enforces no regulatory logic** —
turning any gate into enforcement is a governed, compliance-owned decision
(`V1_RISK_REGISTER.md` GOV-2, `PRODUCT_DECISIONS.md` PD-4), not this framework's job.

## Authorization safeguards
- `get_client_signals` / `get_household_signals` enforce record scope **first**
  (`record_in_scope(principal, "person"/"household", id)`); an inaccessible record
  yields `()` and **never reaches a producer** — it cannot expose another client's
  data even when rules exist. Proven by tests that probe the producer seam: the probe
  is never called for an out-of-scope record and receives only the in-scope id.
- `get_dashboard_signals` resolves the advisor's **book scope**
  (`accessible_person_ids`) and hands producers only that scope — a scoped advisor
  never gets a firm-wide set.
- **Even empty responses respect scope** — scope is resolved before returning `()`.

## Exclusions (unchanged from the governing architecture)
No Advisor Intelligence itself; no recommendations; no advice; no Roth conversions,
tax opportunities, insurance gaps, estate planning, retirement readiness,
cross-selling, investment advice, or suitability; no compliance automation; no
workflow creation; no notifications; no AI / LLMs / machine learning / vector search
/ embeddings; no historical or predictive analytics; no new tables, migrations,
scheduler, or engines; no writes.

## Tests
`tests/test_advisor_intelligence.py` (13): empty registry; signal registration +
ordering; duplicate-key guard; registration does not execute rules; signal
serialization (JSON-safe); explainability placeholder defaults; deterministic
priority ordering; policy-gate model; client-signal record-scope (probe never
reached for an inaccessible person); household-signal record-scope; dashboard book
scope; framework emits no recommendation/AI content; dashboard panel shows the
empty placeholder. No new route → route-count guards unchanged (319).

## Remaining work (Phase D.5B and beyond — not in this phase)
- Governed, deterministic rule bodies attached to the producer seam (each
  propose-only, evidence-backed, `[Policy-gated]` where regulated).
- Per-signal evidence/decision ledger for regulated proposals (mirror the
  workflow-evidence append-only pattern).
- Enabling any `[Policy-gated]` signal requires firm-supplied thresholds **and** an
  accountable compliance owner (GOV-2 / PD-4) — mechanism only until then.
- Rendering populated signals in the Client 360 / Meeting workspaces.
