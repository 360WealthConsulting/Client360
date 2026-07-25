# ADR-071 — Enterprise Data Governance, Lineage & Information Stewardship Intelligence: A Read-Only Composition, Not a Second Data Catalog / Metadata Repository / Lineage Engine / Governance Platform

## Status
Accepted

## Date
2026-07-25

## Decision owners
Platform Architecture; Domain Owner (Data Governance / Lineage / Information Stewardship); Data Governance;
Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.66 audit inventoried every data-domain / lineage / stewardship / quality / retention owner the
platform actually has, and the ones it does **not**:

* **Governance catalog (`app/services/governance/catalog.py`, D.23)** — the authoritative data catalog:
  `list_domains` (data domains, each carrying a `steward_user_id`), `list_elements` (catalogued fields),
  `list_rules` (data-quality / governance rules), `list_survivorship_rules`. Mutations (`create_domain`,
  `create_element`, `create_rule`) are the prohibited surface.
* **Governance MDM (`app/services/governance/mdm.py`)** — the authoritative lineage / provenance owner:
  `list_lineage(entity_type, entity_id)`, `person_lineage(principal, person_id)` (record-scoped source-system
  provenance), `list_candidates` (merge-candidate backlog). Mutation: `record_lineage`, `record_merge_decision`.
* **Governance Quality (`app/services/governance/quality.py`)** — `metrics` (open / critical-open findings),
  `list_findings`. Mutation: `create_finding`, `run_check`. **Governance Retention
  (`app/services/governance/retention.py`)** — `list_retention_assignments`, `list_legal_holds`,
  `list_deletion_requests`, `list_cases`, `metrics`. Mutation: `create_retention_assignment`,
  `place_legal_hold`, `execute_deletion`, `create_case`.
* **Genuinely absent (not_configured):** there is **no external data-catalog integration (Collibra / Alation),
  no business glossary / data dictionary, no data-classification taxonomy, no automated column-level lineage
  (MDM provenance is source-system level), no data-sharing agreements / data contracts, no data-quality
  scorecards / SLAs, no retention-policy catalog beyond the Document Platform, no DPIA, and no data-product /
  golden-record store.**

There was already a **D.52 Data Governance composition layer** (`app/services/data_governance/`,
`/data-governance`, `governance.view`, gates `data_governance.enabled` / `stewardship.enabled` /
`lineage.enabled`) — the operational data-quality / lineage / stewardship / ownership view. D.66 is a
**dedicated, deeper** Data Governance, Lineage & Information Stewardship **Intelligence** layer: five
declarative registries mapping every data domain to its authoritative owner + lineage source + steward +
quality rule + retention rule, plus governance-readiness, data-risk, and gap intelligence. Both are read-only
views over the SINGLE authoritative D.23 Governance package; neither owns, persists, or duplicates governance
data. Building a second data catalog, metadata repository, ETL platform, MDM platform, warehouse, governance
platform, lineage engine, or quality engine would violate the "no second system" invariant and duplicate
governed infrastructure — and would invite fabricated lineage, metadata, or quality scores the platform cannot
truthfully assert.

## Decision
Phase D.66 adds a **governed, read-only data-governance-intelligence composition layer**
(`app/services/data_governance_intelligence/`) with NO new capability, NO new metric, NO persistence, and NO
mutation:

1. Five declarative **registries** (`registry.py`): `DATA_DOMAIN_REGISTRY` (8 — external catalog / business
   glossary / classification not_configured), `DATA_LINEAGE_REGISTRY` (5 — automated column lineage / data
   contracts not_configured), `DATA_STEWARDSHIP_REGISTRY` (5 — stewardship workflow / data-product ownership
   not_configured), `DATA_QUALITY_REGISTRY` (5 — DQ scorecards / SLAs not_configured), and
   `DATA_RETENTION_REGISTRY` (6 — retention-policy catalog / DPIA not_configured), each naming authoritative
   owner + read surface + **prohibited mutation surface** + evidence source + governing capability + runtime
   gate + deep links + config status. Plus `PANEL_REGISTRY` (29) and `DATA_GOVERNANCE_DASHBOARDS` (8).
2. Normalized read-models (`model.py`): `PanelResult` + `DataGovernanceDashboard`, each explainable (a hard
   emit gate), carrying `derived` / `config_status`; **counts, coverage, status, and ratios only — never a
   sensitive data value, client PII, credential, secret, token, confidential metadata, internal governance
   note, or quality-rule internal.**
3. A **panel compute layer** (`panels.py`): each panel's value is composed on read by its authoritative owner
   (the Governance catalog / MDM / quality / retention owners); fail-closed; every panel self-restricts.
   External catalog / business glossary / classification / column lineage / contracts / DQ scorecards /
   retention-policy catalog panels are emitted `available=False` with `config_status='not_configured'` —
   honest, never fabricated. `executive_data_governance_posture`, `governance_readiness`, `data_risk_indicators`,
   and the coverage panels are **DERIVED** governance-readiness summaries (labeled `derived`).
