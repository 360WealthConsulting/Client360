# ADR-067 — Enterprise Knowledge Management, SOP Governance & Institutional Intelligence: A Read-Only Composition, Not a Second Wiki / Document Repository / Knowledge Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Knowledge Management / SOP Governance / Institutional Intelligence);
Operations; Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.62 audit inventoried every knowledge / SOP / documentation / institutional-memory owner:

* **Document Platform (D.16)** — the DMS of record. `list_documents(*, classification, status, folder_id,
  search, page, page_size)` (→ `{rows, total, page, page_size, pages}`), `get_document`, `list_folders`,
  `relationships.documents_for_entity`, `versions.list_versions` / `current_version`. Owns classification
  (`client / compliance / tax / operations / legal / internal / …`), a deterministic lifecycle (`draft →
  review → approved → superseded → archived`), **immutable versions**, and ownership (`owner_user_id` /
  `created_by_user_id`).
* **Document Intelligence (D.50)** — `document_summary` (completeness / missing / expiring), `client_documents`
  / `household_documents` (record-scoped). **Data Governance retention (D.23)** — `governance.retention.metrics`
  (legal holds, disposition reviews).
* **Genuinely absent (not_configured):** there is **no SOP / procedure / runbook / playbook owner, no
  knowledge base / wiki / article owner, no Confluence / Notion knowledge integration, and no dedicated
  full-text / vector search or indexing owner** (only an `ILIKE`-on-filename filter). The document
  classification vocabulary has **no `sop` / `procedure` / `policy` value**. There are **no `knowledge.*` /
  `sop.*` / `documentation.*` capabilities** — `documents.view` + `governance.view` express the boundary.

**A critical namespace collision:** the runtime gate `knowledge.enabled` is already owned by the D.45
Enterprise Knowledge **GRAPH** layer (a relationship composition). D.62 therefore uses a **distinct master
gate `knowledge_management.enabled`**, never reusing `knowledge.enabled`.

There was **no knowledge-management composition layer** unifying these into named, firm-wide views of SOP
coverage, documentation completeness / freshness, ownership, versioning, publication readiness, and knowledge
health. Building a second wiki, document-management platform, Confluence replacement, SharePoint,
records-management platform, search engine, AI knowledge store, or document repository would violate the "no
second system" invariant and duplicate governed infrastructure.

## Decision
Phase D.62 adds a **governed, read-only knowledge-management composition layer**
(`app/services/knowledge_management/`) with NO new capability, NO new metric, NO persistence, and NO mutation:

1. Five declarative **registries** (`registry.py`): `KNOWLEDGE_DOMAIN_REGISTRY` (8 — knowledge base +
   institutional memory not_configured), `SOP_CATEGORY_REGISTRY` (6 — runbooks / playbooks / onboarding SOPs
   not_configured), `DOCUMENTATION_OWNER_REGISTRY` (5), `KNOWLEDGE_SOURCE_REGISTRY` (7 — wiki / Confluence /
   search index not_configured), and `PUBLICATION_STATUS_REGISTRY` (5 — the Document Platform lifecycle), each
   naming owner + runtime gate + capabilities + deep links + config status. Plus `PANEL_REGISTRY` (22) and
   `KNOWLEDGE_DASHBOARDS` (8).
2. Normalized read-models (`model.py`): `PanelResult` + `KnowledgeDashboard`, each explainable (a hard emit
   gate), carrying `derived` / `config_status`; **counts, status, and coverage only, never document contents,
   confidential procedures, credentials, tokens, or client-sensitive documentation**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Document Platform via `documents.view`, Document Intelligence, Data Governance retention via
   `governance.view`); fail-closed; every panel self-restricts. SOP governance / runbooks / playbooks /
   onboarding SOPs / knowledge base / institutional memory / wiki / Confluence / search-index panels are
   emitted `available=False` with `config_status='not_configured'` — honest, never a fabricated SOP / knowledge
   / version history. The `executive_knowledge_status` panel is a **DERIVED** documentation-coverage summary
   (labeled `derived`) — never a certified SOP / approval / institutional-knowledge figure.
4. The **knowledge-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `knowledge_summary`, plus `client_documentation` / `household_documentation` — composed from ONLY the
   record-scoped Document Intelligence per-entity read (`client_documents` over the Document Platform's scoped
   `documents_for_entity`). **Internal SOPs, unrelated documentation, confidential operational procedures, and
   firm-wide documentation metrics are never exposed at client/household scope.** Dashboard-level authorization
   admits **documentation OR an executive** (`documents.view` / `analytics.executive`, via
   `require_any_capability`).
