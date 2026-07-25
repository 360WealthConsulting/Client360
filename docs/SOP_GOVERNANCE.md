# SOP Category & Publication Registry (Phase D.62)

Two declarative catalogs in `app/services/knowledge_management/registry.py` describe the firm's SOP categories
and document publication statuses the layer composes. Both are metadata only — the layer owns no SOP, no
procedure, and no publication workflow, and never approves or publishes anything.

## SOP categories (`SOP_CATEGORY_REGISTRY`)

Each category declares `owner` (or `not_configured`), `runtime_gate` (`sop_governance.enabled`),
`capabilities`, `deep_links`, and `config_status`.

| Category | Authoritative owner | Config |
| --- | --- | --- |
| `operational_sops` | document_platform | configured |
| `compliance_sops` | document_platform | configured |
| `procedures` | document_platform | configured |
| `runbooks` | **not_configured** | **not_configured** |
| `playbooks` | **not_configured** | **not_configured** |
| `onboarding_sops` | **not_configured** | **not_configured** |

## SOP governance is not_configured (reported honestly)

**There is NO dedicated SOP-governance / runbook / playbook owner in the platform.** The Document Platform has
`operations` / `internal` / `compliance` classified documents (surfaced for the operational / compliance SOP
categories as document counts by classification), but there is **no SOP-approval engine, no runbook store, and
no SOP taxonomy** (the document classification vocabulary has no `sop` / `procedure` / `policy` value). Runbooks,
playbooks, and onboarding SOPs are therefore declared `not_configured` and reported honestly in the
`sop_coverage` / `runbook_coverage` panels. **The layer never fabricates an SOP, a procedure, or an SOP
approval.**

## Publication statuses (`PUBLICATION_STATUS_REGISTRY`)

Document publication is the **Document Platform deterministic lifecycle** (`draft → review → approved →
superseded → archived`). Each status is owned by `document_platform`:

| Status | Owner |
| --- | --- |
| `draft` | document_platform |
| `review` | document_platform |
| `approved` | document_platform (published) |
| `superseded` | document_platform |
| `archived` | document_platform |

The `publication_readiness` / `draft_documents` / `approved_documents` / `pending_review_documents` panels
compose `document_platform.list_documents(status=…)` counts. **The layer never publishes, approves, or
transitions a document** — it only reports the lifecycle status counts.

## How the registry is used

The `sop_governance` + `publication_readiness` dashboards compose `sop_coverage` (DERIVED), `runbook_coverage`
(not_configured), `documentation_gaps`, `publication_readiness`, `draft_documents`, `approved_documents`, and
`pending_review_documents`. Governance validates completeness + single ownership (unique keys).

See [ENTERPRISE_KNOWLEDGE_MANAGEMENT.md](ENTERPRISE_KNOWLEDGE_MANAGEMENT.md),
[KNOWLEDGE_DOMAIN_REGISTRY.md](KNOWLEDGE_DOMAIN_REGISTRY.md),
[DOCUMENTATION_OWNERSHIP.md](DOCUMENTATION_OWNERSHIP.md), and [ADR-067](adr/ADR-067-knowledge-management.md).
