# Resource Registry (Phase D.49)

The **resource registry** (`RESOURCE_REGISTRY` in `app/services/practice_management/registry.py`) is the
declarative catalog of the firm's resource classes and, for each, the **authoritative source** it is measured
against. It is metadata only: the Practice Management layer owns no roster, no assignment store, and no
schedule — it references the owners.

## Resource classes

| Resource | Capabilities | Workload source | Assignment source | Scheduling source | Utilization source | Availability source |
| --- | --- | --- | --- | --- | --- | --- |
| `advisors` | `advisor_work.read`, `client.read` | `work_queue.compose_queue` | `work_management.assign_work` | `scheduling.availability` | `operations.capacity.resource_utilization` | `scheduling.availability` |
| `tax_preparers` | `tax.read` | `tax_domain.dashboard` | `record_assignments (tax_return)` | `scheduling.availability` | `operations.capacity.resource_utilization` | `scheduling.availability` |
| `reviewers` | `compliance.supervise` | `compliance_intelligence.supervisory_dashboard` | `compliance.reviews` | `scheduling.availability` | `operations.capacity.resource_utilization` | `scheduling.availability` |
| `operations` | `operations.view` | `operations.capacity.capacity_overview` | `operations.tasks` | `scheduling.availability` | `operations.capacity.resource_utilization` | `scheduling.availability` |
| `compliance` | `compliance.review.read` | `work_queue.compose_queue` | `compliance.reviews` | `scheduling.availability` | `operations.capacity.resource_utilization` | `scheduling.availability` |
| `administrative_staff` | `work.read` | `work_queue.compose_queue` | `work_management.assign_work` | `scheduling.availability` | `operations.capacity.resource_utilization` | `scheduling.availability` |

## Ownership boundaries (never re-implemented here)

- **Identity / roster** is owned by `app/services/identity.py` (`list_identity_data`, teams, roles). The
  registry names `identity` as each resource's `owner`; the layer never seeds or edits a roster.
- **Assignment** is owned by `app/services/work_management.py` (`assign_work`, assignment roles) and the
  `record_assignments` table. The registry names the assignment source for explainability; the Practice
  Management layer **never calls** `assign_work` / `reassign_*` — governance forbids it.
- **Scheduling / availability** is owned by `app/services/scheduling/`. The registry names it as the
  scheduling + availability source; the layer never books or edits a meeting.
- **Utilization** is owned by `operations.capacity.resource_utilization` — the single, deterministic
  utilization owner (see [CAPACITY_PLANNING.md](CAPACITY_PLANNING.md)).

## How the registry is used

Resource classes drive the resource dashboards (advisor/department utilization, staffing) and the
`/api/v1/practice/registry` catalog endpoint. Governance validates that every resource declares all seven
fields (owner, capabilities, workload, assignment, scheduling, utilization, availability), that keys are
unique, and that the layer contains no call into an assignment/scheduling **mutation**. The Practice
Management layer surfaces per-resource utilization and staffing **signals** (advisory only) — the firm acts
by opening the authoritative surface via the panel's deep link.

See [PRACTICE_MANAGEMENT.md](PRACTICE_MANAGEMENT.md), [PRACTICE_GOVERNANCE.md](PRACTICE_GOVERNANCE.md), and
[ADR-054](adr/ADR-054-practice-management.md).
