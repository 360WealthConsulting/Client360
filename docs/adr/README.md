# Client360 Architecture Decision Records (ADRs)

## Purpose
ADRs preserve the **reasoning** behind Client360's major architectural decisions — the
problem that existed, the decision made, the alternatives rejected, the consequences, the
constraints future phases must preserve, and the conditions under which a decision may be
revisited. They exist so future development does not have to reverse-engineer *why* the current
design is the way it is.

All ADRs in this set reflect the architecture **as implemented after Phase D.12A** on
`release/0.13.0` (migration head `j0b1u2s3o4w5`, 352 routes). They describe implemented
decisions, not aspirations.

## Three documentation layers (do not conflate)
- **`docs/PLATFORM_ARCHITECTURE.md`** — *what exists now* (authoritative top-level reference:
  domains, source-of-truth matrix, capabilities, scope, redaction, boundaries).
- **`docs/adr/`** (this directory) — *why the key decisions were made* (durable rationale).
- **`docs/PHASE_D*.md` / `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md`** — *phase-specific history*
  (how each slice was designed and delivered). These remain historical and are not rewritten.

## ADR numbering rules
- ADRs are numbered sequentially: `ADR-001`, `ADR-002`, … (zero-padded to three digits).
- Filenames: `docs/adr/ADR-NNN-short-kebab-title.md`.
- Numbers are **never reused**. A number, once assigned, belongs to that decision forever
  (even if later superseded).
- A new decision that replaces an old one gets a **new** number and marks the old one
  `Superseded` (see below).

## Status definitions
- **Accepted** — the decision is in force and reflected in the current codebase.
- **Superseded** — replaced by a later ADR (which is named in the `Superseded_by` note). The
  superseded ADR is kept as a historical record and is **not** deleted or rewritten.
- **Deprecated** — no longer recommended but not yet replaced; retained for context.
- **Proposed** — under consideration, not yet in force. (No ADR in this set is `Proposed`.)

## How to propose a new ADR
1. Copy the structure of an existing ADR (headings below are mandatory).
2. Assign the next unused sequential number.
3. Set `Status: Proposed` while under review; change to `Accepted` on approval.
4. Fill every required section with **evidence from the code** (files, migrations, tests).
5. Add a row to the table below and link it.
6. Update `docs/PLATFORM_ARCHITECTURE.md` and, where hard facts change,
   `docs/platform_architecture_manifest.yaml` and the relevant tests.

## How to supersede an ADR
- Do **not** edit the accepted ADR's decision. Create a **new** ADR that states it supersedes
  the prior one; add `Superseded_by: ADR-NNN` to the old ADR's Status section and set its
  status to `Superseded`. Accepted ADRs are historical records — only typographical fixes that
  do not alter the decision are permitted in place.

## Required ADR headings
Every ADR must contain, in order: `# ADR-NNN — Title`, `## Status`, `## Date`,
`## Decision owners`, `## Context`, `## Decision`, `## Alternatives considered`,
`## Reasons for the decision`, `## Consequences` (with `### Positive consequences` and
`### Negative consequences and tradeoffs`), `## Enforcement`, `## Exceptions`,
`## Revisit conditions`, `## References`.

## Decision ownership and approval
ADR **acceptance is architectural approval, not automatically regulatory approval.**
- General technical ADRs require: **Platform Architecture**, the relevant **Domain Owner**, and
  the **Business Operations Owner**.
- Compliance-related ADRs (ADR-008, and any future ADR affecting regulated rule sets or
  approval authority) additionally require an **authorized Compliance reviewer**. Where the
  repository does not yet name one, ADRs state *"Authorized compliance reviewer: Not yet
  designated"* and mark any future regulated rule change as requiring compliance sign-off.
- **Michael Shelton** may be listed as **Business Operations Owner** for workflow and
  operational requirements. His business approval is **not** regulatory certification.

## Architecture change process
1. Determine whether a change affects an **Accepted** ADR.
2. If not, implement under the existing architecture.
3. If it does, do **not** silently edit the accepted ADR.
4. Create a new ADR that **supersedes** the prior one or **records an approved exception**.
5. Update `PLATFORM_ARCHITECTURE.md`, `platform_architecture_manifest.yaml` (where hard facts
   change), relevant tests, and phase documentation.
6. Obtain required architectural (and, where applicable, compliance) approvals.
7. Merge only after CI passes.

## Reviewer expectations
Reviewers confirm that each ADR: reflects **implemented** code (not aspiration); cites
repository-relative evidence; keeps composition layers non-authoritative; keeps regulatory
approval inside authorized Compliance; and does not rewrite prior phase history.

## ADR index