5. **Runtime gates** (`knowledge_management.enabled` [distinct from the D.45 graph's `knowledge.enabled`],
   `sop_governance.enabled`, `documentation.enabled`, `knowledge_ai_summary.enabled`) + the runtime gate of
   every composed source, **policy composition**, **analytics reuse** (four operational counters into the ONE
   Analytics Registry — no second registry), internal **diagnostics** (`observability.audit`), and a read-only
   **governance** checker that forbids mutation, persistence, any document / version / retention mutation
   (`create_document`, `approve`, `create_version`, `create_retention_assignment`, …), a second metrics
   registry, and a fabricated document / SOP / version. AI Assist may summarize documentation coverage / SOP
   availability / freshness / ownership gaps / publication status but never invents documentation, fabricates
   SOPs, creates procedures, implies approvals, infers unpublished knowledge, or modifies documentation.

No migration, no new table, no new capability, no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`.

## Alternatives considered
- **A second wiki / document-management platform / Confluence replacement / SharePoint / records-management
  platform / search engine / AI knowledge store / document repository.** Rejected: the Document Platform,
  Document Intelligence, and Data Governance retention are the authoritative owners; D.62 composes them.
  Governance forbids a second store and any document / version / retention mutation. Where no owner exists (SOP
  governance, runbooks, wiki, Confluence, search index), the entry declares `not_configured`.
- **A knowledge-scoring engine that fabricates institutional knowledge.** Rejected: any figure comes from an
  authoritative source; the one derived summary is deterministic, labeled `derived`, keeps
  configured/not_configured visible, and is a documentation-coverage summary — never fabricated documentation,
  SOP approval, version history, or institutional knowledge.
- **Reusing the `knowledge.enabled` gate.** Rejected: it is owned by the D.45 Knowledge Graph; D.62 uses the
  distinct `knowledge_management.enabled` master gate to avoid coupling two unrelated layers to one flag.

## Reasons for the decision
Knowledge / SOP / documentation governance needs one operational view; the Document Platform, Document
Intelligence, and retention already own every signal with the correct scoping. A read-only composition gives
that view with full explainability (source + deep link) while every document stays owned by the Document
Platform, every version by its immutable-version store, and every retention decision by Data Governance.
Emitting counts / status / coverage only keeps document contents and confidential procedures out of the layer
entirely.

## Rationale for avoiding a second wiki, document repository, or knowledge platform
A second wiki / document repository / knowledge platform would require duplicated SOPs, policies, procedures,
runbooks, documentation, versions, approvals, articles, and metadata, plus its own search + approval model —
duplicating governed infrastructure and creating reconciliation + drift + shadow-documentation risk, with no
benefit the composition does not already provide. Composing over the single Document Platform keeps one source
of truth for every document and zero fabricated knowledge.

## Consequences

### Positive consequences
- One firm-wide knowledge / SOP / documentation surface with no second wiki / DMS / Confluence / SharePoint /
  search / AI-knowledge-store / document repository.
- Record scope + capability inherited from composed owners; a restricted panel leaks no value or count;
  client/household sections expose only record-scoped documentation, never internal SOPs or firm-wide metrics.
- Zero schema change; Advisor Workspace Knowledge & SOPs panel + Client 360 / Household 360 Documentation
  sections + an Executive Enterprise Knowledge & Documentation dashboard + AI summarize-only.
- SOP governance / runbooks / wiki / Confluence / search reported `not_configured` — honest; posture is a
  documentation-coverage summary, never fabricated knowledge.

### Negative consequences and tradeoffs
- Dashboards recompute per request (no persistence); ownership coverage is sampled over the first document
  window (flagged `sampled`) when the corpus exceeds the page window.
- Coverage is bounded by the owners' read surface; a genuinely new knowledge signal is added to the owning
  domain first, then surfaces here.
- SOP governance / runbooks / wiki / search stay `not_configured` until an authoritative owner exists.

## Enforcement
`tests/test_knowledge_management.py` (five registries + integrity + duplicate-key prevention + configured-owner
validation + honest not_configured + non-colliding master gate; explainable composition; authorization —
unauthorized → None, unentitled panel restricted; runtime + policy gates; the firm summary + record-scoped
client/household documentation rollups that hide firm data; analytics reuse; diagnostics; routes registered +
capability-gated documentation OR executive; AI summarize-only; the no-fabricated-knowledge invariant; and the
architecture invariants — no second wiki/document platform, no persistence, no mutation, no unauthorized
document exposure). `app/services/knowledge_management/governance.py` enforces the invariants at runtime. Route
count, section registries, ADR count, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` +
`tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate are exposed only within dashboards whose required capability
(`documents.view` / `analytics.executive`) the principal holds; each panel additionally self-restricts.
Client-scoped sections compose ONLY the record-scoped Document Intelligence per-entity read — internal SOPs and
firm-wide documentation metrics are never exposed at client/household scope.

## Revisit conditions
Revisit when an authoritative SOP / runbook / wiki / knowledge-base / Confluence / full-text-search owner is
added (compose it here, replacing the `not_configured` entries — never a second wiki/document repository).

## References
- `app/services/knowledge_management/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/knowledge_management.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Knowledge & SOPs panel in
  `app/services/workspace/service.py`; Executive dashboard in `app/services/executive_intelligence/registry.py`;
  AI grounding in `app/services/ai_assist/context.py`; analytics counters in
  `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/document_platform/{service,relationships,versions}.py`,
  `app/services/document_intelligence/*`, `app/services/governance/retention.py`, the Runtime + Policy engines
- `docs/ENTERPRISE_KNOWLEDGE_MANAGEMENT.md`, `docs/KNOWLEDGE_DOMAIN_REGISTRY.md`, `docs/SOP_GOVERNANCE.md`,
  `docs/DOCUMENTATION_OWNERSHIP.md`, `docs/KNOWLEDGE_INTELLIGENCE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_knowledge_management.py`; relates to ADR-016, ADR-023, ADR-045, ADR-050, ADR-055, ADR-066
