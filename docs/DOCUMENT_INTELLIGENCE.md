# Document Intelligence (Phase D.50)

The **Document Intelligence** layer (`app/services/document_intelligence/`) is a governed, **read-only
composition** that gives records and operations leadership one view of document inventory, retention,
archive, lifecycle, missing documentation, and completeness — **without** building a second DMS, OCR engine,
indexing/search engine, archive, document database, metadata store, or records repository. Every number is
composed on read from an **authoritative owner**; the layer owns no persistence, runs no OCR, builds no
index, and never mutates, archives, deletes, re-classifies, or alters retention. **Panels carry counts +
status only — never document content or client-sensitive text.**

## What it composes (and never duplicates)

| Concern | Authoritative owner (composed) |
| --- | --- |
| Documents + metadata + folders + lifecycle | `app/services/document_platform/` (D.16) — `list_documents`, `documents_for_entity`, `list_folders`, `_TRANSITIONS` |
| Retention **policies** | `app/services/document_platform/service.py` — `list_retention_policies` |
| Retention assignments / legal holds / disposition | `app/services/governance/retention.py` (D.23) — `list_retention_assignments`, `metrics`, `list_deletion_requests` |
| Missing documentation / gaps | `app/services/compliance_intelligence/` (D.47) — `supervisory_dashboard` (normalizes the exception engine) |
| OCR / preview status | Document Platform metadata (`ocr_status`) — **reported, never run** |

See [RECORDS_LIFECYCLE.md](RECORDS_LIFECYCLE.md) for the lifecycle + document classes,
[RETENTION_REGISTRY.md](RETENTION_REGISTRY.md) for the retention policies, and
[DOCUMENT_GOVERNANCE.md](DOCUMENT_GOVERNANCE.md) for the enforced invariants.

## Modules

- `registry.py` — the declarative catalogs: `DOCUMENT_REGISTRY` (10 document classes), `RETENTION_REGISTRY`
  (6 policies), `PANEL_REGISTRY` (18 panels), `INTELLIGENCE_DASHBOARDS` (6 dashboards).
- `model.py` — `PanelResult` + `IntelligenceDashboard`. A panel is emitted only if `is_explainable`
  (explanation + source + deep link).
- `panels.py` — the per-panel compute functions. Read-only, fail-closed, **self-restricting** (a principal
  lacking `documents.view` gets a `restricted` panel, never its value). Counts + status only.
- `service.py` — the engine: `compose_dashboard`, `list_dashboards`, `get_panel`, `document_summary`,
  `client_documents`, `household_documents`.
- `gate.py` — runtime gates (`document_intelligence.enabled`, `retention.enabled`, `lifecycle.enabled`) +
  policy composition. No raw environment gating.
- `stats.py` / `metrics.py` — low-cardinality in-process counters, registered into the **single** Analytics
  Registry (`analytics.metrics`). No second metrics registry; never document contents.
- `diagnostics.py` — internal-only observability (`observability.audit`).
- `governance.py` — read-only invariant checker (never raises), including second-OCR/second-index tells.

## Dashboards

`document_inventory`, `retention`, `archive`, `lifecycle`, `missing_documentation`, `document_completeness`.
Each carries a generated timestamp, governing services, source inventory, explainable panels, and deep links
to the authoritative document surface. Dashboards are gated by `documents.view`; each panel additionally
self-restricts to `documents.view`.

## Surfaces

- **HTTP** (`app/routes/document_intelligence.py`, gated by `documents.view`; diagnostics by
  `observability.audit`): `/document-intelligence` (HTML), `/api/v1/document-intelligence/dashboards`,
  `/dashboard/{key}`, `/summary`, `/registry`, `/panel/{key}`, `/metrics`, `/document-intelligence/diagnostics`.
- **Advisor Workspace** — the Document Intelligence panel (`document_summary`).
- **Client 360 / Household 360** — the `document_intelligence` section (`client_documents` /
  `household_documents`, book-scoped Document Platform entity rollups; household dedupes by document id).
- **Executive Dashboard** — a `document_intelligence` dashboard (composed from existing D.48 widgets; no new
  widget), navigation deep-linking to `/document-intelligence`.
- **AI Assist** — summarizes document counts / open gaps only; it never alters metadata, archives, deletes,
  modifies retention, or changes document ownership.

## Invariants

No new persistence, no new metric, no new capability, no migration (single Alembic head unchanged). No
mutation, no OCR, no index, no outbox publication, no audit write, no second store. Every document count
comes from the Document Platform; every dashboard panel is explainable and deep-links to its authoritative
surface. Enforced by `app/services/document_intelligence/governance.py` and
`tests/test_document_intelligence.py`. See [ADR-055](adr/ADR-055-document-intelligence.md).
