# ADR-055 — Enterprise Document Intelligence & Records Lifecycle: A Read-Only Composition, Not a Second DMS/OCR/Index/Archive

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Firm Operations / Records & Information Governance); Reliability /
Operations; Security / Authorization (RBAC ownership); Compliance; Business Operations Owner
(Michael Shelton).

## Context
The mandatory D.50 audit found the platform already owns every document, metadata, storage, lifecycle,
retention, and disposition capability:

* **Document Platform** (`app/services/document_platform/`, D.16) — the single authoritative repository of
  documents + metadata + folders + versions + relationships + the lifecycle state machine (`_TRANSITIONS`:
  draft → active → review → approved → superseded → archived) + the retention-policy store
  (`document_retention_policies`). Reads are principal-aware and record-scoped: `documents_for_entity`,
  `get_document`, `list_documents`, `list_folders`, `list_retention_policies`. Its package docstring is
  explicit: *"the Documents domain is the authoritative repository… files/metadata are never duplicated."*
* **Governance retention** (`app/services/governance/retention.py`, D.23) — the records retention /
  legal-hold / disposition owner: `list_retention_assignments`, `metrics(principal)`,
  `list_deletion_requests`, `review_due_retention`, legal holds. References `document_retention_policies`,
  never a parallel policy table; **never hard-deletes**.
* **Compliance Intelligence** (D.47) — normalizes the authoritative **exception engine** into documentation
  gaps (`missing_document`, `unsigned_disclosure`, `missing_beneficiary`) via `supervisory_dashboard`.

The audit also confirmed two genuine **gaps**: there is **no OCR / text-extraction / full-text index**
anywhere (only inert `ocr_status`/`preview_status` stub columns), and document search is a per-list
`original_name ILIKE` filter, not an index. Building a second DMS, OCR engine, index, archive, metadata
store, or records repository would violate the "no second system" invariant and duplicate governed
infrastructure.

## Decision
Phase D.50 adds a **governed, read-only document-intelligence composition layer**
(`app/services/document_intelligence/`) with NO new metrics, NO persistence, NO OCR, NO index, and NO
mutation:

1. Three declarative **registries** (`registry.py`): `DOCUMENT_REGISTRY` (10 document classes — owner,
   storage source, metadata source, classification, retention policy, lifecycle, runtime gate, refresh
   policy, deep links), `RETENTION_REGISTRY` (6 policies — owner, retention period, archive owner,
   disposition policy, governing regulation, runtime gate), plus `PANEL_REGISTRY` (18 panels) and
   `INTELLIGENCE_DASHBOARDS` (6 dashboards). Every document class names `document_platform` as owner.
2. Normalized read-models (`model.py`): `PanelResult` + `IntelligenceDashboard`, each explainable
   (explanation + source + deep link, a hard emit gate) and reference-only; **counts + status only, never
   document content**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative
   owner (Document Platform inventory/lifecycle/OCR-status, Governance retention, Compliance Intelligence
   gaps). The OCR-status panel *reports* the Document Platform's own `ocr_status` — it runs no OCR.
   Fail-closed; every panel self-restricts to `documents.view`.
4. The **document-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `document_summary`, plus `client_documents` / `household_documents` (book-scoped Document Platform entity
   rollups). Every dashboard carries generated timestamp, governing services, source inventory, explainable
   panels, and deep links. Dashboard-level authorization (`documents.view`).
5. **Runtime gates** (`document_intelligence.enabled` + `retention.enabled` + `lifecycle.enabled`),
   **policy composition**, **analytics reuse** (four operational counters registered into the ONE Analytics
   Registry — no second registry), internal **diagnostics** (`observability.audit`), and a read-only
   **governance** checker that forbids mutation, persistence, any Document Platform / Governance mutation
   call, and any second-OCR/second-index tell. AI Assist may summarize document counts / gaps but never
   alters metadata, archives, deletes, modifies retention, or changes document ownership.

No migration, no new table, no new capability (reuses `documents.view` + `observability.audit`), no new
metric, no new outbox contract. Single Alembic head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second DMS / OCR engine / search index / archive / metadata store.** Rejected: the Document Platform
  is the authoritative repository + metadata + lifecycle + retention owner and Governance retention owns
  disposition; D.50 composes them. Governance forbids a second store, OCR/index tells, and copied metadata.
