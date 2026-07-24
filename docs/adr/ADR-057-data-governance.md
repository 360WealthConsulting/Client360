# ADR-057 — Enterprise Data Governance, Master Data & Platform Stewardship: A Read-Only Composition, Not a Second Master-Data/Identity Platform

## Status
Accepted

## Date
2026-07-24

## Decision owners
Platform Architecture; Domain Owner (Data Governance / Information Management); Reliability / Operations;
Security / Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.52 audit found the platform already owns every authoritative data owner, entity registry,
metadata source, stewardship process, and lineage capability — anchored by the **D.23 Governance package**
(`app/services/governance/`), which is itself already a read-only composition over the identity/merge and
quality subsystems:

* **Master-data / metadata catalog** — `governance/catalog.py` (`list_domains` with `steward_user_id`,
  `list_elements`, `list_rules`, `list_survivorship_rules`) over `governance_data_domains` /
  `governance_data_elements` / `governance_quality_rules` / `governance_survivorship_rules`.
* **Identity / Person-merge / entity-resolution** — `app/services/person_merge.py`
  (`merge_source_contacts`, the canonical merge — WRITE) + `app/matching/promote.py`
  (`list_ambiguous_unlinked` read; `resolve_*` writes) + `governance/mdm.py` (`list_candidates`,
  `get_candidate`, `person_lineage`, `list_lineage` reads; `scan_duplicates`, `record_merge_decision` writes)
  over `person_source_links` / `source_contacts` / `governance_duplicate_candidates`.
* **Lineage / provenance** — `governance/mdm.py person_lineage` (reads `person_source_links` — "not
  duplicated") + `list_lineage` over `governance_lineage`; event-dependency lineage
  `events/registry.py dependency_graph`.
* **Data-quality / validation** — `governance/quality.py` (`list_findings`, `metrics`); **retention/cases**
  `governance/retention.py`; **overview** `governance/service.py overview_metrics`.
* **Domain entity owners** — people, household_derivation, organization_service, portfolio, insurance,
  benefits_domain, tax_domain, opportunity, document_platform, identity, relationships.

There was **no enterprise data-governance composition layer** unifying these into named, firm-wide views of
master data, stewardship, lineage, ownership, duplicate detection, validation, and data quality. Building a
second master-data platform, identity store, synchronization engine, entity-resolution engine, metadata
repository, or merge engine would violate the "no second system" invariant and duplicate governed, gated
infrastructure.

## Decision
Phase D.52 adds a **governed, read-only data-governance composition layer**
(`app/services/data_governance/`) with NO new metrics, NO persistence, and NO mutation:

1. Two declarative **registries** (`registry.py`): `MASTER_DATA_REGISTRY` (15 governed entities — Person,
   Household, Organization, Advisor, Client, Prospect, Trust, Estate, Account, Policy, Plan, Tax Return,
   Engagement, Opportunity, Document — each naming authoritative / identity / metadata / stewardship /
   lineage owner + runtime gate + deep links) and `STEWARDSHIP_REGISTRY` (8 responsibilities — business /
   technical / validation / approval owner + runtime gate), plus `PANEL_REGISTRY` (19 panels) and
   `GOVERNANCE_DASHBOARDS` (7 dashboards).
2. Normalized read-models (`model.py`): `PanelResult` + `GovernanceDashboard`, each explainable (explanation
   + source + deep link, a hard emit gate) and reference-only; **counts + status only, never a
   client-sensitive payload**.
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Governance catalog / quality / MDM / retention / overview, the entity-resolution engine, the Event
   registry). Fail-closed; every panel self-restricts to `governance.view`.
4. The **data-governance engine** (`service.py`): `compose_dashboard`, `list_dashboards`, `get_panel`,
   `governance_summary`, plus `client_governance` / `household_governance` (person-lineage rollups). Every
   dashboard carries generated timestamp, governing services, source inventory, explainable panels, and deep
   links. Dashboard-level authorization (`governance.view`).
5. **Runtime gates** (`data_governance.enabled` + `stewardship.enabled` + `lineage.enabled`), **policy
   composition**, **analytics reuse** (four operational counters registered into the ONE Analytics Registry —
   no second registry), internal **diagnostics** (`observability.audit`), and a read-only **governance**
   checker that forbids mutation, persistence, and any merge / identity / lineage / catalog mutation call
   (`merge_source_contacts`, `resolve_*`, `record_merge_decision`, `scan_duplicates`, `record_lineage`,
   `create_domain`, `run_check`, …). AI Assist may summarize governance counts but never merges entities,
   alters identities, modifies metadata, approves stewardship, changes ownership, or bypasses validation.

