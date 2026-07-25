# Data Governance (Phase D.52)

The **Data Governance** layer (`app/services/data_governance/`) is a governed, **read-only composition** that
gives data-governance leadership one enterprise view of data quality, lineage, stewardship, and ownership —
**without** building a second master-data platform, identity system, synchronization engine,
entity-resolution engine, metadata repository, or merge engine. Every number is composed on read from an
**authoritative owner**; the layer owns no persistence and never merges an entity, alters an identity,
modifies metadata, approves stewardship, or changes ownership. **Panels carry counts + status only — never a
client-sensitive payload or entity data.**

## What it composes (and never duplicates)

| Concern | Authoritative owner (composed) |
| --- | --- |
| Metadata / data catalog | `app/services/governance/catalog.py` — `list_domains`, `list_elements`, `list_rules`, `list_survivorship_rules` |
| Duplicate detection / merge candidates | `app/services/governance/mdm.py` — `list_candidates` (composes the authoritative person-merge) |
| Identity / entity resolution | `app/matching/promote.py` — `list_ambiguous_unlinked` (merge is `person_merge`, never called) |
| Lineage / provenance | `app/services/governance/mdm.py` — `person_lineage` (reads `person_source_links`); Event lineage `events/registry.py dependency_graph` |
| Data quality / validation | `app/services/governance/quality.py` — `list_findings`, `metrics` |
| Retention / cases | `app/services/governance/retention.py` — `list_cases` |
| Firm overview | `app/services/governance/service.py` — `overview_metrics` |
| Entity ownership | domain entity owners (people, portfolio, insurance, tax_domain, document_platform, …) |

See [MASTER_DATA_REGISTRY.md](MASTER_DATA_REGISTRY.md) for the governed entities,
[STEWARDSHIP_REGISTRY.md](STEWARDSHIP_REGISTRY.md) for the stewardship roles, and
[DATA_GOVERNANCE_GOVERNANCE.md](DATA_GOVERNANCE_GOVERNANCE.md) for the enforced invariants.

## Modules

- `registry.py` — the declarative catalogs: `MASTER_DATA_REGISTRY` (15 governed entities),
  `STEWARDSHIP_REGISTRY` (8 stewardship roles), `PANEL_REGISTRY` (19 panels), `GOVERNANCE_DASHBOARDS` (7
  dashboards).
- `model.py` — `PanelResult` + `GovernanceDashboard`. A panel is emitted only if `is_explainable`
  (explanation + source + deep link).
- `panels.py` — the per-panel compute functions. Read-only, fail-closed, **self-restricting** (a principal
  lacking `governance.view` gets a `restricted` panel, never its value). Counts + status only.
- `service.py` — the engine: `compose_dashboard`, `list_dashboards`, `get_panel`, `governance_summary`,
  `client_governance`, `household_governance`.
- `gate.py` — runtime gates (`data_governance.enabled`, `stewardship.enabled`, `lineage.enabled`) + policy
  composition. No raw environment gating.
- `stats.py` / `metrics.py` — low-cardinality in-process counters, registered into the **single** Analytics
  Registry (`analytics.metrics`). No second metrics registry; never client-sensitive data.
- `diagnostics.py` — internal-only observability (`observability.audit`).
- `governance.py` — read-only invariant checker (never raises), including merge/identity mutation-call tells.

## Dashboards

`master_data`, `stewardship`, `lineage`, `ownership`, `duplicate_detection`, `validation`, `data_quality`.
Each carries a generated timestamp, governing services, source inventory, explainable panels, and deep links
to the authoritative entity-owner surface. Dashboards are gated by `governance.view`; each panel additionally
self-restricts to `governance.view`.

## Surfaces

- **HTTP** (`app/routes/data_governance.py`, gated by `governance.view`; diagnostics by
  `observability.audit`): `/data-governance` (HTML), `/api/v1/data-governance/dashboards`, `/dashboard/{key}`,
  `/summary`, `/registry`, `/panel/{key}`, `/metrics`, `/data-governance/diagnostics`.
- **Advisor Workspace** — the Data Governance panel (`governance_summary`).
- **Client 360 / Household 360** — the `data_governance` section (`client_governance` /
  `household_governance`, person-lineage rollups; counts + source systems only).
- **Executive Dashboard** — a `data_governance` dashboard (composed from existing D.48 widgets; no new
  widget), navigation deep-linking to `/data-governance`.
- **AI Assist** — summarizes governance counts only; it never merges entities, alters identities, modifies
  metadata, approves stewardship, changes ownership, or bypasses validation.

