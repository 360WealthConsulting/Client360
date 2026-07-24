# Master Data Registry (Phase D.52)

The **master data registry** (`MASTER_DATA_REGISTRY` in `app/services/data_governance/registry.py`) is the
declarative catalog of the firm's governed entities and, for each, the **authoritative owners** it is
composed from. It is metadata only: the Data Governance layer owns no master data, no identities, and no
metadata — it references the owners and explains the result with a deep link.

## Governed entities

Each entity declares its `authoritative_owner` (the entity of record), `identity_owner` (the authoritative
identity/merge owner), `metadata_owner`, `stewardship_owner` (a key into the
[stewardship registry](STEWARDSHIP_REGISTRY.md)), `lineage_owner`, `runtime_gate`, and `deep_links`.

| Entity | Authoritative owner | Identity owner | Lineage owner |
| --- | --- | --- | --- |
| `person` | people | person_merge | governance.mdm.person_lineage |
| `household` | household_derivation | household_derivation | governance.mdm |
| `organization` | organization_service | organization_service | governance.mdm |
| `advisor` | identity | identity | governance.mdm |
| `client` | client360 | person_merge | governance.mdm |
| `prospect` | client360 | person_merge | governance.mdm |
| `trust` | relationships | relationships | governance.mdm.list_lineage |
| `estate` | relationships | relationships | governance.mdm.list_lineage |
| `account` | portfolio | portfolio | governance.mdm |
| `policy` | insurance | insurance | governance.mdm |
| `plan` | benefits_domain | benefits_domain | governance.mdm |
| `tax_return` | tax_domain | tax_domain | governance.mdm |
| `engagement` | tax_domain | tax_domain | governance.mdm |
| `opportunity` | opportunity | opportunity | governance.mdm |
| `document` | document_platform | document_platform | governance.mdm.list_lineage |

Every entity's `identity_owner` is the authoritative identity/merge owner; the layer never merges or alters
an identity.

## Ownership boundaries (never re-implemented here)

- **Identity + merge** is owned by `app/services/person_merge.py` (`merge_source_contacts` — the canonical
  merge) + `app/matching/promote.py`. The registry names the identity owner; the layer **never calls**
  `merge_source_contacts` / `resolve_*` — governance forbids it.
- **Metadata** is owned by `governance.catalog` (data domains + elements + rules). The registry names the
  metadata owner; the layer never creates a domain/element/rule.
- **Lineage / provenance** is owned by `governance.mdm.person_lineage` (reads `person_source_links`) and
  `list_lineage` (`governance_lineage`). The registry names the lineage owner; the layer never records
  lineage.
- **Entity of record** is owned by each domain service (people, portfolio, insurance, tax_domain,
  document_platform, …). The layer references them; it stores no master record.

## How the registry is used

The master-data + ownership + lineage dashboards compose `registered_entities`, `entity_ownership`, and
`lineage_coverage` (from this registry) alongside `data_domains` / `data_elements` (catalog) and the
duplicate/lineage/validation reads. Governance validates that every entity declares all seven owner fields,
that every entity's stewardship owner is a registered stewardship role, that keys are unique, and that the
layer contains no merge/identity/metadata **mutation** call.

See [STEWARDSHIP_REGISTRY.md](STEWARDSHIP_REGISTRY.md), [DATA_GOVERNANCE.md](DATA_GOVERNANCE.md), and
[ADR-057](adr/ADR-057-data-governance.md).
