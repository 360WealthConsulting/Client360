# Regulatory Readiness Governance (Phase D.59)

`app/services/regulatory_readiness/governance.py` is a read-only checker that verifies the readiness layer
stays a **composition** over the authoritative regulatory / evidence / certification owners and never becomes a
second compliance platform, examination-management system, audit platform, document repository, records-management
system, regulatory filing system, certification engine, evidence vault, supervisory approval engine, or
policy-management system. It returns `{ok, issue_count, findings}` and **never raises** into normal use.
`validate_regulatory_readiness()` is surfaced through the internal diagnostics endpoint
(`/regulatory-readiness/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB, opens `engine.begin(`,
   publishes to the outbox, or writes audit events (`write_audit(`). No `rm_*` projection table is read
   directly.
2. **No second compliance / evidence / filing / certification engine — no mutation.** No module calls an
   evidence / review / approval / filing / retention / authority mutation — `record_decision(`,
   `submit_review(`, `assign_reviewer(`, `activate(`, `revoke(`, `supersede(`, `record_evidence(`,
   `record_workflow_evidence(`, `record_license(`, `create_retention_assignment(`, `execute_deletion(`,
   `place_legal_hold(`, `resolve(`, `write_audit(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class.
4. **Registry completeness + single ownership.** Every obligation / evidence / examination-request /
   certification / panel / dashboard key is unique; every **configured** obligation names an authoritative
   owner; every **configured** evidence class names an authoritative + storage owner.
5. **Reviewer authority never inferred; business approval is not certification.** No certification carries a
   status other than `reviewer_not_confirmed` / `blocked` / `not_configured` (`fabricated_certification_status`
   otherwise); no certification carries a named reviewer without confirmed authority
   (`inferred_reviewer_authority`); no certification carries a fabricated review date; every blocked
   certification states a `blocked_reason`.
6. **No fabricated readiness score.** Any readiness / coverage / `*_score` panel **derived from the layer's own
   registries/compose** must be labeled `derived` (`unlabeled_derived_readiness` otherwise). The derived
   readiness summary describes operational readiness, never regulatory certification.
7. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in both
   `model.py` and `panels.py`.
8. **No raw environment gating.** Gates flow through the Runtime + Policy engines — never `os.getenv` /
   `os.environ`.

## No sensitive evidence, ever

Panels and summaries carry **counts, status, coverage, freshness, and age bands only** — never document
contents, tax-return contents, client narratives, regulator-correspondence contents, audit payloads,
credentials, tokens, account numbers, license keys, PII, private incident narratives, or evidence files. The
composed owners already strip sensitive payloads; the readiness layer surfaces only aggregates about them.

## Honest not_configured + no-compliance-from-missing-findings

Regulatory filing / acknowledgements, examination-case ownership, certification reviewers, evidence export,
backup/restore evidence, and several obligations have **no authoritative owner today** and are declared
`not_configured` — reported honestly, never fabricated. **Operational readiness is never regulatory
certification**, there is no single "compliant" result, and an absent finding is never interpreted as
compliance (the summary carries `operational_readiness_not_certification: True` and
`absence_of_findings_is_not_compliance: True`).

## Authorization & least privilege

- Readiness routes admit a **supervisor OR an executive** (`compliance.supervise` / `analytics.executive`, via
  `require_any_capability`); diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities`; otherwise
  `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to its authoritative-source capability. A principal lacking the panel capability
  receives a `restricted` panel with `value = None`, no hidden count, and no freshness or leaking metadata.
- Client-scoped sections compose ONLY owners that support per-entity record scope — firm-wide examination
  posture is never exposed to a client-scoped view.

## AI Assist boundary

AI Assist may **summarize** obligation coverage, evidence availability / gaps / staleness, unresolved findings,
blocked certifications, reviewer-not-confirmed status, licensing & CE gaps, filing-acknowledgement availability,
examination-request coverage, and derived operational-readiness — distinguishing authoritative facts,
source-provided approvals, derived summaries, blocked items, reviewer-not-confirmed items, unavailable
information, and not_configured domains (fact class `DERIVED`, counts only, deep links only). It **never**
certifies compliance, claims regulator acceptance, approves a rule set, signs an attestation, infers reviewer
authority, invents evidence, fabricates a filing acknowledgement, submits evidence, files a form, closes a
finding, resolves an exception, treats business approval as regulatory certification, or interprets an absent
finding as compliance.

## Enforcement

`tests/test_regulatory_readiness.py` exercises the four registries, completeness + duplicate-key prevention +
configured-owner validation + honest not_configured, all-certifications-blocked + reviewer-never-inferred +
business-approval-not-certification + blocked-states-why, explainable composition, authorization (`None` +
restricted with no leaking metadata), gate/policy behavior, the analytics-counter reuse, diagnostics, the
routes (registered + capability-gated supervisor OR executive), AI summarize-only, the
no-fabricated-readiness-score + no-compliance-from-missing-findings invariants, and the architecture invariants.
Route count, section registries, ADR count, and the single migration head are guarded by
`tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [REGULATORY_EXAMINATION_READINESS.md](REGULATORY_EXAMINATION_READINESS.md),
[REGULATORY_OBLIGATION_REGISTRY.md](REGULATORY_OBLIGATION_REGISTRY.md), [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md),
[EXAMINATION_REQUEST_REGISTRY.md](EXAMINATION_REQUEST_REGISTRY.md),
[CERTIFICATION_SIGNOFF_REGISTRY.md](CERTIFICATION_SIGNOFF_REGISTRY.md), and
[ADR-064](adr/ADR-064-regulatory-examination-readiness.md).