No migration, no new table, no new capability (reuses `governance.view` + `observability.audit`), no new
metric, no new outbox contract. Single Alembic head stays `n5s6u7p8v9w0`.

## Alternatives considered
- **A second master-data platform / identity store / MDM / entity-resolution / merge engine.** Rejected: the
  Governance package + person-merge + matching engine are the authoritative owners; D.52 composes them.
  Governance forbids a second store, identity system, and merge call.
- **A second metadata repository.** Rejected: metadata is owned by `governance.catalog`; the layer references
  it and defines no catalog of its own beyond the declarative entity/stewardship registries (which reference,
  never store).
- **Persisting composed governance state / a data warehouse.** Rejected: dashboards are a deterministic
  function of the authoritative data at read time; a store would be a governance warehouse to reconcile, and
  the layer must never hold master data or identities.

## Reasons for the decision
Data-governance leadership needs one enterprise view; the Governance package + entity owners already own
every number with the correct scoping. A read-only composition gives that view with full explainability
(source + deep link) while every identity stays owned by the person-merge engine, every metadata element by
the catalog, every quality finding by the quality engine, and every lineage record by
`person_source_links` / `governance_lineage`. Deep links (never inline merge) route the steward to the
authoritative surface to act. Emitting counts + status only keeps client-sensitive data and entity payloads
out of the layer entirely.

## Rationale for avoiding a second master data or identity platform
A second MDM / identity platform would require copied identities + metadata, a parallel merge + survivorship
model, its own synchronization, and its own access model — duplicating governed, gated infrastructure and
creating reconciliation + drift + split-identity risk, with no benefit the composition does not already
provide. Composing over the single Governance package keeps one source of truth for every entity, one merge
engine, one metadata catalog, and zero copied identities.

## Consequences

### Positive consequences
- One firm-wide data-governance surface with no second master-data platform, identity store, or merge engine.
- Record scope + capability are inherited from the composed governance reads; a non-`governance.view`
  principal sees restricted panels, never values, and never a client-sensitive payload.
- Zero schema change: no migration, table, capability, metric, or outbox contract.
- Advisor Workspace Data Governance panel + Client 360 / Household 360 Data Governance sections + an
  Executive Data Governance dashboard (reusing existing widgets) + AI summarize-only, all from one layer.

### Negative consequences and tradeoffs
- Dashboards are recomputed per request (no persistence) — bounded by the authoritative reads' cost.
- Per-client / per-household governance rollups compose `person_lineage` per member — bounded by member
  count; a count rollup only.
- The layer's coverage is bounded by the Governance package's read surface; a genuinely new governance signal
  is added to the Governance package first, then surfaces here.

## Enforcement
`tests/test_data_governance.py` (two registries + single ownership; explainable dashboard composition;
authorization — unauthorized → None, unentitled panel restricted never valued; runtime + policy gates; the
firm summary + client/household rollups; analytics reuse — the 4 counters in the ONE registry; diagnostics;
routes registered + capability-gated; AI summarize-only; and the architecture invariants — no second
master-data platform / identity system / merge engine / metadata repository, no mutation, governance reads
composed from the Governance package, every dashboard deep-links, every lineage summary names an
authoritative owner). `app/services/data_governance/governance.py` enforces the invariants at runtime. Route
count, section registries, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate (governance catalog counts, event dependency graph) are exposed only
within dashboards whose required capability (`governance.view`) the principal holds; each panel additionally
self-restricts to `governance.view`, so a value is never shown to a principal lacking that capability.

## Revisit conditions
Revisit when a new governance signal is required (add it to the Governance package), when a materialized
governance read-model is needed (it would be a governed projection, never a second master-data store), or if
a dedicated merge-history read is added (compose the existing `governance_merge_decisions`, never a second
merge engine).

## References
- `app/services/data_governance/*` (`registry.py`, `model.py`, `service.py`, `panels.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/data_governance.py`; Client 360 section in `app/services/client360/{registry,sections}.py`;
  Household 360 section in `app/services/client360/household.py`; Data Governance panel in
  `app/services/workspace/service.py`; Executive Data Governance dashboard in
  `app/services/executive_intelligence/registry.py`; AI grounding in `app/services/ai_assist/context.py`;
  analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/governance/*` (`catalog.py`, `quality.py`, `mdm.py`, `retention.py`, `service.py`),
  `app/services/person_merge.py`, `app/matching/promote.py`, `app/services/events/registry.py`
- `docs/DATA_GOVERNANCE.md`, `docs/MASTER_DATA_REGISTRY.md`, `docs/STEWARDSHIP_REGISTRY.md`,
  `docs/DATA_GOVERNANCE_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_data_governance.py`; relates to ADR-023, ADR-045, ADR-050, ADR-046 through ADR-056
