# Examination Request Registry (Phase D.59)

The **examination request registry** (`EXAMINATION_REQUEST_REGISTRY` in
`app/services/regulatory_readiness/registry.py`) is a declarative **readiness map** of common
examination-request categories → the required evidence classes + authoritative owners. It is metadata only.

> **This registry is a readiness map only. It does NOT represent an active regulator request** — there is no
> authoritative examination-case owner in the platform, so the layer can never open, track, or respond to a
> real examination. Each entry maps *which owned evidence would answer a request category*, nothing more.

## Request categories (22)

Each request declares `category`, `description`, `required_evidence` (evidence-class keys),
`authoritative_owners`, `review_owner`, `export_owner` (`not_configured` — no evidence-export owner exists),
`capabilities`, `runtime_gate`, `deep_links`, and `config_status`.

`organizational_records`, `registrations_licenses` (not_configured — filing owner absent), `policies_procedures`,
`client_agreements`, `advisory_billing`, `portfolio_custody_records` (not_configured — custody owner absent),
`communications_advertising`, `suitability_best_interest`, `replacement_documentation`, `complaints_incidents`
(not_configured — complaint owner absent), `cybersecurity`, `privacy`, `business_continuity`, `vendor_oversight`,
`financial_records`, `employee_supervision`, `training_ce`, `tax_practice_controls`, `insurance_practice_controls`,
`books_records_retention`, `audit_trails`, `remediation_history`.

## not_configured request categories (reported honestly)

`registrations_licenses` (depends on state filing acknowledgements — no filing owner), `portfolio_custody_records`
(no custody-verification owner), and `complaints_incidents` (no complaint owner) are declared `not_configured`.
The `examination_request_coverage` panel reports which categories have owned evidence vs `not_configured`, and
carries `no_active_examination_case: True`.

## Export owner is not_configured

Every request declares `export_owner = not_configured` — there is **no authoritative evidence-export /
examination-bundle owner** in the platform. The layer never packages, exports, or submits evidence; the
`evidence_export_availability` panel reports `not_configured`.

## How the registry is used

The `obligation_coverage` + `examination_readiness` dashboards compose `examination_request_coverage` (DERIVED).
Governance validates completeness + single ownership (unique keys).

See [REGULATORY_OBLIGATION_REGISTRY.md](REGULATORY_OBLIGATION_REGISTRY.md), [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md),
[REGULATORY_EXAMINATION_READINESS.md](REGULATORY_EXAMINATION_READINESS.md), and
[ADR-064](adr/ADR-064-regulatory-examination-readiness.md).
