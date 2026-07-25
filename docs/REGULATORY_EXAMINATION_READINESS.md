# Enterprise Regulatory Examination Readiness, Evidence Governance & Supervisory Certification (Phase D.59)

`app/services/regulatory_readiness/` is a governed, **read-only composition** that provides a unified, governed
view of the firm's **operational readiness** to respond to regulatory examinations, audits, supervisory
reviews, and evidence requests — obligation coverage, evidence availability/completeness/freshness, supervisory
reviews, certification & sign-off status, filing readiness, and remediation evidence. It is **not** a second
compliance platform, examination-management system, audit platform, document repository, records-management
system, regulatory filing system, certification engine, evidence vault, supervisory approval engine, or
policy-management system: **no new capability, no new metric, no persistence, no mutation, no duplicated
evidence, no migration** (single Alembic head `n5s6u7p8v9w0`).

> **Operational readiness is NOT regulatory certification.** There is no single "compliant / noncompliant"
> result, and an absent finding is never interpreted as compliance.

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| Reviewer authority (the gate) | `compliance/reviewer_authority.py` | `reviewer_authority(...)` (seeded empty → blocked) | record-scoped |
| Rule-set approval | `compliance/rule_catalog.py` | `RuleCatalog.from_registry()` | metadata |
| Supervisory reviews / findings / exceptions | Compliance Intelligence + Exception Engine | `supervisory_dashboard` | `compliance.supervise` |
| Documentation completeness / retention | Document Intelligence (D.50) | `document_summary` | `documents.view` |
| Licensing / CE evidence | Insurance licensing | `list_licenses` / `list_ce` | `insurance.licensing.read` |
| Cybersecurity / access evidence | Security Operations (D.54) | `security_summary` | `security.view` |
| Continuity evidence | Business Continuity (D.55) | `continuity_summary` | `observability.view` |
| Vendor-review evidence | Vendor Management (D.56) | `vendor_summary` | `integration.view` |
| Financial / commission evidence | Financial Operations (D.57) + commission ledger | `firm_financial_summary` / `commission_report` | `analytics.executive` |
| Audit-log / architecture / CI evidence | audit log + CI pipeline | availability only | `observability.audit` |

## Certifications are blocked / reviewer_not_confirmed (never inferred)

The authoritative reviewer-authority owner (`reviewer_authorities`) is **seeded empty**, so
`reviewer_authority(...)` returns `None` for everyone and no reviewer is ever fabricated or inferred from a
role string. Consequently **every certification in the D.59 registry is `reviewer_not_confirmed` / blocked**,
with `named_reviewer` and `review_date` never fabricated, and a `blocked_reason` stating why. **Business
approval is never regulatory certification.** Michael Shelton remains the business owner for workflow and
operational requirements but is **not** the regulatory certifier unless a recorded `reviewer_authorities`
record confirms he is the appropriately licensed and authorized compliance principal for the reviewed rule
set. See [CERTIFICATION_SIGNOFF_REGISTRY.md](CERTIFICATION_SIGNOFF_REGISTRY.md).

## The not_configured areas (reported honestly)

The D.59 audit confirmed several areas have **no authoritative owner** and are declared `not_configured`
(the D.55–D.58 precedent), never fabricated:

- **Regulatory filing / Form ADV / filing acknowledgements** (federal + state) — no filing owner exists.
- **Examination-case ownership / examination correspondence** — no examination-case owner exists; the
  examination-request registry is a **readiness map only**, never an active regulator request.
- **Certification / attestation store** — none beyond the reviews + rule-catalog approvals.
- **Policy acknowledgements** and **evidence export / examination bundling** — no owner exists; the layer never
  packages or submits evidence.
- **Obligation domains** IA registration, Form ADV, advertising review, custody, conflicts, complaints —
  no authoritative owner.

## Registries, panels, dashboards

Four declarative registries — `REGULATORY_OBLIGATION_REGISTRY` (23) + `EVIDENCE_REGISTRY` (27) +
`EXAMINATION_REQUEST_REGISTRY` (22) + `CERTIFICATION_REGISTRY` (14) — plus 37 panels and 8 dashboards
(examination_readiness, obligation_coverage, evidence_completeness, evidence_freshness, supervisory_reviews,
certification_signoff, filing_readiness, remediation_evidence). See
[REGULATORY_OBLIGATION_REGISTRY.md](REGULATORY_OBLIGATION_REGISTRY.md), [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md),
[EXAMINATION_REQUEST_REGISTRY.md](EXAMINATION_REQUEST_REGISTRY.md), and
[CERTIFICATION_SIGNOFF_REGISTRY.md](CERTIFICATION_SIGNOFF_REGISTRY.md). Every dashboard carries a generated
timestamp, governing services, source inventory, explainable panels, deep links, and its configured /
not_configured / blocked domain lists.

