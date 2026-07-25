# Utilization Registry (Phase D.61)

The **utilization registry** (`UTILIZATION_REGISTRY` in `app/services/capacity_planning/registry.py`) is the
declarative catalog of the capacity layer's utilization / workload / staffing / queue / assignment indicators
and, for each, the **authoritative owner it references**. It is metadata only — every indicator references an
authoritative owner and never computes its own workforce metric.

## Utilization indicators

Each indicator declares `owner`, `runtime_gate`, `capabilities`, `deep_links`, and `config_status`. All five
are configured (each references a real authoritative owner).

| Indicator | Authoritative owner |
| --- | --- |
| `utilization_categories` | operations.capacity |
| `workload_indicators` | work_queue |
| `staffing_indicators` | practice_management |
| `queue_health` | work_queue |
| `assignment_health` | work_queue |

## References authoritative owners only

- **Utilization** is owned by `operations.capacity` (`capacity_overview` — resource count + over-capacity).
- **Workload / queue / assignment health** are owned by the Work Queue (`work_queue.summary`: `my_overdue`,
  `sla_breaches`, `unassigned_team`, `by_domain`). The layer never assigns or modifies work.
- **Staffing indicators** are owned by Practice Management (advisory `staffing_recommendations`). The layer
  never assigns, rebalances, or modifies staffing.

## No fabricated utilization

Every utilization / workload / queue / assignment value comes from an authoritative owner's read. The
`executive_workforce_status` panel is a DERIVED operational summary (labeled `derived`) — configured vs
not_configured domains + firm utilization + queue-health counts. It is never a certified staffing / utilization
figure and never an HR record.

## How the registry is used

The `advisor_utilization` + `operations_utilization` + `queue_health` + `resource_allocation` dashboards
compose `advisor_workload_distribution`, `workload_by_domain`, `utilization_summary`, `queue_health`,
`open_backlog`, `sla_backlog`, `assignment_distribution`, and `automation_workload`. Governance validates
completeness + single ownership (unique keys).

See [ENTERPRISE_CAPACITY_PLANNING.md](ENTERPRISE_CAPACITY_PLANNING.md),
[WORKFORCE_REGISTRY.md](WORKFORCE_REGISTRY.md), [CAPACITY_REGISTRY.md](CAPACITY_REGISTRY.md), and
[ADR-066](adr/ADR-066-capacity-planning.md).