| # | Title | Status | Decision area | Related phase |
|---|-------|--------|---------------|---------------|
| [ADR-001](ADR-001-composition-layers.md) | Composition layers | Accepted | Platform structure | D.2–D.12 |
| [ADR-002](ADR-002-domain-ownership-and-source-of-truth.md) | Domain ownership and source of truth | Accepted | Platform structure | D.2–D.12A |
| [ADR-003](ADR-003-relationship-entity-and-business-ownership-graph.md) | Relationship-entity & business-ownership graph | Accepted | Identity/relationships | D.12 |
| [ADR-004](ADR-004-server-side-authorization-and-record-scope.md) | Server-side authorization & record scope | Accepted | Security | D.1–D.12 |
| [ADR-005](ADR-005-sensitive-data-redaction-and-restricted-vs-missing.md) | Sensitive-data redaction; restricted vs missing | Accepted | Security | D.10–D.12 |
| [ADR-006](ADR-006-advisor-intelligence-as-deterministic-computation.md) | Advisor Intelligence as deterministic computation | Accepted | Intelligence | D.5 |
| [ADR-007](ADR-007-advisor-work-as-owned-work-management-not-workflow-engine.md) | Advisor Work: work management, not workflow engine | Accepted | Work | D.9 |
| [ADR-008](ADR-008-compliance-decision-and-reviewer-authority-boundaries.md) | Compliance decision & reviewer-authority boundaries | Accepted | Compliance | D.6–D.8 |
| [ADR-009](ADR-009-activity-timeline-as-projection-not-event-sourced-platform.md) | Activity Timeline as projection, not event sourcing | Accepted | Events | D.10 |
| [ADR-010](ADR-010-annual-review-as-meeting-oriented-composition.md) | Annual Review as meeting-oriented composition | Accepted | Composition | D.11 |
| [ADR-011](ADR-011-business-owner-planning-as-business-planning-composition.md) | Business Owner Planning as business-planning composition | Accepted | Composition | D.12 |
| [ADR-012](ADR-012-business-planning-profile-as-narrow-prospective-persistence.md) | Business-planning profile: narrow prospective persistence | Accepted | Persistence | D.12 |
| [ADR-013](ADR-013-additive-read-services-belong-to-owning-domains.md) | Additive reads belong to owning domains | Accepted | Service design | D.11–D.12 |
| [ADR-014](ADR-014-no-mutation-during-incidental-rendering.md) | No mutation during incidental rendering | Accepted | Integrity | D.12 |
| [ADR-015](ADR-015-no-fabricated-data-history-calculations-or-relationships.md) | No fabricated data, history, calculations, or relationships | Accepted | Integrity | D.5–D.12 |
| [ADR-016](ADR-016-linear-migration-chain-and-declared-schema-registration.md) | Linear migration chain & declared-schema registration | Accepted | Database | D.6–D.12 |
| [ADR-017](ADR-017-architecture-manifest-and-enforcement-tests.md) | Architecture manifest & enforcement tests | Accepted | Governance | D.12A |
| [ADR-018](ADR-018-opportunity-pipeline-as-authoritative-domain.md) | Opportunity & Pipeline as an authoritative domain | Accepted | Domain (business development) | D.13 |
| [ADR-019](ADR-019-campaigns-referral-sources-and-attribution.md) | Campaigns, Referral Sources & attribution | Accepted | Domain (business development) | D.14 |
| [ADR-020](ADR-020-analytics-as-read-model.md) | Enterprise Analytics as a deterministic read-model | Accepted | Domain (analytics) | D.15 |
| [ADR-021](ADR-021-document-platform-as-authoritative-domain.md) | Document Management as the authoritative artifact domain | Accepted | Domain (documents) | D.16 |
| [ADR-022](ADR-022-workflow-orchestration-layer.md) | Workflow Automation as an orchestration layer over the existing engine | Accepted | Domain (workflow) | D.17 |
| [ADR-023](ADR-023-communications-as-authoritative-domain.md) | Communications as an authoritative communication-metadata domain | Accepted | Domain (communications) | D.18 |
| [ADR-024](ADR-024-scheduling-as-authoritative-domain.md) | Scheduling as an authoritative scheduling-metadata domain | Accepted | Domain (scheduling) | D.19 |
| [ADR-025](ADR-025-operations-as-authoritative-firm-domain.md) | Enterprise Operations as an authoritative firm-operations domain | Accepted | Domain (operations) | D.20 |

Related: `docs/PLATFORM_ARCHITECTURE.md`, `docs/ADVISOR_WORKSPACE_ARCHITECTURE.md`,
`docs/platform_architecture_manifest.yaml`, `tests/test_platform_architecture.py`,
`tests/test_architecture_decision_records.py`.
