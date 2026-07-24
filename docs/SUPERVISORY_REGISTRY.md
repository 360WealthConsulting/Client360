# Supervisory Registry (Phase D.47)

`app/services/compliance_intelligence/registry.py` holds the two declarative catalogs of the supervisory
layer — the authoritative catalog of supervisory review types and compliance exception types. See
[`ADR-052`](adr/ADR-052-compliance-intelligence.md).

## Supervisory review registry
`SUPERVISORY_REGISTRY` — each `SupervisoryReviewType` declares:
- `key`; `owner` (authoritative owning service); `governing_workflow` (owns resolution); `policy_owner`;
- `required_evidence`; `approval_authority`; `escalation_path`;
- `retention_class` (`regulatory` | `standard` — declarative bucket; enforcement stays with the owner);
- `deep_link` (authoritative surface); `runtime_gate` (the governed flag guarding the type); `lifecycle`;
- `populated` (whether an authoritative source currently supplies items).

### Registered review types (12)
`suitability_review`, `annual_review_oversight`, `account_opening_review`, `trade_review`,
`document_review`, `communication_review`, `workflow_review`, `advertising_review`, `compliance_exception`,
`licensing_review`, `continuing_education_review`, `complaint_review`.

**Populated** (backed by real authoritative data today): suitability, annual-review oversight,
document review, compliance exception, licensing, continuing-education. **Declared-but-unpopulated**
(no backing model yet, so the catalog is complete and future-ready): account-opening, trade, communication,
workflow, advertising, complaint. The engine emits items only where an authoritative source supplies them.

## Exception registry
`EXCEPTION_REGISTRY` — each `ComplianceExceptionType` declares `key`, `owner`, `default_severity`,
`lifecycle`, `governing_policy`, `escalation`, `suppression`.

### Registered exception types (10)
`overdue_review`, `missing_document`, `unsigned_disclosure`, `missing_beneficiary`,
`stale_financial_information`, `missing_compliance_approval`, `communication_exception`, `licensing_issue`,
`ce_deficiency`, `workflow_violation`.

## How types are populated
- `overdue_review` ← `portfolio.accounts_due_for_review`; `missing_beneficiary` ←
  `accounts_missing_required_beneficiary`; `missing_compliance_approval` ← blocked compliance reviews;
  `missing_document` / `unsigned_disclosure` / `stale_financial_information` ← exception-engine rows mapped
  by keyword; `licensing_issue` / `ce_deficiency` ← `insurance_licensing`.
- Exception-engine rows that do not map to a registered type are **suppressed** (counted) — every emitted
  exception is a registered type. Governance asserts this.

## Onboarding a new type
Add a `SupervisoryReviewType` / `ComplianceExceptionType` (via the `_s(...)` / `_x(...)` helper) with its
owner, workflow, policy owner, evidence, authority, escalation, deep link, and runtime gate. When an
authoritative source appears, flip `populated=True` and add its adapter mapping. Governance verifies
completeness + single ownership (no duplicate keys).

## References
`app/services/compliance_intelligence/registry.py`, `app/services/compliance_intelligence/adapters/*`,
`app/services/compliance_intelligence/governance.py`, `tests/test_compliance_intelligence.py`, ADR-052.
