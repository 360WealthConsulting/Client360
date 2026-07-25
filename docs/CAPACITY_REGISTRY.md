# Capacity Registry (Phase D.61)

The **capacity registry** (`CAPACITY_REGISTRY` in `app/services/capacity_planning/registry.py`) is the
declarative catalog of the capacity layer's capacity categories and, for each, the **authoritative owner**. It
is **metadata only** — the layer owns no capacity plans, no scheduling, and no staffing assignments.

## Capacity categories

Each category declares `owner` (or `not_configured`), `runtime_gate`, `capabilities`, `deep_links`, and
`config_status`.

| Category | Authoritative owner | Config |
| --- | --- | --- |
| `client_capacity` | operations.capacity | configured |
| `meeting_capacity` | **not_configured** | **not_configured** |
| `review_capacity` | work_queue | configured |
| `workflow_capacity` | workflow_automation | configured |
| `operational_capacity` | operations.capacity | configured |
| `onboarding_capacity` | **not_configured** | **not_configured** |
| `tax_season_capacity` | tax_domain | configured |
| `planning_capacity` | **not_configured** | **not_configured** |
| `service_capacity` | operations.capacity | configured |

## The not_configured categories (reported honestly)

**Meeting capacity, onboarding capacity, and planning capacity** have **no authoritative owner in the platform
today** — there is a scheduling owner (record-scoped meeting metadata) but no meeting-*capacity* model, and no
onboarding / planning capacity owner. Rather than fabricate a capacity forecast, those categories are declared
`not_configured` and reported honestly in `capacity_forecast` / `registered_capacity`. When an authoritative
owner is added, the layer composes it — never a second scheduling / PSA system.

## Ownership boundaries (never re-implemented here)

- **Client / operational / service capacity** are owned by `operations.capacity`. The layer never creates or
  changes a capacity plan.
- **Review capacity** is owned by the Work Queue; **workflow capacity** by workflow automation; **tax-season
  capacity** by the tax domain. The layer reads counts only.
- **Meeting scheduling** is owned by the D.19 scheduling platform (record-scoped) — the layer never creates,
  reschedules, or books a meeting, and does not surface calendar contents.

## How the registry is used

The `capacity_planning` dashboard composes `firm_capacity_utilization` (Operations capacity), `capacity_horizon`
(Operations capacity), `capacity_forecast` (DERIVED), and `registered_capacity` (DERIVED). Governance validates
completeness + single ownership + honest not_configured (unique keys).

See [ENTERPRISE_CAPACITY_PLANNING.md](ENTERPRISE_CAPACITY_PLANNING.md),
[WORKFORCE_REGISTRY.md](WORKFORCE_REGISTRY.md), [UTILIZATION_REGISTRY.md](UTILIZATION_REGISTRY.md), and
[ADR-066](adr/ADR-066-capacity-planning.md).
