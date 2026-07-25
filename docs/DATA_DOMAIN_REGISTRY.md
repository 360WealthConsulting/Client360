# Data Domain Registry (Phase D.66)

`DATA_DOMAIN_REGISTRY` and `DATA_QUALITY_REGISTRY` in
`app/services/data_governance_intelligence/registry.py` are declarative catalogs of the **8 data-domain
governance domains** and **5 quality domains** the firm's platform actually has. Metadata only — they define no
data catalog or quality engine. Each entry names its authoritative owner, read surface, **prohibited mutation
surface** (the mutating entry point this layer must NEVER call), evidence source, governing capability, runtime
gate, deep links, and config status.

## Data-domain governance domains (8)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| data_domains | governance.catalog | `list_domains` | `create_domain` | configured |
| data_elements | governance.catalog | `list_elements` | `create_element` | configured |
| data_quality_rules | governance.catalog | `list_rules` | `create_rule` | configured |
| survivorship_rules | governance.catalog | `list_survivorship_rules` | `create_survivorship_rule` | configured |
| mdm_merge_candidates | governance.mdm | `list_candidates` | `record_merge_decision` | configured |
| external_data_catalog | not_configured | n/a | n/a | **not_configured** |
| business_glossary | not_configured | n/a | n/a | **not_configured** |
| data_classification | not_configured | n/a | n/a | **not_configured** |

## Data-quality domains (5)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| quality_rules | governance.catalog | `list_rules` | `create_rule` | configured |
| quality_findings | governance.quality | `metrics` | `create_finding` | configured |
| critical_findings | governance.quality | `metrics` | `create_finding` | configured |
| quality_coverage | governance.catalog | `list_rules` | `run_check` | configured |
| quality_scorecards | not_configured | n/a | n/a | **not_configured** |

## The honest gaps

An external data catalog (Collibra / Alation), a business glossary / data dictionary, a data-classification
taxonomy, and data-quality scorecards / SLAs have **no authoritative owner** in the platform — declared
`not_configured`, never a fabricated catalog entry or quality score. **A registered rule is not an executed
check** — the layer reports how many rules are registered, never runs one.

## References
- `app/services/data_governance_intelligence/registry.py` (`DATA_DOMAIN_REGISTRY`, `DATA_QUALITY_REGISTRY`,
  `_e`)
- `docs/ENTERPRISE_DATA_GOVERNANCE.md`, `docs/DATA_GOVERNANCE.md`, ADR-071
