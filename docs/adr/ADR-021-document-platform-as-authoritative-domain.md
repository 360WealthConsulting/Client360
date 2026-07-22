# ADR-021 — Document Management as the authoritative artifact domain

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Documents); Compliance Architecture (records/retention);
Business Operations Owner (Michael Shelton — document operational requirements).

## Context
A minimal documents domain already existed (a person-anchored `documents` table, a basic
`documents.py` service, `/documents` routes, `document.read/write` capabilities, plus
`microsoft_documents` SharePoint/OneDrive references and a client-portal `document_versions`
table). It lacked the enterprise capabilities the firm needs: classification, folders, immutable
version history, multi-domain relationships, retention, and a lifecycle. The requirement was to
build a full Document Management / Knowledge Repository platform **without** replacing or
duplicating existing documents, and to make Documents the single authoritative artifact domain
that every other domain references.

## Decision
Make **Documents the authoritative source domain**; every other domain **references** documents
and **never owns** them, and no file or metadata is duplicated.
- **Extend, do not replace.** The existing `documents` table is extended additively
  (classification, status/lifecycle, folder/retention refs, effective/expiration dates,
  archive/delete timestamps, storage provider/URI, OCR/preview/signature/encryption status, tags,
  notes, current_version; `person_id` relaxed to nullable for firm/internal documents). The
  existing client-portal `document_versions` table is **extended** (major/minor/current/author/
  approval) rather than recreated. The legacy `/documents` routes + `document.read/write` remain.
- New tables: `document_folders`, `document_relationships` (polymorphic multi-domain links),
  `document_retention_policies`, `document_events` (lifecycle log). Documents may relate to
  person/household/organization/opportunity/campaign/referral_source/annual_review/
  business_owner_plan/compliance_review/advisor_work/timeline_event — many relationships allowed;
  relationships never own the document.
- **Lifecycle** (draft → active → review → approved → superseded → archived; soft-delete +
  restore) is a deterministic allowed-transition map recorded in `document_events`.
- **Versioning** is immutable history: new versions supersede; a historical version can be
  restored to current without rewriting history.
- **Capabilities:** a new `documents.*` family (view/edit/delete/version/approve/archive/restore/
  export/manage_retention). Record scope is always enforced in-service (the `/document-library`
  prefix matches no middleware rule).
- **Microsoft 365** is referenced via `storage_provider=sharepoint/onedrive` + `storage_uri`
  (and the existing `microsoft_documents`); no storage provider is duplicated.
- **Timeline** receives approved lifecycle events only (uploaded/approved/archived/
  version_created/restored) for client-anchored documents via the shared writer — never a
  metadata edit; firm/internal documents (no client anchor) are recorded in `document_events`
  only. **Advisor Work / Compliance may reference** documents; **Annual Review / Business Owner
  Planning / Opportunity / Campaign / Referral** get **read-only** visibility via
  `documents_for_entity`. **Analytics consumes** a document-count statistic; Documents never
  depend on Analytics.

## Alternatives considered
1. **Replace the existing `documents` table / build a parallel `artifacts` table.** Rejected:
   would strand 13 existing FK references and the portal's document_versions, duplicate storage,
   and violate "preserve existing documents / extend rather than replace."
2. **Store document links as FK columns on each consuming domain** (opportunity.document_id,
   etc.). Rejected: fragments the many-to-many relationship and duplicates linkage; a single
   polymorphic `document_relationships` keeps Documents authoritative.

## Reasons for the decision
Extending the existing table preserves every current document and reference while adding the full
platform; a polymorphic relationship table plus the domains' own document FKs keep Documents the
single owner; lifecycle/versioning/retention give records governance; and the D.5 golden and every
ADR are preserved.

## Consequences
### Positive consequences
- One authoritative document repository; no duplicated files/metadata; existing documents and FK
  references preserved.
- Full lifecycle, immutable versioning, retention, and multi-domain relationships.
- Consumers get read-only visibility; Documents own nothing of theirs.

### Negative consequences and tradeoffs
- The extended `documents` table now serves both the legacy service and the platform (one domain,
  two service modules) — coordinated but larger.
- `document_versions` carries both portal columns and platform columns (a documented dual-purpose
  table).
- Firm/internal documents (no client anchor) are visible to `documents.view` holders — a broad
  grant appropriate for operational/marketing/internal artifacts.

## Enforcement
- `app/services/document_platform/{service,versions,relationships}.py`; routes
  `app/routes/document_library.py` (in-route `documents.*` gating). Migration `n4e5f6a7b8c9`
  (extends `documents` + `document_versions`, adds 4 tables, seeds retention + 9 capabilities);
  declared schema `app/database/document_platform_tables.py` (registered).
- Scope enforced in-service (`_visible` / `_scope_clause`). Consumer visibility via
  `documents_for_entity`. D.5 golden untouched. Tests: `tests/test_document_platform.py`;
  manifest/platform-architecture/route guards updated.

## Exceptions
None currently approved.

## Revisit conditions
Full-text search / OCR pipelines, e-signature integration, an external DMS/warehouse export, or
automated retention enforcement (a scheduler acting on expiration_date) would each warrant a new or
superseding ADR.

## References
- `app/services/document_platform/`, `app/routes/document_library.py`,
  `app/database/document_platform_tables.py`
- migration `migrations/versions/n4e5f6a7b8c9_document_platform.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_document_platform.py`; relates to ADR-001, ADR-002, ADR-005, ADR-009, ADR-013, ADR-015
