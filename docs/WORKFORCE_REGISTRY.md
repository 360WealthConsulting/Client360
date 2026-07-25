# Workforce Registry (Phase D.61)

The **workforce registry** (`WORKFORCE_REGISTRY` in `app/services/capacity_planning/registry.py`) is the
declarative catalog of the capacity layer's workforce classes and, for each, the **authoritative owner**. It is
metadata only — the layer owns no employee directory and never exposes employee details (counts only).

## Workforce classes

Each class declares `owner` (or `not_configured`), `runtime_gate`, `capabilities`, `deep_links`, and
`config_status`.

| Class | Authoritative owner | Config |
| --- | --- | --- |
| `advisors` | operations.capacity | configured |
| `tax_professionals` | operations.capacity | configured |
| `insurance_professionals` | operations.capacity | configured |
| `operations_staff` | operations.capacity | configured |
| `administrative_staff` | operations.capacity | configured |
| `contractors` | **not_configured** | **not_configured** |
| `automation_workers` | automation_orchestration | configured |
| `shared_resources` | operations.capacity | configured |

## Owner choice: capacity resources, not an HR directory

The workforce classes are owned (for a **capacity** view) by the **Operations capacity owner**
(`operations.capacity`), which holds the resource records with department + committed/declared capacity. The
platform also has an Identity owner (`users` / `teams`), but D.61 deliberately composes the capacity owner's
**counts** rather than the Identity directory's rows — so **no employee details / PII are ever exposed**.
Automation workers are owned by Automation Orchestration.

## The not_configured class (reported honestly)

**Contractors** have **no dedicated authoritative owner** (no HR / contractor directory that a capacity view
should surface). Rather than fabricate a contractor roster, the class is declared `not_configured` and reported
honestly in `workforce_inventory` / `registered_workforce`. When an authoritative HR / contractor owner is
added, the layer composes counts from it — never an HR platform.

## Ownership boundaries (never re-implemented here)

- **Resources / capacity** are owned by `operations.capacity`. The layer never creates a resource, assigns a
  role, or changes capacity.
- **Automation workers** are owned by Automation Orchestration. The layer never launches a worker.
- **The employee/team directory** (`identity`) and **assignments** (`object_security.resolve_assignments`)
  remain the authoritative owners; the layer reads counts only, never employee details.

## How the registry is used

The `workforce_overview` + `staffing_readiness` dashboards compose `workforce_inventory` (DERIVED),
`registered_workforce` (DERIVED), and `staffing_readiness` (DERIVED). Governance validates that every class
declares its fields, that every **configured** class names an authoritative owner, and that keys are unique.

See [ENTERPRISE_CAPACITY_PLANNING.md](ENTERPRISE_CAPACITY_PLANNING.md),
[CAPACITY_REGISTRY.md](CAPACITY_REGISTRY.md), [UTILIZATION_REGISTRY.md](UTILIZATION_REGISTRY.md), and
[ADR-066](adr/ADR-066-capacity-planning.md).
