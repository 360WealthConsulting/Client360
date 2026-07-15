# Release 0.9.10 — Exception Engine (platform-wide; tax domain first)

**Status:** **Released** — validated by [RC13](RC13_VALIDATION.md) (**SAFE TO MERGE**, 0
defects); PR #21 merged to `main` (merge commit `43921e4`); tagged **`v0.9.10`**.
**Alembic head:** `q7b58f6c5d4e` (baseline v0.9.9 `o5f36c4d3e2a`).
**Design:** [ADR-17](ADR_EXCEPTION_ENGINE_SCOPE.md) · [Sprint 5.5 design](SPRINT_5_5_EXCEPTION_DESIGN.md).

Release 0.9.10 delivers the **Exception Engine** — a unified, platform-wide way to
detect, own, escalate, resolve, and report the problems that block work. Per ADR-17 the
schema is **domain-neutral** (`exceptions` / `exception_events` / `exception_types` with a
required, CHECK-constrained `domain`), and this release **implements `domain='tax'` only**;
the other domains are schema-ready but not yet detected.

## What's new

- **Canonical Exception Engine** (`app/services/exception_engine.py`) — one state machine
  (open → acknowledged → in_progress → waiting → escalated/reopened → resolved/cancelled),
  idempotent dedupe, stale-action rejection, an **immutable append-only event ledger**,
  and audit + timeline on every mutation. Record-scope authorization on every read/write.
- **Tax detectors** (`tax_exception_detectors.py`) — 15 detectors translate existing tax
  source-of-truth conditions (missing docs/organizer/questionnaire/signatures, client
  non-response, overdue/blocked work, missing preparer/reviewer, filing rejection/
  transmission, acceptance pending, compliance sign-off/retention, document ambiguity)
  into exceptions with stable dedupe keys; conditions that clear auto-resolve and recur by
  reopening.
- **Deterministic SLA sweep** (`exception_sla.py`) — severity-based escalation cadence,
  replay-safe (cadence-gated, not wall-clock), with **honest notification outcomes**
  (email/SMS remain stubbed → recorded `disabled`, never fabricated).
- **Work Management integration** — exceptions project through the single `work_items()`
  point into My/Team Work, queues (`tax_exceptions`, `tax_exceptions_critical`,
  `compliance_exceptions`), agenda, capacity, and bottlenecks. **No second assignment
  model** — reuses `record_assignments`.
- **Versioned API + staff console** — `/api/v1/exceptions/*` and `/exceptions` (list,
  detail, event timeline, capability-gated actions) as thin routes over the canonical
  services; out-of-scope → 404; blocker/compliance resolution segregation.
- **Client portal "Action Needed"** — `/portal/action-needed` and
  `/api/v1/portal/exceptions[/{id}]` expose a **strict client-visible allowlist**
  (`CLIENT_VISIBLE_CODES`) as plain-language, scoped, portal-safe items. Read-only in the
  portal — resolution stays with the real underlying action / detector reconciliation.
  No internal fields, codes, event history, or audit leak.
- **Dashboards & reporting** (`exception_reporting.py`) — authorization-filtered metrics
  (open/blocker/high/at-risk/breached/unassigned/compliance, by category/owner/team/
  client/return, aging buckets, escalation distribution, MTTA, MTTR, reopen rate, SLA
  compliance, and a real opened/resolved trend). Role-appropriate audiences
  (advisor/operations/tax/compliance/management) at `/exceptions/reporting` and
  `/api/v1/exceptions/report`, plus a compact summary strip embedded on the advisor, tax,
  and operations dashboards. **Aggregation is always scope-filtered; nothing is
  fabricated** — only stored fields are aggregated.

## Capabilities & roles

New least-privilege family: `exception.read`, `exception.write`,
`exception.resolve` (sensitive — blocker), `exception.compliance` (sensitive — compliance
category). **No role widened; no new `record.read_all` grant.**

## Migrations

- `p6a47e5d4f3b` — exception engine schema (tables, CHECK constraints, hot-path indexes,
  append-only trigger, tax reference seed, capability family, work queues). Additive/
  reversible.
- `q7b58f6c5d4e` — data-only queue criteria (renames to `tax_exceptions*`; sets queue
  criteria). Reversible.

Single head `q7b58f6c5d4e`. Sentinel preservation verified across a v0.9.9 down/up cycle
(see [RC13 §3](RC13_VALIDATION.md)).

## Validation

Full suite **421 passed / 5 skipped**; migration lifecycle, schema constraints, event
immutability, dedupe, authorization, portal isolation, work projection, and dashboard
aggregation all verified. See [RC13](RC13_VALIDATION.md) — **SAFE TO MERGE**.

## Known limitations

- Tax domain only; other domains are schema-ready but not detected this release.
- Notifications: in-app delivered; email/SMS stubbed (`disabled`) until a real provider.
- Console "Return {id}" links to the tax-returns board (no per-return detail route yet).
- Reporting trend/throughput reflect only data the system stores; no synthetic history.
