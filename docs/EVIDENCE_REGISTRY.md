# Evidence Registry (Phase D.59)

The **evidence registry** (`EVIDENCE_REGISTRY` in `app/services/regulatory_readiness/registry.py`) is the
declarative catalog of the firm's evidence classes and, for each, the **authoritative owner** plus its storage /
metadata / retention / verification owner, applicable obligation keys, and freshness metadata. **This registry
references evidence only — it must never create, copy, store, alter, or certify evidence.**

## Evidence classes

Each class declares `evidence_class`, `authoritative_owner` (or `not_configured`), `storage_owner`,
`metadata_owner`, `retention_owner`, `verification_owner`, `obligation_keys`, `freshness`, `capabilities`,
`runtime_gate`, `deep_link`, and `config_status`. **27 classes; 5 not_configured.**

| Evidence class | Owner | Config |
| --- | --- | --- |
| `policies_procedures` | compliance_rule_catalog | configured |
| `supervisory_reviews` | compliance_reviews | configured |
| `compliance_approvals` | compliance_reviews | configured |
| `exception_resolutions` | exception_engine | configured |
| `licensing_records` | insurance_licensing | configured |
| `ce_records` | insurance_licensing | configured |
| `communications_review` | compliance_intelligence | configured |
| `document_completeness` | document_intelligence | configured |
| `suitability_evidence` | compliance_intelligence | configured |
| `replacement_1035_evidence` | compliance_intelligence | configured |
| `vendor_review` | vendor_management | configured |
| `cybersecurity_evidence` | security_operations | configured |
| `access_review` | security_operations | configured |
| `business_continuity_evidence` | business_continuity | configured |
| `backup_restore_evidence` | **not_configured** | **not_configured** |
| `financial_reconciliation` | financial_operations | configured |
| `commission_reconciliation` | insurance_reporting | configured |
| `audit_log_verification` | observability.audit | configured |
| `data_quality_validation` | data_governance | configured |
| `architecture_governance_tests` | continuous_integration | configured |
| `automated_test_evidence` | continuous_integration | configured |
| `ci_evidence` | continuous_integration | configured |
| `regulatory_filing_acknowledgements` | **not_configured** | **not_configured** |
| `state_filing_acknowledgements` | **not_configured** | **not_configured** |
| `filing_history` | **not_configured** | **not_configured** |
| `examination_correspondence` | **not_configured** | **not_configured** |
| `remediation_evidence` | exception_engine | configured |

## The not_configured evidence (reported honestly)

**Backup/restore evidence** (backup / restore / DR has no authoritative owner — the D.55 precedent) and the
four **filing / examination** evidence classes (regulatory + state filing acknowledgements, filing history,
examination correspondence — no filing or examination-case owner exists) are declared `not_configured` with
`freshness = not_tracked`. Their panels are emitted `available=False` with `config_status = not_configured` —
honest, never a fabricated acknowledgement.

## References evidence, never creates it

Every configured class names a real authoritative owner that already holds the evidence (Compliance
Intelligence, the Exception Engine, Document Intelligence, Insurance licensing, Security Operations, the audit
log, the CI pipeline, …). The registry stores a pointer + freshness metadata; the layer never copies, stores,
alters, or certifies the evidence, and never packages or exports it (evidence export is `not_configured`).

## How the registry is used

The `evidence_completeness` + `evidence_freshness` dashboards compose `evidence_class_inventory` (DERIVED),
`evidence_availability` (DERIVED), `stale_evidence` (DERIVED age-band from freshness), `unverifiable_evidence`,
`evidence_completeness` (Document Intelligence), and `retention_coverage`. Governance validates that every
class declares its fields, that every **configured** class names an authoritative + storage owner, and that
keys are unique.

See [REGULATORY_OBLIGATION_REGISTRY.md](REGULATORY_OBLIGATION_REGISTRY.md),
[EXAMINATION_REQUEST_REGISTRY.md](EXAMINATION_REQUEST_REGISTRY.md),
[REGULATORY_EXAMINATION_READINESS.md](REGULATORY_EXAMINATION_READINESS.md), and
[ADR-064](adr/ADR-064-regulatory-examination-readiness.md).