## Invariants

No new persistence, no new metric, no new capability, no migration (single Alembic head unchanged). No
mutation, no merge, no outbox publication, no audit write, no second store. Every governance count comes from
the Governance package; every dashboard panel is explainable and deep-links to its authoritative surface.
Enforced by `app/services/data_governance/governance.py` and `tests/test_data_governance.py`. See
[ADR-057](adr/ADR-057-data-governance.md).

## Related: Integration Hub (D.53)

The D.53 Integration Hub layer (`app/services/integration_hub/`) provides the connected-platform view of
external systems, synchronization health, and connector status over the authoritative D.24 Integration
Platform — a read-only composition, never a second integration platform. Its per-client / per-household
External Integrations sections compose the same authoritative person lineage
(`source_contacts.source_system`) that Data Governance uses for provenance, framed as external-integration
connectivity. See [`INTEGRATION_HUB.md`](INTEGRATION_HUB.md) and ADR-058.

**Related (D.59):** the **Regulatory Readiness** layer (`/regulatory-readiness`) references this layer's
`governance_summary` as the authoritative owner of the `data_quality_validation` evidence class — read-only,
`governance.view`. It never validates or mutates data; Data Governance remains the authoritative owner. See
[REGULATORY_EXAMINATION_READINESS.md](REGULATORY_EXAMINATION_READINESS.md) and
[ADR-064](adr/ADR-064-regulatory-examination-readiness.md).

---

## D.66 Data Governance Intelligence — the governance checker (a distinct, deeper layer)

Phase D.66 adds a **dedicated, deeper** Data Governance, Lineage & Information Stewardship **Intelligence**
layer (`app/services/data_governance_intelligence/`, `/data-governance-intelligence`) — distinct from this D.52
layer. Both are read-only views over the SINGLE authoritative D.23 Governance package
(`app/services/governance/`); **neither owns, persists, or duplicates governance data.** The D.66 master
runtime gate is `data_governance_intelligence.enabled` (NOT this layer's `data_governance.enabled`), and its
lineage gate is `lineage_landscape.enabled` (NOT this layer's `lineage.enabled`).

`app/services/data_governance_intelligence/governance.py`'s `validate_data_governance_intelligence()` returns
`{ok, issue_count, findings}` and **never raises**. Invariants enforced:

1. **No persistence / no writes.** No table, no DB write, no outbox publication, no audit write — only reads.
2. **No mutation / no duplicate engine.** `_FORBIDDEN_CALLS` scans for `create_domain(`, `create_element(`,
   `create_rule(`, `record_lineage(`, `record_merge_decision(`, `create_finding(`, `run_check(`,
   `create_retention_assignment(`, `place_legal_hold(`, `execute_deletion(`, `create_case(`, `write_audit(`, …
   — the layer never transforms data, mutates metadata, creates lineage, assigns a steward, executes a quality
   rule, or enforces retention.
3. **No raw environment gating; no second metrics registry.**
4. **Reuses authoritative reads** (`governance.catalog` / `governance.mdm` / `governance.quality` /
   `governance.retention`, incl. `list_domains` + `person_lineage`); explainability enforced.
5. **Registry integrity** — five registries (Data Domain 8 + Lineage 5 + Stewardship 5 + Quality 5 + Retention
   6 = 29 domain entries), unique keys across all registries, configured entries name an authoritative owner,
   derived values labeled, honest `not_configured`.
6. **Gate-collision guard** — a finding is raised if the D.52 gate `data_governance.enabled` ever appears in
   the D.66 `GATES`.

The honesty stance: no fabricated lineage, source systems, stewardship assignments, quality scores, retention
policies, metadata, catalog entries, or data owners; no exposure of sensitive data values / client PII /
confidential metadata / quality-rule internals; **a registered rule is not an executed check, a steward
assignment is not a governance guarantee, a lineage record is not a complete lineage, and coverage is not
certification.** See [`ENTERPRISE_DATA_GOVERNANCE.md`](ENTERPRISE_DATA_GOVERNANCE.md),
[`DATA_DOMAIN_REGISTRY.md`](DATA_DOMAIN_REGISTRY.md), [`DATA_LINEAGE_REGISTRY.md`](DATA_LINEAGE_REGISTRY.md),
[`DATA_STEWARDSHIP_REGISTRY.md`](DATA_STEWARDSHIP_REGISTRY.md), and
[`ADR-071`](adr/ADR-071-data-governance-intelligence.md).