## Panels — counts, status, coverage, freshness only

Panels carry counts, status, coverage, freshness, and age bands only. They **never** return document contents,
tax-return contents, client narratives, regulator-correspondence contents, audit payloads, credentials, tokens,
account numbers, license keys, PII, private incident narratives, or evidence files. The
`derived_readiness_coverage` panel is a DERIVED operational-readiness summary (labeled `derived`) — never a
certified score.

## Authorization

- Routes + dashboards admit a **supervisor OR an executive** (`compliance.supervise` / `analytics.executive`,
  via `require_any_capability`); diagnostics by `observability.audit`.
- Each **panel self-restricts** to its authoritative-source capability. A principal lacking the panel
  capability receives a `restricted` panel with `value = None`, no hidden count, and no freshness or leaking
  metadata.
- Client-scoped sections compose ONLY owners that support per-entity record scope — firm-wide examination
  posture, firm-wide incidents, unrelated supervisory findings, other clients' evidence, and confidential
  regulator information are never exposed.

## Runtime, governance, analytics, observability

Every surface is gated through the Runtime Engine (`regulatory_readiness.enabled`, `evidence_governance.enabled`,
`certification_signoff.enabled`, `filing_readiness.enabled`, `readiness_ai_summary.enabled`) **and** the runtime
gate of every composed source, plus the Policy Engine — **no environment bypass**. Governance
(`validate_regulatory_readiness()`) returns `{ok, issue_count, findings}` and forbids persistence, mutation,
any evidence/review/approval/filing/retention/authority mutation, a second metrics registry, a fabricated
readiness score, a fabricated acknowledgement, an inferred reviewer, and a fabricated certification status —
see [REGULATORY_READINESS_GOVERNANCE.md](REGULATORY_READINESS_GOVERNANCE.md). Four low-cardinality counters
register into the **single** Analytics Registry. Internal diagnostics (`/regulatory-readiness/diagnostics`,
`observability.audit`) report registry coverage, configured/not_configured/blocked counts, panel availability,
and the governance summary.

## Surfaces

- **Advisor Workspace** — a **Regulatory Readiness** panel (`ws["regulatory_readiness"]`), stating operational
  readiness is not regulatory certification.
- **Client 360 / Household 360** — an **Evidence & Supervisory Readiness** section (`compliance.supervise`):
  client-relevant, record-scoped evidence signals (documentation completeness, open client-specific compliance
  exceptions, suitability / replacement / workflow-approval evidence).
- **Executive Dashboard** — a **Regulatory Readiness & Evidence** dashboard reusing existing widgets
  (`compliance_workload`, `operational_health`, `runtime_health` — no new widget).
- **AI Assist** — summarizes obligation coverage / evidence availability / gaps / stale evidence / unresolved
  findings / blocked certifications / reviewer-not-confirmed / licensing & CE gaps / filing-acknowledgement
  availability / examination-request coverage / derived operational-readiness, distinguishing authoritative
  facts, source-provided approvals, derived summaries, blocked items, reviewer-not-confirmed items, unavailable
  information, and not_configured domains. It **never** certifies compliance, claims regulator acceptance,
  approves a rule set, signs an attestation, infers reviewer authority, invents evidence, fabricates a filing
  acknowledgement, files a form, closes a finding, resolves an exception, treats business approval as
  regulatory certification, or interprets an absent finding as compliance.

## Routes

`/regulatory-readiness` (HTML) + `/api/v1/regulatory-readiness/{dashboards, dashboard/{key}, summary, registry,
panel/{key}, metrics}` + `/regulatory-readiness/diagnostics`.

See [REGULATORY_OBLIGATION_REGISTRY.md](REGULATORY_OBLIGATION_REGISTRY.md), [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md),
[EXAMINATION_REQUEST_REGISTRY.md](EXAMINATION_REQUEST_REGISTRY.md),
[CERTIFICATION_SIGNOFF_REGISTRY.md](CERTIFICATION_SIGNOFF_REGISTRY.md),
[REGULATORY_READINESS_GOVERNANCE.md](REGULATORY_READINESS_GOVERNANCE.md), and
[ADR-064](adr/ADR-064-regulatory-examination-readiness.md).

**Composed by D.63:** the **Change Management** layer (`/change-management`) composes the regulatory-readiness
governance checker into its derived `governance_status` panel — read-only, `observability.view`. It never files,
signs off, or certifies anything; Regulatory Readiness remains the authoritative evidence-governance owner. Just
as operational readiness is not regulatory certification, change readiness is not production certification — a
green build is not production, merged is not deployed. See
[ENTERPRISE_CHANGE_MANAGEMENT.md](ENTERPRISE_CHANGE_MANAGEMENT.md) and
[ADR-068](adr/ADR-068-change-management.md).
