# Architecture Decision Records (ADRs)

ADRs are the **only** sanctioned way to change Client360's architecture or the
frozen baseline documents (Engineering Constitution §3). Each ADR records a
decision, its alternatives, rationale, and consequences.

| ADR | Title | Status |
|---|---|---|
| ADR-001…012 | Founding architectural decisions (event-driven, PostgreSQL, workflow engine, evidence-first, reference documents, domain boundaries, connector framework, security model, data ownership, compliance boundaries, modular monolith, CQRS-lite) | Baselined in the Enterprise Architecture Package |
| [ADR-013](ADR-013-repository-reconciliation.md) | Reconcile the Enterprise Architecture with the existing in-place application | Accepted |
| [ADR-014](ADR-014-engineering-backlog-and-roadmap-governance.md) | Engineering backlog & roadmap governance (single-source-of-truth roadmap; two-track numbering rules) | Accepted |
| [ADR-015](ADR-015-tamper-evident-audit-architecture.md) | Tamper-evident audit architecture (in-database per-chain SHA-256 hash chain) | Accepted |
| [ADR-016](ADR-016-workflow-orchestration-architecture.md) | Workflow orchestration architecture (Option B bounded hybrid; platform adapter over the preserved engine) | Accepted |
| [ADR-017](ADR-017-notifications-and-communications-architecture.md) | Notifications & communications architecture (Epic 5; canonical platform notification service reconciling portal + benefits) | Accepted |

> ADR-001…012 are catalogued in the frozen Enterprise Architecture Package. This
> directory holds ADRs recorded from ADR-013 onward; earlier ADRs may be
> transcribed here later without changing their content.
>
> **ADRs record architecture decisions; implementation sequencing (epic numbering,
> status, milestones) lives in the canonical roadmap
> [`../REIMPLEMENTATION_ROADMAP.md`](../REIMPLEMENTATION_ROADMAP.md), governed by ADR-014.**
> Note the two distinct roadmap tracks: the current ADR-driven re-implementation
> ("Epic N") and the frozen legacy product roadmap ("Legacy Epic N", see `docs/ROADMAP.md`).

---

## ADR namespace authority & legacy-reference mapping

**This directory — the formal, zero-padded set `ADR-001…ADR-017` — is the single
authoritative ADR namespace** (Engineering Constitution §3). Always cite formal ADRs
in the **zero-padded** form: `ADR-013`, `ADR-016`, `ADR-017`. Every `ADR-0NN`
reference in the documentation and code resolves to this directory (ADR-013…017) or
to the founding Enterprise Architecture Package (ADR-001…012).

A **legacy, non-padded** numbering (`ADR-1…ADR-18`) also exists: it is the
self-contained decision **summary** in
[`../../PRODUCTION_ARCHITECTURE.md` §25](../../PRODUCTION_ARCHITECTURE.md), and two of
its IDs (legacy `ADR-17`, `ADR-18`) are additionally referenced by some frozen
release/RC docs and by existing code comments. This legacy scheme **predates and does
not map 1:1** to the formal namespace — the *same number denotes a different
decision*:

| Legacy `ADR-N` (PRODUCTION_ARCHITECTURE §25) | Formal `ADR-0NN` (this directory) |
|---|---|
| ADR-13 = Fernet-encrypted MSAL token cache | ADR-013 = Repository reconciliation |
| ADR-14 = Canonical authorization service | ADR-014 = Backlog & roadmap governance |
| ADR-15 = Shared API response envelope (planned) | ADR-015 = Tamper-evident audit |
| ADR-16 = Inert AI port | ADR-016 = Workflow orchestration |
| ADR-17 = Platform-wide Exception Engine | ADR-017 = Notifications & communications |

> **Discriminator:** **zero-padded `ADR-0NN` → formal ADR (this directory); non-padded
> `ADR-N` → the legacy PRODUCTION_ARCHITECTURE §25 summary.**

**Redirect for legacy IDs referenced outside §25.** These decisions have **no formal
ADR file**; their authoritative home is the governing document below. The legacy IDs
are **intentionally preserved** (they are cited by frozen release/RC docs and by code
comments that are out of scope to rewrite):

| Legacy ID | Decision | Authoritative home |
|---|---|---|
| ADR-7  | Provider-neutral adapters | Founding EA package (ADR-001–012, connector-framework / security-model) |
| ADR-13 | Fernet-encrypted MSAL token cache | `docs/RELEASE_0.9.9_PLATFORM_CONSOLIDATION.md` §4–5 |
| ADR-14 | Canonical authorization service | `app/security/authorization.py`; founding "security model" ADR (EA package) |
| ADR-17 | Platform-wide Exception Engine | `docs/ADR_EXCEPTION_ENGINE_SCOPE.md`, `docs/SPRINT_5_5_EXCEPTION_DESIGN.md` |
| ADR-18 | Organizations + Employee Benefits | `docs/RELEASE_0.9.11_BENEFITS_ARCHITECTURE.md` |

**Going forward:** cite only the formal zero-padded namespace; a net-new architecture
decision gets a new `ADR-0NN` file in this directory. When one of the legacy
decisions (e.g. the Exception Engine or Benefits) is next materially revised, promote
it into a formal `ADR-0NN` file and link back from its legacy row — do **not**
retro-renumber the existing legacy references.
