# Regulatory Obligation Registry (Phase D.59)

The **regulatory obligation registry** (`REGULATORY_OBLIGATION_REGISTRY` in
`app/services/regulatory_readiness/registry.py`) is the declarative catalog of the firm's regulatory /
supervisory obligation domains and, for each, the **authoritative owner** plus its evidence / review /
exception / approval / filing / retention owners, accountable business owner, and accountable compliance
reviewer. It is **metadata only** — it must never become a persisted risk/obligation register.

> It does not assert that a rule applies unless the existing platform or approved architecture establishes that
> applicability. Unknown applicability is represented honestly via `not_configured`.

## Obligation domains

Each obligation declares its `reg_domain`, `authoritative_owner` (or `not_configured`), `evidence_owner`,
`review_owner`, `exception_owner`, `approval_owner`, `filing_owner`, `retention_owner`, `business_owner`,
`compliance_reviewer` (default `reviewer_not_confirmed`), `capabilities`, `runtime_gate`, `deep_links`, and
`config_status`.

| Obligation | Domain | Authoritative owner | Config |
| --- | --- | --- | --- |
| `investment_adviser_registration` | registration | **not_configured** | **not_configured** |
| `form_adv_governance` | registration | **not_configured** | **not_configured** |
| `books_and_records` | records | document_intelligence | configured |
| `privacy_safeguarding` | privacy | security_operations | configured |
| `cybersecurity_governance` | security | security_operations | configured |
| `business_continuity` | resilience | business_continuity | configured |
| `vendor_oversight` | vendor | vendor_management | configured |
| `communications_supervision` | supervision | compliance_intelligence | configured |
| `advertising_marketing_review` | supervision | **not_configured** | **not_configured** |
| `suitability` | supervision | compliance_intelligence | configured |
| `replacement_1035_review` | supervision | compliance_intelligence | configured |
| `licensing_registration` | licensing | insurance_licensing | configured |
| `continuing_education` | licensing | insurance_licensing | configured |
| `fee_billing_oversight` | financial | financial_operations | configured |
| `custody_asset_verification` | financial | **not_configured** | **not_configured** |
| `best_interest_obligations` | supervision | compliance_intelligence | configured |
| `conflicts_of_interest` | supervision | **not_configured** | **not_configured** |
| `complaint_handling` | supervision | **not_configured** | **not_configured** |
| `document_retention` | records | document_intelligence | configured |
| `supervisory_review` | supervision | compliance_intelligence | configured |
| `tax_practice_controls` | tax | exception_engine | configured |
| `insurance_practice_controls` | insurance | insurance_licensing | configured |
| `employee_benefits_controls` | benefits | exception_engine | configured |

**17 configured, 6 not_configured.**

## The not_configured obligations (reported honestly)

**IA registration, Form ADV governance, advertising & marketing review, custody & asset verification,
conflicts of interest, and complaint handling** have **no authoritative owner in the platform today** (the D.59
audit confirmed no filing / Form ADV / advertising-review / custody-verification / conflicts / complaint
store). Rather than assert a rule applies or fabricate a status, they are declared `not_configured`. Applicability
is asserted only where the platform establishes an owner; unknown applicability is honest.

## Accountable business owner vs compliance reviewer

Each obligation names a `business_owner` (`business_operations`) and a separate `compliance_reviewer` (default
`reviewer_not_confirmed`). The two are distinct: **business approval is not regulatory certification**, and the
compliance reviewer stays unconfirmed until a recorded `reviewer_authorities` record confirms authority — never
inferred. See [CERTIFICATION_SIGNOFF_REGISTRY.md](CERTIFICATION_SIGNOFF_REGISTRY.md).

## How the registry is used

The `examination_readiness` + `obligation_coverage` dashboards compose `regulatory_obligation_inventory`
(DERIVED), `configured_obligation_coverage` (DERIVED), and `unconfigured_obligation_inventory`. Governance
validates that every obligation declares its fields, that every **configured** obligation names an authoritative
owner, and that keys are unique.

See [EVIDENCE_REGISTRY.md](EVIDENCE_REGISTRY.md), [EXAMINATION_REQUEST_REGISTRY.md](EXAMINATION_REQUEST_REGISTRY.md),
[REGULATORY_EXAMINATION_READINESS.md](REGULATORY_EXAMINATION_READINESS.md), and
[ADR-064](adr/ADR-064-regulatory-examination-readiness.md).
