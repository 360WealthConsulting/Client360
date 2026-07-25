# Knowledge Intelligence Governance (Phase D.62)

`app/services/knowledge_management/governance.py` is a read-only checker that verifies the knowledge layer
stays a **composition** over the authoritative knowledge / SOP / documentation owners and never becomes a
second wiki, document-management platform, Confluence replacement, SharePoint, records-management platform,
search engine, AI knowledge store, or document repository. It returns `{ok, issue_count, findings}` and **never
raises** into normal use. `validate_knowledge_management()` is surfaced through the internal diagnostics
endpoint (`/knowledge-management/diagnostics`, gated by `observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe`), or writes
   audit events (`write_audit(`). No `rm_*` projection table is read directly.
2. **No second wiki / DMS / version engine — no mutation.** No module calls a document / version / retention
   **mutation** — `create_document(`, `update_document(`, `set_status(`, `approve(`, `archive(`, `soft_delete(`,
   `create_folder(`, `create_version(`, `approve_version(`, `restore_version(`, `create_retention_assignment(`,
   `place_legal_hold(`, `execute_deletion(`, `link_entity(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every knowledge-domain / SOP-category / documentation-owner /
   knowledge-source / publication-status / panel / dashboard key is unique; every **configured** entry names an
   authoritative owner.
5. **No fabricated knowledge.** Any status / health / coverage / gaps panel **derived from the layer's own
   registries/compose** must be labeled `derived` (`unlabeled_derived_summary` otherwise). The
   `executive_knowledge_status` panel is a DERIVED documentation-coverage summary and never a certified SOP /
   approval / institutional-knowledge figure.
6. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in both
   `model.py` and `panels.py`.
7. **No raw environment gating.** Gates flow through the Runtime + Policy engines — never `os.getenv` /
   `os.environ`. The master gate is `knowledge_management.enabled` (distinct from the D.45 graph's
   `knowledge.enabled`).

## No sensitive documentation + honest not_configured

Panels and summaries carry **counts, status, and coverage only** — never document contents, confidential
procedures, credentials, tokens, or client-sensitive documentation. The composed owners already strip payloads;
the knowledge layer surfaces only aggregates. SOP governance, runbooks, playbooks, onboarding SOPs, a knowledge
base, institutional memory, a wiki, Confluence, and a dedicated search index have **no authoritative owner
today** and are declared `not_configured` — reported honestly, never fabricated.

## Authorization & least privilege

- Knowledge routes admit **documentation OR an executive** (`documents.view` / `analytics.executive`, via
  `require_any_capability`); diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities`; otherwise
  `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to its authoritative-source capability (retention panels `governance.view`,
  executive panel `analytics.executive`). A principal lacking the panel capability receives a `restricted`
  panel with `value = None`, no hidden count, and no leaking metadata.
- Client-scoped sections compose ONLY the record-scoped Document Intelligence per-entity read — internal SOPs
  and firm-wide documentation metrics are never exposed at client/household scope.

## AI Assist boundary

AI Assist may **summarize** documentation coverage, SOP availability, document freshness, ownership gaps, and
publication status (fact class `DERIVED`, counts only, deep links only). It **never** invents documentation,
fabricates SOPs, creates procedures, implies approvals, infers unpublished knowledge, or modifies
documentation.

## Enforcement

`tests/test_knowledge_management.py` exercises the five registries, integrity + duplicate-key prevention +
configured-owner validation + honest not_configured + the non-colliding master gate, explainable composition,
authorization (`None` + restricted), gate/policy behavior, the record-scoped client/household documentation
rollups that hide firm data, the analytics-counter reuse, diagnostics, the routes (registered + capability-gated
documentation OR executive), AI summarize-only, the no-fabricated-knowledge invariant, and the architecture
invariants (no second wiki/document platform, no persistence, no mutation, no unauthorized document exposure).
Route count, section registries, ADR count, and the single migration head are guarded by
`tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [ENTERPRISE_KNOWLEDGE_MANAGEMENT.md](ENTERPRISE_KNOWLEDGE_MANAGEMENT.md),
[KNOWLEDGE_DOMAIN_REGISTRY.md](KNOWLEDGE_DOMAIN_REGISTRY.md), [SOP_GOVERNANCE.md](SOP_GOVERNANCE.md),
[DOCUMENTATION_OWNERSHIP.md](DOCUMENTATION_OWNERSHIP.md), and [ADR-067](adr/ADR-067-knowledge-management.md).
