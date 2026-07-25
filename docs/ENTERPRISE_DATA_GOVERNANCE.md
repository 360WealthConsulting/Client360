# Enterprise Data Governance, Lineage & Information Stewardship Intelligence (Phase D.66)

`app/services/data_governance_intelligence/` is a governed, **read-only composition** that provides a unified,
governed view of the firm's data-governance posture — enterprise data inventory, source-of-truth coverage,
lineage coverage, stewardship coverage, quality-rule coverage, retention coverage, governance readiness,
data-risk indicators, and governance gaps. It is **not** a second data catalog, metadata repository, ETL
platform, MDM platform, warehouse, governance platform, lineage engine, or quality engine: **no new capability,
no new metric, no persistence, no mutation, no duplicated metadata / lineage / governance data, no migration**
(single Alembic head `n5s6u7p8v9w0`).

> These are **governance-readiness summaries**, never a repaired dataset, a created lineage edge, an assigned
> steward, an executed quality rule, or an enforced retention decision. **A registered rule is not an executed
> check, a steward assignment is not a governance guarantee, a lineage record is not a complete lineage, and
> coverage is not certification.**

> **No secrets, ever.** No sensitive data value, client PII, credential, secret, token, confidential metadata,
> internal governance note, or quality-rule internal is ever carried in a panel — counts, coverage, status, and
> ratios only.

> **Distinct from the D.52 Data Governance layer** (`/data-governance`). Both are read-only views over the
> SINGLE authoritative D.23 Governance package; neither owns or duplicates governance data. The D.66 master
> gate is `data_governance_intelligence.enabled` — NOT D.52's `data_governance.enabled`.

## What it composes (existing owners only)

| Signal | Authoritative owner | Composed read | Capability |
| --- | --- | --- | --- |
| Data domains / elements / quality rules / survivorship | Governance catalog (D.23) | `list_domains` / `list_elements` / `list_rules` / `list_survivorship_rules` | `governance.view` |
| Stewardship (steward_user_id) | Governance catalog | `list_domains` (steward_user_id) | `governance.view` |
| Lineage / source-system provenance | Governance MDM | `list_lineage` / `person_lineage` | `governance.view` |
| MDM merge candidates | Governance MDM | `list_candidates` | `governance.view` |
| Data-quality findings | Governance Quality | `metrics` | `governance.view` |
| Retention assignments / legal holds / deletions / cases | Governance Retention | `list_retention_assignments` / `metrics` / `list_cases` | `governance.view` |

## The not_configured domains (reported honestly)

The D.66 audit confirmed several domains have **no authoritative owner** and are declared `not_configured`,
never fabricated: **external data catalog** (Collibra / Alation), **business glossary / data dictionary**,
**data-classification taxonomy**, **automated column-level lineage** (MDM provenance is source-system level),
**data-sharing agreements / contracts**, **data-quality scorecards / SLAs**, **a retention-policy catalog
beyond the Document Platform**, **DPIA**, and **data-product ownership / golden records**.

## Registries, panels, dashboards

Five declarative registries — Data Domain (8) + Lineage (5) + Stewardship (5) + Quality (5) + Retention (6) =
29 domain entries (19 configured, 10 not_configured) — plus 29 panels and 8 dashboards (enterprise_data_inventory,
lineage_landscape, stewardship_coverage, data_quality_coverage, retention_coverage, executive_data_governance,
governance_readiness, data_risk_overview). See `DATA_DOMAIN_REGISTRY.md`, `DATA_LINEAGE_REGISTRY.md`, and
`DATA_STEWARDSHIP_REGISTRY.md`.

Each panel is **explainable** (explanation + source + deep link — a hard emit gate) and self-restricts to its
authoritative-source capability. A principal lacking the panel capability sees `restricted` (never the value or
count). A panel whose owner is `not_configured` is emitted `available=False` with
`config_status='not_configured'` — fail closed. **Derived** panels (executive_data_governance_posture,
governance_readiness, data_risk_indicators, the coverage panels) carry `derived=True` and describe governance
readiness only.

