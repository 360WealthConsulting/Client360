# Governance (Git-canonical operational artifacts)

> **Release 0.11.0 · Phase A (P2) skeleton — structure only.** This tree is a **skeleton**. It
> contains directory purpose, metadata conventions, and guidance **only**. **No** policies,
> controls, disaster-recovery procedures, runbooks, inventories, schedules, operational rules, or
> other substantive governance content are authored here yet. Authored content arrives in later,
> separately approved phases (roadmap Phase B/D) under the Definition of Done.

## Purpose

`governance/` is the **Git-canonical home** for the firm's version-controlled operational
artifacts — policies, runbooks, business-continuity/DR plans, controls & compliance registers,
asset/configuration inventories, and operating-calendar data. Anything that needs a **change
history, review, and audit trail** lives here so it is PR-reviewed and versioned, per the
documentation framework (`docs/documentation-framework/06-SYNC-AND-DEFINITION-OF-DONE.md` §1).

## Git-canonical status

Artifacts under `governance/` are **canonical in Git**. Confluence renders a generated summary +
link; it never holds the authoritative copy of a governance artifact. Editing happens here, via
pull request.

## Relationship to Confluence

- Git (`governance/`) = the authoritative, versioned source.
- Confluence = the published, staff-facing rendering (a summary + backlink), surfaced in later
  phases by the sync tooling (roadmap Phase E). No governance artifact is authored in both places.

## One-canonical-home rule

Every artifact has **exactly one** canonical home. A governance artifact that is git-canonical here
is **linked** (never copied) from its Confluence area page and from the Publication Register. The
register's `canonical_source` field is the contract.

## Directory inventory

| Directory | Holds (future) | Doc type | Profile |
|---|---|---|---|
| `policies/` | Firm policies/standards (security, data-retention, HR, AUP, compliance) | `POLICY` | operations |
| `runbooks/` | System operational + emergency procedures | `RUNBOOK` | infrastructure |
| `dr/` | Business Continuity & DR plans (RTO/RPO) | `BCDR` | infrastructure |
| `controls/` | Controls & Compliance Register (controls, evidence, audit calendar) | `CONTROLS` | operations |
| `inventory/` | Asset & Configuration Inventory (CMDB-lite) | `ASSET` | infrastructure |
| `calendar/` | Operating Calendar & Key Dates (data) | `CALENDAR` | operations |

Each directory has a `README.md` defining its scope and metadata. See also `CONTRIBUTING.md`.

## Ownership expectations

Every future artifact names an **accountable owner** and an **independent reviewer** (page
front-matter). **Michael Shelton** is the **business owner** for workflow/operational requirements.
Business ownership is **not** regulatory certification (see AD-5, and `CONTRIBUTING.md`).

## Review expectations

Every artifact carries `last_reviewed`, `review_cycle`, and `next_review`. Overdue items surface
through the Review Calendar in later phases. Skeleton READMEs use `TBD`/`UNFILLED` placeholders.

## AD-5 boundary

The **accountable compliance reviewer is UNFILLED (AD-5)**. Regulated rule sets — insurance
**suitability**, **replacement/1035**, **licensing**, and **continuing-education** — remain
**blocked** and may not be authored, approved, or marked publishable here until a **qualified, named
compliance reviewer** is identified and provides the required sign-off artifact. This skeleton
authors **no** regulated rule set.

## Prohibition against secrets or client data

**Never** commit secrets, credentials, tokens, certificates, private keys, endpoints, or **client/PII
data** to this tree. Governance artifacts are policy/procedure text and metadata only; sensitive
values live in the platform secret store or authoritative systems, referenced by name, never by
value.

## Phase-A skeleton vs future authored content

This commit provisions **structure and guidance only**. The distinction is deliberate: a directory
README describing *what a policy is* is Phase A; an *actual policy* is later authored content subject
to the Definition of Done and (for regulated material) AD-5 clearance.