- **Filling the OCR/index gap now.** Rejected for this phase: D.50 is a read-only composition. It *reports*
  the Document Platform's `ocr_status` honestly (mostly unprocessed) rather than inventing an OCR pipeline;
  a real OCR/index would be added to the Document Platform (the owner), then surfaced here.
- **Persisting composed document dashboards / a records warehouse.** Rejected: dashboards are a
  deterministic function of the authoritative data at read time; a store would be a records warehouse to
  reconcile.

## Reasons for the decision
Records leadership needs one document/records view; the Document Platform, Governance retention, and
Compliance Intelligence already own every number with the correct scoping. A read-only composition gives
that view with full explainability (source + deep link) while every document stays owned by the Document
Platform, every retention rule by the Document Platform + Governance, and every gap by the exception engine.
Deep links (never inline mutation) route the user to the authoritative document surface to act. Emitting
counts + status only keeps document content and client-sensitive text out of the layer entirely.

## Rationale for avoiding a second document platform
A second DMS / OCR / index / archive would require copied documents + metadata, a parallel lifecycle and
retention model, and its own access + storage model — duplicating governed infrastructure and creating
reconciliation + drift + data-exposure risk, with no benefit the composition does not already provide.
Composing over the single Document Platform keeps one source of truth for every document, one lifecycle
state machine, one retention-policy store, and zero copied content.

## Consequences

### Positive consequences
- One firm-wide document/records surface with no second DMS, OCR engine, index, archive, or metadata store.
- Record scope + capability are inherited from the Document Platform reads; a non-`documents.view` principal
  sees restricted panels, never values, and never any document content.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Document Intelligence panel + Client 360 / Household 360 document sections + an Executive
  Document Intelligence dashboard (reusing existing widgets) + AI summarize-only, all from one layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost;
  inventory rollups issue per-classification/status counts against the Document Platform.
- The OCR-status and completeness panels are bounded by the Document Platform's own metadata (OCR is inert
  today) — the layer reports that honestly rather than fabricating processing.
- Some panels sample a bounded page (e.g. expiring documents) where no aggregate read exists — flagged in
  the panel value (`sampled`), never presented as a full count.

## Enforcement
`tests/test_document_intelligence.py` (three registries + single ownership; explainable dashboard
composition; authorization — unauthorized → None, unentitled panel restricted never valued; runtime + policy
gates; the firm summary + client/household rollups; analytics reuse — the 4 counters in the ONE registry;
diagnostics; routes registered + capability-gated; AI summarize-only; and the architecture invariants — no
second DMS/OCR/index, no duplicate metadata, no mutation, document reads composed from `document_platform`,
every dashboard deep-links, every lifecycle calc names an authoritative owner).
`app/services/document_intelligence/governance.py` enforces the invariants at runtime (including
second-OCR/second-index tells). Route count, section registries, and migration head are guarded by
`tests/test_platform_architecture.py` + `tests/test_client360_workspace.py` +
`tests/test_household360_workspace.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate (Document Platform inventory counts, Governance retention metrics)
are exposed only within dashboards whose required capability (`documents.view`) the principal holds; each
panel additionally self-restricts to `documents.view`. Missing-documentation panels compose Compliance
Intelligence, which itself self-gates to supervisors — a non-supervisor sees an unavailable panel, never
leaked gap detail.

## Revisit conditions
Revisit when a real OCR / full-text index is required (add it to the Document Platform — the owner — then
surface it here), when durable document search is needed (compose over the Document Platform's index, never
a second index), or if a materialized records read-model is ever justified (it would be a governed
projection, never a second document warehouse).

## References
- `app/services/document_intelligence/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/document_intelligence.py`; Client 360 section in
  `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; Document Intelligence panel in `app/services/workspace/service.py`;
  Executive Document Intelligence dashboard in `app/services/executive_intelligence/registry.py`; AI
  grounding in `app/services/ai_assist/context.py`; analytics counters in
  `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/document_platform/*` (`service.py`, `relationships.py`),
  `app/services/governance/retention.py`, `app/services/compliance_intelligence/*`
- `docs/DOCUMENT_INTELLIGENCE.md`, `docs/RECORDS_LIFECYCLE.md`, `docs/RETENTION_REGISTRY.md`,
  `docs/DOCUMENT_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_document_intelligence.py`; relates to ADR-016, ADR-023, ADR-025, ADR-046 through ADR-054