4. The **data-governance-intelligence engine** (`service.py`): `compose_dashboard`, `list_dashboards`,
   `get_panel`, `data_governance_summary`, plus `client_data_governance` / `household_data_governance` — which
   compose the record-scoped source-system provenance from the authoritative Governance MDM owner
   (`person_lineage`), exposing ONLY the provenance record count + distinct source-system names, **never an
   internal governance note, confidential metadata, a quality-rule internal, system architecture, platform
   configuration, or an INFERRED governance state.** Dashboard-level authorization admits **a governance viewer
   OR an executive** (`governance.view` / `analytics.executive`, via `require_any_capability`).
5. **Runtime gates** (`data_governance_intelligence.enabled`, `lineage_landscape.enabled`,
   `data_quality_landscape.enabled`, `data_governance_ai_summary.enabled` — all distinct, no reused/unrelated
   gate, no runtime-variable fallback; in particular the master gate is `data_governance_intelligence.enabled`,
   NOT the D.52 layer's `data_governance.enabled`, and `lineage_landscape.enabled`, NOT D.52's
   `lineage.enabled`) + the runtime gate of every composed source, **policy composition**, **analytics reuse**
   (four operational counters into the ONE Analytics Registry — no second registry), internal **diagnostics**
   (`observability.audit`), and a read-only **governance** checker that forbids mutation, persistence, any
   catalog / lineage / quality / retention mutation (`create_domain`, `create_rule`, `record_lineage`,
   `create_finding`, `run_check`, `create_retention_assignment`, `place_legal_hold`, `execute_deletion`, …), a
   second metrics registry, a fabricated lineage / metadata / quality score, and a gate collision with the D.52
   layer. AI Assist may summarize governance coverage / lineage completeness / stewardship readiness / quality
   coverage / retention readiness but never invents lineage, fabricates metadata, assigns stewardship, modifies
   governance, repairs data, or infers missing ownership.

No migration, no new table, no new capability, no new metric, no new outbox contract. Single Alembic head stays
`n5s6u7p8v9w0`. **A registered rule is not an executed check, a steward assignment is not a governance
guarantee, a lineage record is not a complete lineage, and coverage is not certification** — every derived
summary states this explicitly.

## Alternatives considered
- **A second data catalog / metadata repository / ETL platform / MDM platform / warehouse / governance platform
  / lineage engine / quality engine.** Rejected: the Governance catalog / MDM / quality / retention owners are
  the authoritative owners; D.66 composes them. Governance forbids a second store and any catalog / lineage /
  quality / retention mutation. Where no owner exists (external catalog, business glossary, classification,
  column lineage, contracts, scorecards, retention-policy catalog, DPIA), the entry declares `not_configured`.
- **A data-quality-scoring engine that fabricates quality scores or lineage.** Rejected: any figure comes from
  an authoritative source; the derived summaries are deterministic, labeled `derived`, keep configured /
  not_configured visible, and are governance-readiness summaries — never a repaired dataset, a created lineage
  edge, an assigned steward, an executed quality rule, or an enforced retention decision.
- **Folding D.66 into the D.52 Data Governance layer.** Rejected: D.52 is the operational view; D.66 is the
  deeper readiness / lineage / stewardship-intelligence view with its own registries and gates. Both compose
  the single authoritative Governance package; a distinct layer keeps each independently governed and disabled
  (mirroring D.65 vs the D.54 identity facet).
- **Reusing the D.52 gate (`data_governance.enabled`).** Rejected: it is owned by the D.52 layer; D.66 uses the
  distinct `data_governance_intelligence.enabled` master gate to avoid coupling two unrelated layers to one
  flag (mirroring D.62's `knowledge_management.enabled` vs D.45's `knowledge.enabled`).

## Reasons for the decision
Data-governance readiness / lineage / stewardship intelligence needs one operational view; the Governance
catalog / MDM / quality / retention owners already own every signal with the correct scoping. A read-only
composition gives that view with full explainability (source + deep link) while every catalog entry, lineage
edge, quality finding, and retention record stays owned by the Governance package. Emitting counts / coverage /
ratios only — and reporting the genuinely absent owners as `not_configured` — keeps sensitive data values,
client PII, confidential metadata, and fabricated governance state out of the layer entirely.

## Rationale for avoiding a second data catalog, metadata repository, lineage engine, or governance platform
A second data catalog / metadata repository / lineage engine / governance platform would require duplicated
metadata, lineage, governance policies, quality rules, source mappings, catalog entries, stewardship
assignments, and retention records, plus its own ETL + quality-execution + lineage-capture model — duplicating
governed infrastructure and creating reconciliation + drift + shadow-metadata risk (a second source of
governance truth undermines the first), and tempting the system to assert lineage or quality it cannot
truthfully know. Composing over the single Governance package keeps one source of truth and zero fabricated
governance.

