# Assurance Registry (Phase D.58)

The **assurance registry** (`ASSURANCE_REGISTRY` in `app/services/enterprise_risk/registry.py`) is the
declarative catalog of the firm's assurance sources and, for each, the **assurance owner** plus the evidence it
references, its scope, frequency, reviewer role, and approval artifact. **This registry references evidence; it
must never create evidence.** The Enterprise Risk layer owns no evidence and produces no assurance artifacts.

## Assurance sources

Each source declares `assurance_owner`, `evidence_source`, `scope`, `frequency`, `reviewer_role`,
`approval_artifact`, `runtime_gate`, `capabilities`, `deep_link`, and `config_status`.

| Source | Assurance owner | Evidence source | Frequency |
| --- | --- | --- | --- |
| `compliance_reviews` | compliance_intelligence | compliance_intelligence.supervisory_dashboard | periodic |
| `supervisory_approvals` | compliance_intelligence | compliance_intelligence | per_event |
| `audit_log_verification` | observability.audit | observability.audit | continuous |
| `security_monitoring` | security_operations | security.incidents.metrics | continuous |
| `runtime_governance_checks` | runtime | runtime.consumption | continuous |
| `architecture_guards` | continuous_integration | tests.test_platform_architecture | per_commit |
| `data_quality_validation` | data_governance | data_governance.governance_summary | continuous |
| `workflow_governance_checks` | automation_orchestration | automation_orchestration.automation_summary | continuous |
| `vendor_risk_reviews` | vendor_management | vendor_management.vendor_summary | periodic |
| `continuity_readiness_reviews` | business_continuity | business_continuity.continuity_summary | periodic |
| `financial_reconciliation` | financial_operations | insurance_reporting.commission_report | periodic |
| `documentation_completeness` | document_intelligence | document_intelligence.document_summary | continuous |
| `licensing_validation` | insurance_licensing | insurance_licensing.list_licenses | continuous |
| `automated_test_evidence` | continuous_integration | tests | per_commit |
| `ci_verification` | continuous_integration | github_actions | per_commit |

## Evidence is referenced, never created

Every assurance source names an **existing** evidence producer — a governed runtime read (Compliance
Intelligence, Security, Data Governance, …), the hash-chain audit log, or the CI pipeline (architecture guards,
automated tests). The layer never generates an audit trail, review sign-off, or CI run; it references them and
deep-links to the authoritative surface. Governance validates that every source references evidence (no
`not_configured` assurance owner) and that keys are unique.

## Ownership boundaries (never re-implemented here)

- **Compliance reviews / approvals** — evidence produced by Compliance Intelligence + `compliance/reviews.py`.
- **Audit-log verification** — evidence produced by the hash-chain audit log (`observability`); the layer never
  writes an audit event.
- **Architecture guards / automated tests / CI** — evidence produced by the CI pipeline (`continuous_integration`
  owner); the layer references the test suite, never runs or fabricates a result.

## How the registry is used

The `controls_assurance` dashboard composes `assurance_evidence_coverage` (this registry, DERIVED), reporting
which assurance sources reference authoritative evidence. Governance validates completeness + single ownership.

See [ENTERPRISE_RISK_REGISTRY.md](ENTERPRISE_RISK_REGISTRY.md), [CONTROL_REGISTRY.md](CONTROL_REGISTRY.md),
[ENTERPRISE_RISK_MANAGEMENT.md](ENTERPRISE_RISK_MANAGEMENT.md), and
[ADR-063](adr/ADR-063-enterprise-risk-management.md).
