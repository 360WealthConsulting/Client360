# Certification & Sign-Off Registry (Phase D.59)

The **certification & sign-off registry** (`CERTIFICATION_REGISTRY` in
`app/services/regulatory_readiness/registry.py`) is the declarative catalog of the firm's sign-off domains and,
for each, the scope, rule-set / artifact version, accountable reviewer role, reviewer qualification requirement,
status, evidence owner, and approval-artifact owner. It is metadata only.

> **Reviewer authority is never inferred. Business approval is never regulatory certification.**

## The reviewer-authority gate

The authoritative reviewer-authority owner is `compliance/reviewer_authority.py`
(`reviewer_authority(principal_id, *, rule_id, policy_gate, today=)`), which returns an active
`reviewer_authorities` record or `None`. **The `reviewer_authorities` catalog is seeded EMPTY**, so it returns
`None` for everyone and no reviewer is ever fabricated or inferred from a job-title string. `compliance/reviews.py`
`record_decision` double-gates final approval on `pending_review` + rule-catalog version match + a non-None
`reviewer_authority(...)`; any failure blocks the review.

Because the catalog is empty, **every certification in this registry defaults to
`status = reviewer_not_confirmed`**, with `named_reviewer = reviewer_not_confirmed`, `review_date =
not_configured`, and a `blocked_reason`. The layer mirrors `record_decision`'s gate read-only — it never invokes
any mutation and never fabricates an approval, reviewer name, or sign-off date.

## Certification domains (14)

Each certification declares `scope`, `ruleset_version` (`not_configured` until an approved version is recorded),
`accountable_reviewer_role`, `named_reviewer` (`reviewer_not_confirmed`), `reviewer_qualification`,
`review_date` (`not_configured`), `status` (`reviewer_not_confirmed`), `blocked_reason`, `evidence_owner`,
`approval_artifact_owner`, `runtime_gate`, `capabilities`, `deep_link`, and `config_status`.

`compliance_rule_set_approval`, `suitability_rule_approval`, `replacement_1035_approval`, `licensing_rule_approval`,
`ce_rule_approval`, `supervisory_policy_approval`, `cybersecurity_policy_approval`, `business_continuity_review`,
`vendor_risk_approval`, `financial_control_review`, `records_retention_approval`, `annual_compliance_review`,
`architecture_governance_approval`, `release_readiness_approval`.

**All 14 are `reviewer_not_confirmed` / blocked today** — the honest, correct state.

## Business owner vs regulatory certifier (Michael Shelton)

Michael Shelton may remain the **business owner** for workflow and operational requirements, but he is **not**
the regulatory certifier unless a recorded `reviewer_authorities` record confirms that he is the appropriately
licensed and authorized compliance principal for the reviewed rule set. The registry never records him (or
anyone) as the named reviewer without that authoritative confirmation. Business approval alone is never
represented as regulatory certification.

## Blocked / reviewer_not_confirmed treatment

- `status` is only ever `reviewer_not_confirmed`, `blocked`, or `not_configured` — never a fabricated
  `approved`. Governance rejects any other status (`fabricated_certification_status`).
- `named_reviewer` may only be a real name when an authoritative authority record confirms it — otherwise it
  stays `reviewer_not_confirmed`. Governance rejects an inferred reviewer (`inferred_reviewer_authority`).
- `review_date` stays `not_configured` — governance rejects a fabricated date.
- Every blocked certification carries a `blocked_reason` — governance rejects a blocked certification without a
  reason.

## How the registry is used

The `certification_signoff` dashboard composes `blocked_certifications`, `reviewer_not_confirmed_certifications`,
and `approval_artifact_coverage` (all DERIVED). Governance validates completeness, unique keys, the
never-inferred + never-fabricated invariants, and that business approval is not treated as certification.

See [REGULATORY_OBLIGATION_REGISTRY.md](REGULATORY_OBLIGATION_REGISTRY.md), [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md),
[REGULATORY_EXAMINATION_READINESS.md](REGULATORY_EXAMINATION_READINESS.md), and
[ADR-064](adr/ADR-064-regulatory-examination-readiness.md).