## Consequences

### Positive consequences
- One firm-wide data-governance-intelligence surface with no second catalog / metadata repository / ETL / MDM /
  warehouse / governance platform / lineage / quality engine.
- Scope + capability inherited from composed owners; a restricted panel leaks no value or count; Client 360 /
  Household 360 sections expose only record-scoped source-system provenance (counts + source-system names),
  never confidential metadata, quality-rule internals, or an inferred governance state.
- Zero schema change; Advisor Workspace Data Governance Status panel + Client 360 / Household 360 Data Lineage &
  Provenance sections + an Executive Enterprise Data Governance dashboard (reusing existing widgets) + AI
  summarize-only.
- External catalog / business glossary / classification / column lineage / contracts / DQ scorecards /
  retention-policy catalog / DPIA reported `not_configured` — honest; posture is a governance-readiness
  summary, never a certified data-governance outcome.

### Negative consequences and tradeoffs
- Dashboards recompute per request (no persistence); firm-wide lineage-row counts have no aggregate reader, so
  firm-level lineage is expressed as domain coverage while provenance is composed at record scope.
- Coverage is bounded by the owners' read surface; a genuinely new governance signal (e.g. a real business
  glossary owner) is added to the owning domain first, then surfaces here, replacing a `not_configured` entry.
- External catalog, business glossary, classification, column lineage, contracts, DQ scorecards,
  retention-policy catalog, and DPIA stay `not_configured` until an authoritative owner exists — deliberately,
  to avoid fabricated governance state.

## Enforcement
`tests/test_data_governance_intelligence.py` (five registries + integrity + duplicate-key prevention [incl.
cross-registry] + configured-owner validation + honest not_configured + distinct non-colliding master gate;
explainable composition; authorization — unauthorized → None, unentitled panel restricted; runtime + policy
gates; the firm summary + record-scoped client / household data-governance-metadata sections that expose only
source-system provenance and never infer governance state or leak confidential metadata; analytics reuse;
diagnostics; routes registered + capability-gated governance OR executive; AI summarize-only; the
registered-rule-is-not-an-executed-check / lineage-record-is-not-complete-lineage / coverage-is-not-certification
invariants; and the architecture invariants — no second data catalog / lineage engine / metadata repository, no
persistence, no mutation, no fabricated metadata / inferred lineage).
`app/services/data_governance_intelligence/governance.py` enforces the invariants at runtime. Route count,
section registries, ADR count, and migration head are guarded by `tests/test_platform_architecture.py` +
`tests/test_client360_workspace.py` + `tests/test_household360_workspace.py` +
`tests/test_executive_reporting.py` + `tests/test_architecture_decision_records.py` + the manifest.

## Exceptions
Firm-global reads that do not self-gate are exposed only within dashboards whose required capability
(`governance.view` / `analytics.executive`) the principal holds; each panel additionally self-restricts to its
authoritative-source capability. Client-scoped sections compose ONLY the record-scoped Governance MDM
provenance read — confidential metadata, quality-rule internals, system architecture, and platform
configuration are never exposed at record scope, and governance state is never inferred.

## Revisit conditions
Revisit when an authoritative external-catalog, business-glossary, data-classification, automated-column-lineage,
data-contract, DQ-scorecard / SLA, retention-policy-catalog, or DPIA owner is added (compose it here, replacing
the `not_configured` entries — never a second data catalog / metadata repository / lineage engine / governance
platform, never a fabricated governance state).

## References
- `app/services/data_governance_intelligence/*` (`registry.py`, `model.py`, `service.py`, `panels.py`,
  `gate.py`, `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `__init__.py`)
- `app/routes/data_governance_intelligence.py`; Client 360 section in
  `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; Data Governance Status panel in `app/services/workspace/service.py`;
  Executive dashboard in `app/services/executive_intelligence/registry.py`; AI grounding in
  `app/services/ai_assist/context.py`; analytics counters in `app/services/analytics/{sources,metrics}.py`
- Composes `app/services/governance/{catalog,mdm,quality,retention,service}.py`, the Runtime + Policy engines
- `docs/ENTERPRISE_DATA_GOVERNANCE.md`, `docs/DATA_DOMAIN_REGISTRY.md`, `docs/DATA_LINEAGE_REGISTRY.md`,
  `docs/DATA_STEWARDSHIP_REGISTRY.md`, `docs/DATA_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_data_governance_intelligence.py`; relates to ADR-023 (governance), ADR-031 (runtime/policy),
  ADR-057 (D.52 data governance), ADR-070
