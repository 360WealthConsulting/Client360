# Knowledge Domain & Source Registry (Phase D.62)

Two declarative catalogs in `app/services/knowledge_management/registry.py` describe the firm's knowledge
domains and the knowledge sources the layer composes. Both are metadata only — the layer owns no knowledge
store, no articles, and no search index.

## Knowledge domains (`KNOWLEDGE_DOMAIN_REGISTRY`)

Each domain declares `owner` (or `not_configured`), `runtime_gate`, `capabilities`, `deep_links`, and
`config_status`.

| Domain | Authoritative owner | Config |
| --- | --- | --- |
| `client_documentation` | document_platform | configured |
| `compliance_documentation` | document_platform | configured |
| `tax_documentation` | document_platform | configured |
| `operational_documentation` | document_platform | configured |
| `legal_documentation` | document_platform | configured |
| `internal_documentation` | document_platform | configured |
| `knowledge_base` | **not_configured** | **not_configured** |
| `institutional_memory` | **not_configured** | **not_configured** |

## Knowledge sources (`KNOWLEDGE_SOURCE_REGISTRY`)

| Source | Authoritative owner | Config |
| --- | --- | --- |
| `document_platform` | document_platform | configured |
| `document_intelligence` | document_intelligence | configured |
| `governance_retention` | governance.retention | configured |
| `rule_catalog` | compliance_rule_catalog | configured |
| `wiki` | **not_configured** | **not_configured** |
| `confluence` | **not_configured** | **not_configured** |
| `search_index` | **not_configured** | **not_configured** |

## The not_configured domains / sources (reported honestly)

**A knowledge base, institutional memory, a wiki, Confluence, and a dedicated full-text / vector search index**
have **no authoritative owner in the platform today** (the D.62 audit confirmed no KB / wiki / article /
Confluence / search engine). Rather than fabricate a knowledge article or a search index, those entries are
declared `not_configured` and reported honestly in the `knowledge_domain_inventory` /
`knowledge_source_inventory` / `knowledge_gaps` panels. The documentation domains are all owned by the Document
Platform (the DMS of record).

## Runtime-gate collision avoided

This registry's `runtime_gate` for the knowledge domains + sources is **`knowledge_management.enabled`** — a
distinct gate from the D.45 Enterprise Knowledge **Graph** layer's `knowledge.enabled`, so the two unrelated
layers are never coupled to one flag.

## How the registry is used

The `knowledge_overview` + `knowledge_gaps` dashboards compose `knowledge_domain_inventory` (DERIVED),
`knowledge_source_inventory` (DERIVED), `knowledge_health` (DERIVED), and `knowledge_gaps` (DERIVED).
Governance validates completeness + single ownership + honest not_configured (unique keys).

See [ENTERPRISE_KNOWLEDGE_MANAGEMENT.md](ENTERPRISE_KNOWLEDGE_MANAGEMENT.md), [SOP_GOVERNANCE.md](SOP_GOVERNANCE.md),
[DOCUMENTATION_OWNERSHIP.md](DOCUMENTATION_OWNERSHIP.md), and [ADR-067](adr/ADR-067-knowledge-management.md).
