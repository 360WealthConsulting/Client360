# Client360 — Platform Re-Implementation Roadmap (current track)

**This is the single source of truth for the current, ADR-driven re-implementation
track's implementation sequencing** — epic numbering, epic status, feature sequencing,
milestone history, and release progression. Governed by
[ADR-014](adr/ADR-014-engineering-backlog-and-roadmap-governance.md) (roadmap governance)
and [ADR-013](adr/ADR-013-repository-reconciliation.md) (in-place reconciliation). ADRs
record *architecture decisions*; this document records *sequencing*.

> **Two roadmaps, distinct numbering — do not conflate.**
> - **Current track (this document):** the ADR-driven platform re-implementation. Epics are
>   `Epic N — {Theme}`, features `F{N}.x`, milestone tags `v0.{N}-{slug}`. **Unqualified
>   "Epic N" means this track.**
> - **Legacy product track:** `docs/ROADMAP.md`, `EPIC_4_PRACTICE_MANAGEMENT_PLATFORM.md`,
>   `EPIC_5_TAX_PRACTICE_PLATFORM.md`, `EPIC_5_REVISED_PLAN.md` — historical, frozen, shipped
>   in v0.9.x–1.0. Always referenced as **"Legacy Epic N — {Theme}"**. Legacy Epic 4 =
>   Practice Management; legacy Epic 5 = Tax Practice Platform (≠ this track's Epic 4/5).

## Epic sequence

| Epic | Theme | Features | Status | Governing ADR | Milestone tag |
|---|---|---|---|---|---|
| **Epic 1** | Platform Foundation | F1.1–F1.5 (dev env, DB baseline, CI/quality gates, observability, transactional outbox, event envelope, workflow template registry) | ✅ Complete | ADR-013 | (foundation — integrated on `release/0.12.0`) |
| **Epic 2** | Identity & Security | F2.1 authentication · F2.2 RBAC · F2.3 object-level · F2.4 field-level · F2.5 security-audit sinks | ✅ Complete | ADR-013 | `v0.2-identity-security-foundation` |
| **Epic 3** | Audit & Evidence | F3.1 append-only audit · F3.2 hash-chain integrity · F3.3 evidence write-once · F3.4 auditor read/export | ✅ Complete | [ADR-015](adr/ADR-015-tamper-evident-audit-architecture.md) | `v0.3-audit-evidence-foundation` |
| **Epic 4** | Workflow Orchestration & Execution | F4.1 registry binding · F4.2 state machine · F4.3 event publication · F4.4 automation consumers · F4.5 approval engine · F4.6 SLA & escalation · F4.7 audit/evidence + capability reconciliation · F4.8 API surface | ✅ Complete | [ADR-016](adr/ADR-016-workflow-orchestration-architecture.md) | `v0.4-workflow-orchestration-foundation` |
| **Epic 5** | Notifications & Communications Foundation | F5.1–F5.7 (planned: data model/ledger · channel registry · preferences/consent · event-driven triggers · dispatch/retry · audit/evidence · API surface) | 🔲 Planned — **scope adopted** | ADR-017 *(pending)* | `v0.5-…` *(planned)* |

## Milestone history

| Tag | Commit | Epic | Notes |
|---|---|---|---|
| `v0.2-identity-security-foundation` | `b823cc8` | Epic 2 | Identity & Security Foundation |
| `v0.3-audit-evidence-foundation` | `54964ed` | Epic 3 | Audit & Evidence Foundation |
| `v0.4-workflow-orchestration-foundation` | `55c8494` | Epic 4 | Workflow Orchestration Foundation |

## Release progression
- All current-track epics are integrated on the app release branch **`release/0.12.0`** and
  marked with epic-milestone tags (`v0.{N}-…`), each with a GitHub Release. The app SemVer
  line (`v0.9.x → v0.12.0`) belongs to the legacy release train; the current track layers
  epic-milestone tags on top of it (see ADR-014 numbering rules).
- **Current head:** `release/0.12.0` @ `55c8494` (Epic 4 released). Single Alembic migration
  head `f41b2n3d4c5e`.

## Adopted next epic
- **Epic 5 — Notifications & Communications Foundation** (scope adopted; see the E5.0
  planning checkpoint). A platform-level, provider-neutral, event-driven notification
  delivery capability that reconciles the scattered portal/benefits provider code, consumes
  the F1.3 outbox / F4.3 event stream, and records reference-only audit/evidence. To be
  governed by **ADR-017** (not yet drafted). Preserves the Epic 1–4 principles: additive,
  deterministic, idempotent, append-only audit/evidence, least-privilege, separation of
  concerns, backward compatibility.

## Governance
See [ADR-014](adr/ADR-014-engineering-backlog-and-roadmap-governance.md) for ownership,
numbering rules, track separation, acceptance workflow, and change management. This roadmap
is updated on each feature/epic acceptance; new epics are numbered here before their ADR.
