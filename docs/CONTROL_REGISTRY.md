# Control Registry (Phase D.58)

The **control registry** (`CONTROL_REGISTRY` in `app/services/enterprise_risk/registry.py`) is the declarative
catalog of the firm's internal-control families and, for each, the **authoritative owner** plus its evidence /
monitoring / test / approval / remediation owners. It is metadata only: the Enterprise Risk layer owns no
control records and never asserts control effectiveness.

## Control families

Each family declares `control_family`, `control_objective`, `authoritative_owner` (or `not_configured`),
`evidence_owner`, `monitoring_owner`, **`test_owner` (always `not_configured`)**, `approval_owner`,
`remediation_owner`, `runtime_gate`, `capabilities`, `deep_links`, and `config_status`.

| Control | Family | Authoritative owner | Config |
| --- | --- | --- | --- |
| `access_control` | access | object_security | configured |
| `authentication_mfa` | authentication | security_operations | configured |
| `authorization_sod` | authorization | policy | configured |
| `data_validation` | data | data_governance | configured |
| `record_retention` | records | document_intelligence | configured |
| `document_completeness` | documentation | document_intelligence | configured |
| `workflow_approval` | workflow | automation_orchestration | configured |
| `supervisory_review` | supervisory | compliance_intelligence | configured |
| `licensing_registration` | licensing | insurance_licensing | configured |
| `suitability` | suitability | compliance_intelligence | configured |
| `replacement_1035` | suitability | compliance_intelligence | configured |
| `communications_supervision` | supervisory | compliance_intelligence | configured |
| `vendor_oversight` | vendor | vendor_management | configured |
| `backup_recovery` | resilience | business_continuity | configured |
| `incident_response` | incident | security.incidents | configured |
| `financial_authorization` | financial | **not_configured** | **not_configured** |
| `commission_reconciliation` | financial | insurance_commissions | configured |
| `change_management` | change | **not_configured** | **not_configured** |
| `runtime_governance` | runtime | runtime | configured |
| `policy_enforcement` | policy | policy | configured |

## Control testing is not_configured everywhere (honest)

There is **no control-testing / control-effectiveness owner in the platform** (the D.58 audit confirmed zero
control-testing engines). Rather than invent control effectiveness, **every control family's `test_owner` is
`not_configured`**, and governance actively rejects any control that declares a non-`not_configured` test owner
(`fabricated_control_test_owner`). The `control_coverage` panel reports which families have an authoritative
owner, which are `not_configured`, and that control testing is `not_configured` platform-wide. The layer never
claims a control is tested or effective.

**Financial authorization** and **change management** likewise have no authoritative owner and are declared
`not_configured` control families (reported honestly, never fabricated).

## Ownership boundaries (never re-implemented here)

- **Access / authentication / authorization** are owned by `object_security` / Security Operations / the
  Policy engine. The layer never grants access or changes a policy.
- **Supervisory review / suitability / communications supervision** are owned by Compliance Intelligence +
  `compliance/reviews.py`. The layer never records a review decision.
- **Commission reconciliation** is owned by `insurance_commissions` + `insurance_reporting`. The layer never
  reconciles or pays a commission.

## How the registry is used

The `controls_assurance` dashboard composes `control_coverage` (this registry, DERIVED) +
`assurance_evidence_coverage` + `documentation_gaps`. Governance validates that every family declares its
fields, that every **configured** family names an authoritative owner, that every `test_owner` is
`not_configured`, and that keys are unique.

See [ENTERPRISE_RISK_REGISTRY.md](ENTERPRISE_RISK_REGISTRY.md), [ASSURANCE_REGISTRY.md](ASSURANCE_REGISTRY.md),
[ENTERPRISE_RISK_MANAGEMENT.md](ENTERPRISE_RISK_MANAGEMENT.md), and
[ADR-063](adr/ADR-063-enterprise-risk-management.md).
