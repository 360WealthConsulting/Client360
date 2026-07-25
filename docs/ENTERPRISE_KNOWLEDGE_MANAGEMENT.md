# Enterprise Knowledge Management, SOP Governance & Institutional Intelligence (Phase D.62)

`app/services/knowledge_management/` is a governed, **read-only composition** that provides a unified, governed
view of firm knowledge, SOPs, and documentation — SOP coverage, documentation completeness, document freshness,
ownership coverage, version awareness, publication readiness, documentation gaps, orphaned documentation,
runbook coverage, and knowledge health. It is **not** a second wiki, document-management platform, Confluence
replacement, SharePoint, records-management platform, search engine, AI knowledge store, or document
repository: **no new capability, no new metric, no persistence, no mutation, no duplicated documentation, no
migration** (single Alembic head `n5s6u7p8v9w0`).

> These are **documentation-coverage summaries**, never fabricated documentation, SOP approval, version
> history, or institutional knowledge.

> **Distinct from the D.45 Knowledge GRAPH.** This D.62 layer's master runtime gate is
> `knowledge_management.enabled` — NOT the D.45 graph's `knowledge.enabled`.

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| Documentation completeness / gaps / freshness | Document Intelligence (D.50) | `document_summary` | `documents.view` |
| Document inventory / lifecycle / classification | Document Platform (D.16) | `list_documents(status=/classification=)` | `documents.view` |
| Publication readiness | Document Platform lifecycle | `list_documents(status=approved/draft/review)` | `documents.view` |
| Ownership / version awareness | Document Platform (`created_by`, immutable versions) | `list_documents` | `documents.view` |
| Retention coverage | Data Governance retention (D.23) | `governance.retention.metrics` | `governance.view` |
| Record-scoped client documentation | Document Intelligence per-entity | `client_documents` / `household_documents` | `documents.view` |

## The not_configured domains (reported honestly)

The D.62 audit confirmed several domains have **no authoritative owner** and are declared `not_configured` (the
D.55–D.61 precedent), never fabricated: **SOP governance, runbooks, playbooks, onboarding SOPs, a knowledge
base, institutional memory, a wiki, Confluence, and a dedicated full-text / vector search index**. The Document
Platform has `operations` / `internal` / `compliance` classified documents (surfaced for operational /
compliance SOP categories) but no SOP-governance engine; document search is a basic filename filter, not a
search platform.

## Registries, panels, dashboards

Five declarative registries — Knowledge Domains (8) + SOP Categories (6) + Documentation Owners (5) + Knowledge
Sources (7) + Publication Statuses (5) — plus 22 panels and 8 dashboards (knowledge_overview, sop_governance,
documentation_health, ownership_coverage, publication_readiness, knowledge_gaps, executive_knowledge_status,
documentation_quality). See [KNOWLEDGE_DOMAIN_REGISTRY.md](KNOWLEDGE_DOMAIN_REGISTRY.md),
[SOP_GOVERNANCE.md](SOP_GOVERNANCE.md), and [DOCUMENTATION_OWNERSHIP.md](DOCUMENTATION_OWNERSHIP.md). Every
dashboard carries a generated timestamp, governing services, source inventory, explainable panels, deep links,
and its configured / not_configured domain lists.

## Panels — counts, status, coverage only

Panels carry counts, status, and coverage only. They **never** return document contents, confidential
procedures, credentials, tokens, or client-sensitive documentation. The `executive_knowledge_status` panel is
a DERIVED documentation-coverage summary (labeled `derived`) — never a certified SOP / approval /
institutional-knowledge figure.

## Authorization

- Routes + dashboards admit **documentation OR an executive** (`documents.view` / `analytics.executive`, via
  `require_any_capability`); diagnostics by `observability.audit`.
- Each **panel self-restricts** to its authoritative-source capability (retention panels `governance.view`,
  executive panel `analytics.executive`). A principal lacking the panel capability receives a `restricted`
  panel with `value = None`, no hidden count, and no leaking metadata.
- Client-scoped sections compose ONLY the record-scoped Document Intelligence per-entity read — internal SOPs,
  unrelated documentation, confidential operational procedures, and firm-wide documentation metrics are never
  exposed at client/household scope.

## Runtime, governance, analytics, observability

Every surface is gated through the Runtime Engine (`knowledge_management.enabled`, `sop_governance.enabled`,
`documentation.enabled`, `knowledge_ai_summary.enabled`) **and** the runtime gate of every composed source,
plus the Policy Engine — **no environment bypass**. Governance (`validate_knowledge_management()`) returns
`{ok, issue_count, findings}` and forbids persistence, mutation, any document / version / retention mutation, a
second metrics registry, and a fabricated document / SOP / version — see
[KNOWLEDGE_INTELLIGENCE_GOVERNANCE.md](KNOWLEDGE_INTELLIGENCE_GOVERNANCE.md). Four low-cardinality counters
register into the **single** Analytics Registry. Internal diagnostics (`/knowledge-management/diagnostics`,
`observability.audit`) report registry coverage, configured vs not_configured counts, documentation / ownership
coverage, panel availability, and the governance summary.

## Surfaces

- **Advisor Workspace** — a **Knowledge & SOPs** panel (`ws["knowledge_sops"]`) showing relevant procedures,
  documentation health, SOP coverage, and knowledge advisories.
- **Client 360 / Household 360** — a **Documentation** section (`documents.view`): only the record-scoped
  documentation relevant to servicing that client / household (document count + gaps), via the Document
  Intelligence per-entity read. Internal SOPs and firm-wide metrics are never exposed.
- **Executive Dashboard** — an **Enterprise Knowledge & Documentation** dashboard reusing existing widgets
  (`compliance_workload`, `operational_health` — no new widget).
- **AI Assist** — summarizes documentation coverage / SOP availability / document freshness / ownership gaps /
  publication status. It **never** invents documentation, fabricates SOPs, creates procedures, implies
  approvals, infers unpublished knowledge, or modifies documentation.

## Routes

`/knowledge-management` (HTML) + `/api/v1/knowledge-management/{dashboards, dashboard/{key}, summary, registry,
panel/{key}, metrics}` + `/knowledge-management/diagnostics`.

See [KNOWLEDGE_DOMAIN_REGISTRY.md](KNOWLEDGE_DOMAIN_REGISTRY.md), [SOP_GOVERNANCE.md](SOP_GOVERNANCE.md),
[DOCUMENTATION_OWNERSHIP.md](DOCUMENTATION_OWNERSHIP.md),
[KNOWLEDGE_INTELLIGENCE_GOVERNANCE.md](KNOWLEDGE_INTELLIGENCE_GOVERNANCE.md), and
[ADR-067](adr/ADR-067-knowledge-management.md).
