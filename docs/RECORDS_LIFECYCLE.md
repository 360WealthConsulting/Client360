# Records Lifecycle (Phase D.50)

The records lifecycle in Client360 is **owned by the Document Platform** (`app/services/document_platform/`,
Phase D.16) and **composed read-only** by the Document Intelligence layer (D.50). The layer computes no new
lifecycle state; it reports the authoritative state machine's counts and deep-links to the document surface.

## The authoritative lifecycle state machine

The Document Platform defines the single lifecycle state machine (`_TRANSITIONS` in
`app/services/document_platform/service.py`) over `DOCUMENT_STATUSES`:

```
draft → {active, review}
active → {draft, review}
review → {draft, active, approved}
approved → {active, review, superseded}
superseded → {active, approved}
archived → {draft, active, review, approved, superseded}
```

Transitions are driven only by the Document Platform (`set_status`, `approve`, `archive`, `soft_delete`,
`restore`), each writing a lifecycle event and publishing a `document.status_changed` / `document.archived`
business event (producer `document.platform`). **The Document Intelligence layer never drives a transition**
— it reads the resulting states. Signing is tracked out-of-band in `signature_requests`
(`app/portal/signatures.py`); there is no `signed` document status.

## Document class registry (`DOCUMENT_REGISTRY`)

Each document class declares its `owner` (always `document_platform`), `storage_source`, `metadata_source`,
the Document Platform `classification` it maps to, its `retention_policy` (a key into the
[retention registry](RETENTION_REGISTRY.md)), `lifecycle` owner, `runtime_gate`, `refresh_policy`, and
`deep_links`.

| Document class | Classification | Retention policy | Metadata source |
| --- | --- | --- | --- |
| `tax_returns` | tax | irs | document_platform |
| `financial_plans` | client | sec | document_platform |
| `ips` | investment | sec | document_platform |
| `investment_documents` | investment | finra | document_platform |
| `insurance_documents` | insurance | state_insurance | document_platform |
| `estate_documents` | estate | internal_operations | document_platform |
| `trust_documents` | legal | internal_operations | document_platform |
| `compliance_documents` | compliance | finra | document_platform |
| `correspondence` | client | client_communications | communications |
| `signed_agreements` | legal | finra | document_platform + portal.signatures |

## Lifecycle dashboards

The lifecycle-oriented dashboards are `lifecycle`, `document_inventory`, and `document_completeness`. Each
panel:

- **`lifecycle_status`** — documents by lifecycle state, from the Document Platform state machine.
- **`pending_review`** / **`superseded_documents`** — documents in the `review` / `superseded` states.
- **`inventory_by_status`** — inventory by lifecycle status.
- **`archived_documents`** — documents in the `archived` lifecycle state (archive is a state, not a second
  archive system).
- **`ocr_status`** — the Document Platform's own `ocr_status` metadata, reported (the layer runs no OCR).
- **`expiring_documents`** — documents past / approaching their retention `expiration_date`.
- **`completeness_score`** — a deterministic, advisory indicator (inventory present vs open documentation
  gaps).

Every panel references an authoritative owner + source and deep-links to the authoritative document surface
(`/document-library`). A principal lacking `documents.view` sees a `restricted` panel, never a value.

## What records lifecycle is NOT

- **Not** a second lifecycle engine — the Document Platform owns every transition.
- **Not** a mutator — the layer never calls `set_status`/`archive`/`soft_delete`/`restore`.
- **Not** persisted — dashboards are recomputed per request from the authoritative reads.

See [DOCUMENT_INTELLIGENCE.md](DOCUMENT_INTELLIGENCE.md), [RETENTION_REGISTRY.md](RETENTION_REGISTRY.md), and
[ADR-055](adr/ADR-055-document-intelligence.md).
