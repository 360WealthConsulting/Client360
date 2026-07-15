# ADR — Exception Engine Scope: Tax-specific vs. Platform-wide

**Status:** Proposed (pre-implementation review for Sprint 5.5). **No code written.**
**Context doc:** `docs/SPRINT_5_5_EXCEPTION_DESIGN.md`. **Baseline:** `main` @ head `o5f36c4d3e2a`.
**Decision owner:** pending approval.

## Context

Sprint 5.5 designs a Tax Exception Management subsystem with canonical tables
`tax_exceptions`, `tax_exception_events`, `tax_exception_types`. The review asks whether
these should instead be **domain-neutral** — `exceptions`, `exception_events`,
`exception_types` with a required `domain` field (`tax`, `wealth`, `operations`,
`compliance`, `portal`, `microsoft`) — with tax as the first implementation.

**Key fact:** exception-like handling **already exists across six domains today**, in
siloed tables with inconsistent severity/owner/SLA/resolution models:

| Domain | Existing exception-like signal |
|---|---|
| workflow | `workflow_escalations` (SLA breaches) |
| document / identity | `match_queue` (ambiguous matches) |
| microsoft | `microsoft_unmatched_messages`, `microsoft_unmatched_calendar_attendees` |
| compliance | `work_approvals` (segregation-of-duty) |
| tax | `tax_missing_items`, `tax_review_corrections` |
| portal / wealth | portal inactivity, portfolio import errors (ad hoc) |

The roadmap adds more exception-prone domains: additional custodians, QuickBooks/revenue
intelligence, IRS transcript integration (Epic 6+). `exceptions` / `exception_events` /
`exception_types` are **not** existing table names (no collision).

## Decision drivers

- The cost difference between A and B is almost entirely a **design-time naming choice**,
  because nothing is built yet — there is no rename/backfill to pay for now.
- Migrating A → B **later** is expensive (table renames, `domain` backfill, capability and
  route deprecation, dual-write window, doc churn).
- Multiple domains already need this; a tax-only engine would spawn parallel per-domain
  engines or an awkward retrofit.

---

## Option A — Tax-specific exception engine

`tax_exceptions` / `tax_exception_events` / `tax_exception_types`; `tax.exception.*`
capabilities; routes under `/tax/exceptions`.

**Pros**
- Smallest conceptual surface; every field assumes a tax return scope.
- Authorization is uniformly record-scoped (return → person/household), matching the
  existing tax hardening exactly.
- Slightly less upfront design (no domain discriminator, no per-domain scoping model).

**Cons**
- Ignores that five other domains already have exception needs → invites parallel engines
  (`workflow_exceptions`, `ops_exceptions`, …) or copy-paste.
- Generalizing later is a **breaking migration**: rename 3 tables, add + backfill `domain`,
  rename capabilities and `/tax/exceptions` routes, dual-write during cutover, and update
  every reference/doc. That is exactly the kind of churn Release 0.9.9 worked to remove.

## Option B — Platform-wide engine, tax as first implementation

`exceptions` / `exception_events` / `exception_types` with a required `domain`
(`tax` | `wealth` | `operations` | `compliance` | `portal` | `microsoft`, CHECK-constrained,
extensible). Types carry `domain`; **only tax types are seeded and only tax detectors are
built this sprint.**

**Pros**
- New domains later = new `exception_types` rows + detectors — **zero schema/API change**.
- One console, one metric set, one audit ledger, one SLA sweep across the whole platform;
  consolidates the six siloed signals over time.
- Chooses the durable name now while it is free; avoids a future breaking migration.

**Cons / added complexity (bounded)**
- A `domain` discriminator + one extra index (`domain, status`).
- **Scope model must be domain-aware:** client-bound domains (`tax`, `wealth`, `portal`)
  are record-scoped to person/household; system domains (`operations`, `microsoft`, and
  some `compliance`) are **firm/system-scoped** (no client). The record-scope check must
  branch on domain — the one genuinely new design concern.
- Capability family generalizes to `exception.*` (read/write/resolve) with domain-scoped
  grants, rather than `tax.exception.*`. Naming: keep DB table `exceptions` but name the
  service module e.g. `app/services/exception_engine.py` to avoid clashing with Python
  "exceptions".
- Mild risk of over-generalized fields; mitigated by seeding/building **tax only** now.

---

## Comparison

| Dimension | Option A (tax-specific) | Option B (platform-wide, tax first) |
|---|---|---|
| **Migration impact (now)** | 3 tables + trigger + seed + caps | **Same** 3 tables + trigger + seed + caps, **+1 `domain` column and index**. Effectively equal. |
| **Migration impact (future)** | **High** — breaking rename + `domain` backfill + capability/route deprecation + dual-write to reach B | **None** — new domain = new seed rows only |
| **Complexity** | Lowest today | Slightly higher: `domain` discriminator + **domain-aware scoping** (record- vs firm-scoped) + generalized capabilities |
| **Extensibility** | Poor — each new domain needs new tables or a retrofit | **High** — additive rows/detectors, no schema/API change |
| **Future Epic support** | Weak — Epic 6 custodians/QuickBooks/transcripts, portal, microsoft, wealth would each re-solve it | **Strong** — one engine already fits the six existing signals and the roadmap domains |
| **Consolidation of today's silos** | No | Yes (incrementally: workflow/match/microsoft/compliance can fold in later) |
| **Risk** | Predictable now, costly later | One new concept (domain scoping) to get right upfront |

---

## Recommendation

**Adopt Option B — a platform-wide exception engine (`exceptions` / `exception_events` /
`exception_types` with a required, CHECK-constrained `domain`), with tax as the first and
only implementation this sprint.**

Rationale: the incremental cost of B *now* is one column, one index, and a domain-aware
scope check; the cost of *not* choosing it is a future breaking migration plus likely
per-domain engine proliferation. Six domains already exhibit the need and the roadmap adds
more, so the generality is demand-backed, not speculative.

### Guardrails (so B does not become speculative over-engineering)
1. **Tax only, this sprint.** Seed only `domain='tax'` exception types; build only tax
   detectors, routes, UI, tests. Other domains are schema-ready but unimplemented.
2. **`domain` is required and CHECK-constrained** to the enumerated set (extensible by a
   later additive migration), not a free string.
3. **Domain-aware authorization from day one.** Record-scoped domains (`tax`, `wealth`,
   `portal`) enforce person/household scope; system domains (`operations`, `microsoft`)
   are firm-scoped (require `record.read_all` / admin/ops capabilities). Bake this branch
   into the scope service now so later domains inherit it.
4. **Capabilities:** `exception.read` / `exception.write` / `exception.resolve`
   (+ a sensitive `exception.compliance` for compliance-domain resolution), granted per
   role; scope narrows the *rows*, capabilities gate the *verbs*. Avoid re-granting broad
   `tax.*`.

### Consequences if approved
- Update `docs/SPRINT_5_5_EXCEPTION_DESIGN.md` §5 (table names → `exceptions*` + `domain`),
  §7/§9 (capabilities → `exception.*`), and §6 integration hooks (tag each detector with a
  `domain`). No change to the exception catalog, lifecycle, SLA/escalation, or integration
  semantics — only the naming/scoping generality.
- Record this as **ADR-17** in `docs/PRODUCTION_ARCHITECTURE.md` when implementation begins.

**If Option A is preferred instead**, accept the future generalization cost and document
that per-domain exceptions will be separate subsystems.
