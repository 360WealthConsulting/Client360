# Stewardship Registry (Phase D.52)

The **stewardship registry** (`STEWARDSHIP_REGISTRY` in `app/services/data_governance/registry.py`) is the
declarative catalog of the firm's data-stewardship responsibilities and, for each, the **authoritative
owners** accountable for the data. It is metadata only: the Data Governance layer owns no stewardship
assignments and approves nothing — it references the owners and explains the result with a deep link.

## Stewardship roles

Each role declares its `business_owner` (accountable steward), `technical_owner` (the authoritative technical
owner service), `validation_owner` (the authoritative validation/quality owner), `approval_owner`, and
`runtime_gate`.

| Role | Business owner | Technical owner | Validation owner | Approval owner |
| --- | --- | --- | --- | --- |
| `client_data` | advisor | people | governance.quality | governance.review |
| `household_data` | advisor | household_derivation | governance.quality | governance.review |
| `tax_data` | tax_preparer | tax_domain | governance.quality | governance.review |
| `investment_data` | advisor | portfolio | governance.quality | governance.review |
| `insurance_data` | advisor | insurance | governance.quality | governance.review |
| `benefits_data` | advisor | benefits_domain | governance.quality | governance.review |
| `document_metadata` | operations | document_platform | governance.quality | governance.review |
| `compliance_records` | compliance | compliance | governance.quality | governance.review |

## Ownership boundaries (never re-implemented here)

- **Steward assignment** is owned by `governance.catalog` — data domains carry a `steward_user_id`
  (`create_domain(..., steward_user_id=...)`). The registry catalogs the stewardship *responsibilities*; the
  layer never assigns a steward.
- **Validation** is owned by `governance.quality` (`list_findings`, `run_check` — writes never called). The
  registry names the validation owner; the layer composes the read-only findings + metrics.
- **Approval** is owned by `governance.review` (the governance approval capability). The registry names the
  approval owner; the layer never approves stewardship or a disposition.
- **The entity of record** for each role is owned by its technical owner service (people, portfolio, tax
  domain, …); the layer references them.

## How the registry is used

The stewardship + ownership dashboards compose `registered_stewardship` and `stewardship_coverage` (from this
registry) alongside `domain_stewards` (governance.catalog domains with an assigned steward) and
`remediation_cases` (governance retention cases). Governance validates that every role declares all five
fields (business / technical / validation / approval owner + runtime gate), that keys are unique, and that
every governed entity's stewardship owner is a registered role.

See [MASTER_DATA_REGISTRY.md](MASTER_DATA_REGISTRY.md), [DATA_GOVERNANCE.md](DATA_GOVERNANCE.md), and
[ADR-057](adr/ADR-057-data-governance.md).
