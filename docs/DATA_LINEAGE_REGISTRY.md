# Data Lineage Registry (Phase D.66)

`DATA_LINEAGE_REGISTRY` and `DATA_RETENTION_REGISTRY` in
`app/services/data_governance_intelligence/registry.py` are declarative catalogs of the **5 lineage domains**
and **6 retention domains**. Metadata only — they define no lineage engine or retention manager, and never
create lineage or enforce retention.

## Lineage domains (5)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| entity_lineage | governance.mdm | `list_lineage` | `record_lineage` | configured |
| record_lineage_provenance | governance.mdm | `person_lineage` | `record_lineage` | configured |
| source_system_provenance | governance.mdm | `list_lineage` | `record_lineage` | configured |
| automated_column_lineage | not_configured | n/a | n/a | **not_configured** |
| data_sharing_agreements | not_configured | n/a | n/a | **not_configured** |

Lineage is **source-system provenance** owned by Governance MDM (`list_lineage` / `person_lineage`).
Firm-wide provenance-row counts have no aggregate reader, so firm-level lineage is expressed as **domain
coverage** (configured lineage domains vs total), while actual provenance is composed **at record scope**
(Client 360 / Household 360). **A lineage record is not a complete lineage, and lineage is never inferred.**
Automated column-level lineage and data-sharing contracts have no owner (`not_configured`).

## Retention domains (6)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| retention_assignments | governance.retention | `list_retention_assignments` | `create_retention_assignment` | configured |
| legal_holds | governance.retention | `list_legal_holds` | `place_legal_hold` | configured |
| deletion_requests | governance.retention | `list_deletion_requests` | `execute_deletion` | configured |
| retention_review_status | governance.retention | `metrics` | `review_due_retention` | configured |
| retention_policy_catalog | not_configured | n/a | n/a | **not_configured** |
| data_privacy_impact_assessments | not_configured | n/a | n/a | **not_configured** |

Retention is owned by Governance Retention. The layer reports counts of assignments, active legal holds, and
pending deletion reviews — **it never executes a deletion or enforces a retention decision** (coverage is not
an enforced retention decision). A retention-policy catalog beyond the Document Platform and DPIA have no owner
(`not_configured`).

## References
- `app/services/data_governance_intelligence/registry.py` (`DATA_LINEAGE_REGISTRY`, `DATA_RETENTION_REGISTRY`,
  `_e`)
- `docs/ENTERPRISE_DATA_GOVERNANCE.md`, `docs/DATA_GOVERNANCE.md`, ADR-071
