# Document Intelligence Governance (Phase D.50)

`app/services/document_intelligence/governance.py` is a read-only checker that verifies the Document
Intelligence layer stays a **composition** over the authoritative document systems and never becomes a
second DMS, OCR engine, indexing/search engine, archive, document database, metadata store, or records
repository. It returns `{ok, issue_count, findings}` and **never raises** into normal use.
`validate_document_intelligence()` is surfaced through the internal diagnostics endpoint
(`/document-intelligence/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No second DMS / document mutation.** No module calls a Document Platform / Governance **mutation** —
   `create_document(`, `update_document(`, `set_status(`, `.archive(`, `soft_delete(`, `.restore(`,
   `apply_retention(`, `create_retention_policy(`, `execute_deletion(`, `place_legal_hold(`,
   `create_deletion_request(`, `link_entity(`. The layer composes **reads** only.
3. **No second OCR / index engine.** No module contains an OCR/index tell — `tesseract`, `pytesseract`,
   `extract_text(`, `to_tsvector`, `pdfminer`, `pypdf`, `textract`. The OCR-status panel *reports* the
   Document Platform's own `ocr_status`; it runs no OCR and builds no index.
4. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
5. **Registry completeness + single ownership.** Every document class declares owner + storage source +
   metadata source + classification + retention policy + lifecycle + runtime gate + deep links, and points at
   a registered retention policy; every retention policy declares owner + period + archive owner +
   disposition + governing regulation + runtime gate; every dashboard declares owner + audience + runtime
   gate + navigation + panels + required capabilities + governing services, and references only registered
   panels; every panel declares owner + source + deep link + explainability + permission; all registry keys
   are unique.
6. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in
   both `model.py` and `panels.py`; a non-explainable panel is never emitted.
7. **No raw environment gating.** Gates flow through the Runtime Engine (`runtime.consumption.feature_enabled`)
   and policy through the Policy Engine — never `os.getenv` / `os.environ`.

## No document content, ever

Panels and summaries carry **counts + status only** — never document contents, file names, or any
client-sensitive text. Diagnostics and analytics counters are low-cardinality aggregates about the layer
itself. This is a structural invariant of the model (`PanelResult` values are counts/status/rollups) and of
the compose layer (it reads document *metadata* — classification, status, dates, `ocr_status` — never the
document body).

## Authorization & least privilege

- Document routes are gated by `documents.view`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities` (`documents.view`);
  otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to `documents.view`: a principal lacking it receives a `restricted` panel
  with `value = None` — never leaked.
- All composed reads inherit the record scope + capability checks of their authoritative owner (the Document
  Platform's `_visible` / `_scope_clause`, Governance retention, Compliance Intelligence's supervisor gate).

## AI Assist boundary

AI Assist may **summarize** document counts and open documentation gaps (fact class `DERIVED`, counts only,
deep links only). It **never** alters metadata, archives documents, deletes documents, modifies retention,
or changes document ownership — every fact comes from a composed section/summary.

## Enforcement

`tests/test_document_intelligence.py` exercises the registries, explainable composition, authorization
(`None` + restricted), gate/policy behavior, the analytics-counter reuse, diagnostics, the routes
(registered + capability-gated), AI summarize-only, and the architecture invariants (no second DMS/OCR/index,
no duplicate metadata, no mutation, document reads composed from `document_platform`, every dashboard
deep-links, every lifecycle calc names an authoritative owner). Route count, section registries, ADR count,
and the single migration head are guarded by `tests/test_platform_architecture.py`,
`tests/test_client360_workspace.py`, `tests/test_household360_workspace.py`,
`tests/test_architecture_decision_records.py`, and the manifest.

See [DOCUMENT_INTELLIGENCE.md](DOCUMENT_INTELLIGENCE.md), [RECORDS_LIFECYCLE.md](RECORDS_LIFECYCLE.md),
[RETENTION_REGISTRY.md](RETENTION_REGISTRY.md), and [ADR-055](adr/ADR-055-document-intelligence.md).
