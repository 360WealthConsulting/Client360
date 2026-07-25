# Documentation Ownership Registry (Phase D.62)

The **documentation owner registry** (`DOCUMENTATION_OWNER_REGISTRY` in
`app/services/knowledge_management/registry.py`) is the declarative catalog of the firm's documentation
ownership / retention / lifecycle owners the layer composes. It is metadata only — the layer owns no document
ownership state and never reassigns or edits a document.

## Documentation owners

Each entry declares `owner`, `runtime_gate`, `capabilities`, `deep_links`, and `config_status`. All five are
configured (each references a real authoritative owner).

| Owner class | Authoritative owner | Composed read |
| --- | --- | --- |
| `document_authors` | document_platform | `list_documents` (`created_by_user_id` / `owner_user_id`) |
| `retention_owners` | governance.retention | `governance.retention.metrics` |
| `classification_owners` | document_platform | `list_documents(classification=…)` |
| `lifecycle_owners` | document_platform | `list_documents(status=…)` |
| `unassigned_documentation` | document_platform | `list_documents` (rows with no author) |

## Ownership = counts only, never an author identity

Document ownership is tracked by the Document Platform via `owner_user_id` / `created_by_user_id` columns. The
D.62 `ownership_coverage` / `orphaned_documentation` panels compose `list_documents` and count documents *with*
vs *without* a recorded author — **counts only, never an author identity**. Because `list_documents` is
paginated, ownership coverage is computed over the first document window (up to 200) and flagged `sampled` when
the corpus exceeds the window — honest, never a fabricated coverage figure.

## Version awareness = the immutable-version store

Document versioning is owned by the Document Platform's **immutable-version store** (`document_versions`). The
D.62 `version_awareness` / `superseded_documents` panels report superseded (versioned) document counts — **the
layer never creates, approves, or restores a version.**

## Retention = Data Governance

Records retention / legal holds / disposition are owned by Data Governance retention (D.23). The
`retention_coverage` panel composes `governance.retention.metrics` (active legal holds + pending disposition
reviews) — read-only, `governance.view`. The layer never places a legal hold or executes a deletion.

## How the registry is used

The `ownership_coverage` + `documentation_quality` dashboards compose `ownership_coverage` (Document Platform),
`orphaned_documentation` (Document Platform), `document_inventory_by_classification` (Document Platform),
`version_awareness`, and `superseded_documents`. Governance validates completeness + single ownership (unique
keys).

See [ENTERPRISE_KNOWLEDGE_MANAGEMENT.md](ENTERPRISE_KNOWLEDGE_MANAGEMENT.md),
[KNOWLEDGE_DOMAIN_REGISTRY.md](KNOWLEDGE_DOMAIN_REGISTRY.md), [SOP_GOVERNANCE.md](SOP_GOVERNANCE.md), and
[ADR-067](adr/ADR-067-knowledge-management.md).
