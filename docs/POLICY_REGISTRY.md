# Policy Registry (Phase D.32)

The **policy registry** (`runtime_policies`, served by `app/services/policy/registry.py`) is the
durable, discoverable catalog of the declarative business-decision policies. The in-code definitions
(`definitions.py`) hold the decision *functions*; the registry row holds the discoverable *metadata*.
Governance reconciles the two.

## Registry fields

| Field | Meaning |
|---|---|
| `code` | Unique policy identifier (e.g. `automation.job_execution`). |
| `category` | The decision area (one of the ten). |
| `status` | `active` (engine-evaluated) · `in_domain` (governed, enforced in the owning domain) · `deprecated` · `retired`. |
| `version` | Policy version (bumped on a semantic change). |
| `owner` | The domain/team that owns the decision. |
| `consumes_feature` / `consumes_config` | The runtime definition the policy consults (blank for whitelist/in-domain policies). |
| `required_capabilities` | RBAC capability codes the decision references (enforcement stays at the call site). |
| `depends_on` | The policy codes this policy composes (the dependency graph). |
| `per_instance` | Unbounded key space (`automation.job.<type>`, `reporting.module.<id>`) — definitions cannot be fully pre-seeded (a compatibility shim). |
| `requires_definition` | Authoritative — the runtime definition must be present (governance flags a gap). |
| `in_domain` | Enforcement stays in the owning domain by documented constraint. |
| `default_decision` | The behavior-preserving legacy default. |

## The seeded policies

### Active (evaluated by the engine — call sites rewired through it)

| Code | Area | Consumes | Depends on | Call site |
|---|---|---|---|---|
| `advisor_workspace.section.work` | advisor_workspace | feature `advisor_workspace.section.work` | — | `advisor_workspace.get_daily_dashboard` |
| `advisor_workspace.section.tasks` | advisor_workspace | feature `advisor_workspace.section.tasks` | `…section.work` | `advisor_workspace.get_daily_dashboard` |
| `advisor_workspace.section.exceptions` | advisor_workspace | feature `advisor_workspace.section.exceptions` | `…section.work` | `advisor_workspace.get_daily_dashboard` |
| `workflow.review_routing` | workflow | (whitelist + optional `workflow.review_template.<code>`) | — | `advisor_workspace.record_meeting_outcome` |
| `automation.job_execution` | automation | feature `automation.job.<type>` (per-instance) | — | `automation.dispatch.execute_dispatch` |
| `reporting.module_eligibility` | reporting | feature `reporting.module.<id>` (per-instance) | — | `reporting.service.list_definitions` |
| `microsoft365.sync_eligibility` | microsoft365 | feature `microsoft365.sync` | — | the three M365 sync jobs |
| `microsoft365.sharepoint_scope` | microsoft365 | config `microsoft365.sharepoint_site_ids` | `…sync_eligibility` | `microsoft_document_sync.discover_drives` |
| `operations.timeline_publish` | operations | (whitelist + optional `operations.timeline_publish.<kind>`) | — | `operations.common.publish_timeline` |

### In-domain (registered + governed; enforcement stays in the owning domain)

| Code | Area | Why enforcement stays in-domain |
|---|---|---|
| `compliance.decision_routing` | compliance | Regulatory approval must stay inside authorized Compliance (architecture invariant: the double-gate on `compliance.review.decide` + a recorded Reviewer Authority + a Rule-Catalog version match). |
| `notification.routing` | notifications | Channel routing is data-driven via the F5.2 provider registry; the F5.5 `notification_dispatch` module is a certified frozen module (never modified). |
| `document.behavior` | documents | Deterministic document CRUD / relationships / retention — no behavioral switch. |
| `scheduling.behavior` | scheduling | Deterministic meeting-lifecycle state machine — enforced in the scheduling service. |

## Coverage (`registry.coverage()`)

- **Decision-area coverage** = areas with a registered policy ÷ ten = **100%** (10/10).
- **Adoption** = active ÷ migratable (`in_domain` excluded as documented exceptions, mirroring how
  D.30 excludes deterministic behaviors) = **100%** (9/9).
- Current: 13 policies — 9 active, 4 in-domain, 0 deprecated, 0 retired.

## Lifecycle

`registry.deprecate(code, reason)` / `registry.retire(code)` transition status and record a firm-level
event (`policy_deprecated` / `policy_retired`) to the D.28 `runtime_events` ledger (entity_type
`policy`). `registry.record_registry_updated()` records a `policy_registry_updated` event. Routine
policy evaluations are never recorded.

## Routes (`/runtime/policy`, reuse the `runtime.*` capabilities)

`GET /runtime/policy` (dashboard, `runtime.view`) · `GET /runtime/policy/registry` · `/adoption` ·
`/graph` (`runtime.view`) · `GET /runtime/policy/governance` · `/events` · `GET /runtime/policy/{code}`
· `/{code}/explain` (`runtime.audit`) · `POST /runtime/policy/governance/validate` ·
`/registry-updated` (`runtime.admin`).
