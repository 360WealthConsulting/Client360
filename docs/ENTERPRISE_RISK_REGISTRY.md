# Enterprise Risk Registry (Phase D.58)

The **enterprise risk registry** (`ENTERPRISE_RISK_REGISTRY` in `app/services/enterprise_risk/registry.py`) is
the declarative catalog of the firm's risk domains and, for each, the **authoritative owner** plus its signal /
exception / incident / remediation / assurance owners. It is **metadata only** — it must never become a
persisted risk register. The layer owns no risk records; it references the owners and explains the result with
a deep link.

## Risk domains

Each domain declares `risk_category`, `authoritative_owner` (or `not_configured`), `signal_owners`,
`exception_owner`, `incident_owner`, `remediation_owner`, `assurance_owner`, `capabilities`, `runtime_gate`,
`deep_links`, and `config_status`.

| Domain | Category | Authoritative owner | Config |
| --- | --- | --- | --- |
| `regulatory_risk` | regulatory | compliance_intelligence | configured |
| `compliance_risk` | compliance | compliance_intelligence | configured |
| `operational_risk` | operational | exception_engine | configured |
| `cybersecurity_risk` | security | security.incidents | configured |
| `identity_access_risk` | security | security_operations | configured |
| `data_governance_risk` | data | data_governance | configured |
| `integration_risk` | integration | integration_hub | configured |
| `vendor_third_party_risk` | vendor | vendor_management | configured |
| `business_continuity_risk` | resilience | business_continuity | configured |
| `financial_control_risk` | financial | financial_operations | configured |
| `client_service_risk` | operational | exception_engine | configured |
| `technology_lifecycle_risk` | technology | vendor_management | configured |
| `model_ai_risk` | model | **not_configured** | **not_configured** |
| `privacy_risk` | privacy | **not_configured** | **not_configured** |
| `records_management_risk` | records | document_intelligence | configured |

## The not_configured domains (reported honestly)

**Model & AI risk** and **Privacy risk** have **no authoritative owner in the platform today** (the D.58 audit
confirmed no model-risk / AI-risk / privacy-risk register or engine). Rather than fabricate a risk rating, they
are declared `authoritative_owner = not_configured` / `config_status = not_configured` and surfaced honestly in
the `enterprise_risk_posture` panel's not_configured list. When an authoritative owner is added, the layer
composes it — never a second risk register. This mirrors the D.55 / D.56 / D.57 precedents.

## Ownership boundaries (never re-implemented here)

- **Exceptions / findings** are owned by `exception_engine.py` (the single authoritative exception owner),
  surfaced via Compliance Intelligence. The layer never raises, escalates, or resolves an exception.
- **Security incidents** are owned by `security/incidents.py`. The layer never creates, acknowledges, or
  resolves an incident.
- **Reviews / approvals** are owned by `compliance/reviews.py` + Compliance Intelligence. The layer never
  submits a review or records a decision.
- Every other signal is owned by its D.50–D.57 layer; the registry references it and deep-links to it.

## How the registry is used

The dashboards compose `risk_domain_inventory` (this registry, DERIVED) + `enterprise_risk_posture` (a DERIVED
coverage summary) + the per-domain signal panels. Governance validates that every domain declares its fields,
that every **configured** domain names an authoritative owner (a configured domain with a `not_configured`
owner is a finding), and that keys are unique.

See [CONTROL_REGISTRY.md](CONTROL_REGISTRY.md), [ASSURANCE_REGISTRY.md](ASSURANCE_REGISTRY.md),
[ENTERPRISE_RISK_MANAGEMENT.md](ENTERPRISE_RISK_MANAGEMENT.md), and
[ADR-063](adr/ADR-063-enterprise-risk-management.md).
