# Data Governance Governance (Phase D.52)

`app/services/data_governance/governance.py` is a read-only checker that verifies the Data Governance layer
stays a **composition** over the authoritative data owners and never becomes a second master-data platform,
identity system, synchronization engine, entity-resolution engine, metadata repository, or merge engine. It
returns `{ok, issue_count, findings}` and **never raises** into normal use. `validate_data_governance()` is
surfaced through the internal diagnostics endpoint (`/data-governance/diagnostics`, gated by
`observability.audit`).

## Enforced invariants

1. **No persistence / no mutation.** No module defines a `Table(...)`, writes the DB (`.insert(` / `.update(`
   / `.delete(` / `sa.insert` …), opens `engine.begin(`, publishes to the outbox (`publish_safe` /
   `publisher.publish`), or writes audit events (`write_audit_event`). No `rm_*` projection table is read
   directly.
2. **No second identity system / master-data store / merge engine.** No module calls a merge / identity /
   lineage / catalog **mutation** — `merge_source_contacts(`, `resolve_link_to_person(`,
   `resolve_create_person(`, `record_merge_decision(`, `scan_duplicates(`, `create_candidate(`,
   `record_lineage(`, `create_domain(`, `create_element(`, `create_rule(`, `create_survivorship_rule(`,
   `run_check(`, `run_all_active_checks(`, `run_stale_scan(`, `create_case(`, `set_case_status(`,
   `review_due_retention(`. The layer composes **reads** only.
3. **No second metrics registry.** No module defines a `_DEFS` catalog or a `Metric` class; the layer's
   counters register into the single Analytics Registry.
4. **Registry completeness + single ownership.** Every governed entity declares authoritative + identity +
   metadata + stewardship + lineage owner + runtime gate + deep links, and points at a registered stewardship
   role; every stewardship role declares business + technical + validation + approval owner + runtime gate;
   every dashboard declares owner + audience + runtime gate + navigation + panels + required capabilities +
   governing services, and references only registered panels; every panel declares owner + source + deep link
   + explainability + permission; all registry keys are unique.
5. **Explainability enforced.** `is_explainable` (explanation + source + deep link) is a hard emit gate in
   both `model.py` and `panels.py`; a non-explainable panel is never emitted.
6. **No raw environment gating.** Gates flow through the Runtime Engine (`runtime.consumption.feature_enabled`)
   and policy through the Policy Engine — never `os.getenv` / `os.environ`.

## No client-sensitive data, ever

Panels and summaries carry **counts + status only** — never client-sensitive data, entity payloads, or
identifiers. Diagnostics and analytics counters are low-cardinality aggregates about the layer itself. This
is a structural invariant of the model (`PanelResult` values are counts/status/rollups) and of the compose
layer (it reads governance *metrics* and *counts* and source-system names, never an entity payload).

## Authorization & least privilege

- Governance routes are gated by `governance.view`; diagnostics by `observability.audit`.
- A dashboard is composed only if the principal holds one of its `required_capabilities` (`governance.view`);
  otherwise `compose_dashboard` returns `None` (→ 404) and an authorization-failure counter increments.
- Each **panel self-restricts** to `governance.view`: a principal lacking it receives a `restricted` panel
  with `value = None` — never leaked.
- All composed reads inherit the record scope + capability checks of their authoritative owner (the
  Governance package's `scope_clause` / `visible`, the quality/MDM engines' scope).

## AI Assist boundary

AI Assist may **summarize** governance counts (duplicate summaries, validation status, stewardship items,
lineage summaries) — fact class `DERIVED`, counts only, deep links only. It **never** merges entities, alters
identities, modifies metadata, approves stewardship, changes ownership, or bypasses validation — every fact
comes from a composed section/summary.

## Enforcement

`tests/test_data_governance.py` exercises the registries, explainable composition, authorization (`None` +
restricted), gate/policy behavior, the analytics-counter reuse, diagnostics, the routes (registered +
capability-gated), AI summarize-only, and the architecture invariants (no second master-data platform /
identity system / merge engine / metadata repository, no mutation, governance reads composed from the
Governance package, every dashboard deep-links, every lineage summary names an authoritative owner). Route
count, section registries, ADR count, and the single migration head are guarded by
`tests/test_platform_architecture.py`, `tests/test_client360_workspace.py`,
`tests/test_household360_workspace.py`, `tests/test_architecture_decision_records.py`, and the manifest.

See [DATA_GOVERNANCE.md](DATA_GOVERNANCE.md), [MASTER_DATA_REGISTRY.md](MASTER_DATA_REGISTRY.md),
[STEWARDSHIP_REGISTRY.md](STEWARDSHIP_REGISTRY.md), and [ADR-057](adr/ADR-057-data-governance.md).