## Engine + surfaces

`service.py` exposes `compose_dashboard`, `list_dashboards`, `get_panel`, `data_governance_summary`, and the
record-scoped `client_data_governance` / `household_data_governance`. Dashboard-level authorization admits **a
governance viewer OR an executive** (`governance.view` / `analytics.executive`, via `require_any_capability`).

- **Advisor Workspace** — a Data Governance Status panel (`data_governance_summary` in
  `workspace/service.py`), self-gated to `governance.view`.
- **Client 360 / Household 360** — a Data Lineage & Provenance section that composes ONLY the record-scoped
  source-system provenance from the authoritative Governance MDM owner (`person_lineage`), exposing the
  provenance record count + distinct source-system names. **No internal governance notes, confidential
  metadata, quality-rule internals, system architecture, or platform configuration are ever exposed, and
  governance state is never inferred.**
- **Executive** — an Enterprise Data Governance dashboard reusing existing widgets (compliance_workload +
  operational_health; **no new widget**).
- **AI Assist** — summarize-only grounding: AI may summarize governance coverage / lineage completeness /
  stewardship readiness / quality coverage / retention readiness and a record-scoped source-system count, but
  never invents lineage, fabricates metadata, assigns stewardship, modifies governance, repairs data, or infers
  missing ownership.

## Runtime gates, policy, analytics, diagnostics

- **Runtime gates** (`gate.py`): `data_governance_intelligence.enabled`, `lineage_landscape.enabled`,
  `data_quality_landscape.enabled`, `data_governance_ai_summary.enabled` — all distinct (no reused/unrelated
  gate; specifically NOT the D.52 layer's `data_governance.enabled` / `lineage.enabled`), evaluated through
  `runtime.consumption.feature_enabled` with **no runtime-variable bypass**. The layer also respects the
  runtime gate of every composed source.
- **Policy** composition alongside RBAC (`policy_ok(area)`), never bypassing either.
- **Analytics** (`metrics.py` → `analytics/{sources,metrics}.py`): four low-cardinality operational counters
  (data_governance_dashboards_composed, data_governance_panels_composed, data_governance_panel_failures,
  data_governance_authorization_failures) registered into the ONE Analytics Registry — no second metrics store.
- **Diagnostics** (`diagnostics.py`): an `observability.audit`-only report (gate snapshot, registry coverage,
  panel availability, governance findings).
- **Governance** (`governance.py`): `validate_data_governance_intelligence()` returns `{ok, issue_count,
  findings}` and never raises — see the D.66 section of `DATA_GOVERNANCE.md`.

## What it never does

No ETL, no synchronization, no catalog mutation, no lineage mutation, no stewardship assignment, no quality-rule
execution, no retention management, no persistence, no second metrics registry, no fabricated lineage / source
systems / stewardship / quality score / retention policy / metadata / catalog entry / data owner, and no
exposure of any sensitive data value, client PII, confidential metadata, or quality-rule internal.

## References
- Code: `app/services/data_governance_intelligence/*`, `app/routes/data_governance_intelligence.py`,
  `app/templates/data_governance_intelligence/home.html`
- Surfaces: `app/services/workspace/service.py`, `app/services/client360/{registry,sections}.py`,
  `app/services/client360/household.py`, `app/services/executive_intelligence/registry.py`,
  `app/services/ai_assist/context.py`, `app/services/analytics/{sources,metrics}.py`
- Tests: `tests/test_data_governance_intelligence.py`; ADR-071; `docs/DATA_DOMAIN_REGISTRY.md`,
  `docs/DATA_LINEAGE_REGISTRY.md`, `docs/DATA_STEWARDSHIP_REGISTRY.md`, `docs/DATA_GOVERNANCE.md`
