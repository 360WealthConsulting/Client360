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
