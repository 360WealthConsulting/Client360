# Enterprise Compliance Intelligence (Phase D.47)

The Compliance Intelligence & Supervisory Operations layer composes the platform's existing compliance
information into one explainable supervisory workspace. It is a governed, **read-only composition** — **not**
a second compliance rules engine, approval engine, audit log, or workflow — and it never mutates. See
[`ADR-052`](adr/ADR-052-compliance-intelligence.md).

## Where it lives
`app/services/compliance_intelligence/` — `registry.py`, `model.py`, `service.py`, `gate.py`, `stats.py`,
`metrics.py`, `diagnostics.py`, `governance.py`, `adapters/{reviews,exceptions,licensing}.py`. Routes:
`app/routes/compliance_intelligence.py`.

## Separation between supervision and execution
The layer strictly separates **supervision** (this read-only composition — it observes, explains, and
prioritizes) from **execution** (the authoritative approval / exception / audit / workflow engines that own
every mutation). It never approves, waives, resolves, or writes the audit log; it deep-links a supervisor to
the authoritative surface where the action is performed under that surface's own gates.

## Composition over duplication — authoritative source map
| Supervisory concern | Authoritative owner | How the layer reads it |
| --- | --- | --- |
| Compliance reviews / approvals | `compliance.reviews` (D.7 double-gated approval engine) | `list_reviews` / `person_reviews` |
| Exceptions (compliance category) | `exception_engine` (single authoritative owner) | `open_exceptions_for_people` |
| Review cadence (overdue / missing beneficiary) | `portfolio` | `accounts_due_for_review` / `accounts_missing_required_beneficiary` |
| Producer licensing / CE | `insurance_licensing` | `list_licenses` |
| Audit trail | `audit_export` (single hash-chain, gated `audit.read`) | reference-only |
| Advisor workload | Unified Work Queue | `work_queue_summary` |

## Supervisor-vs-advisor separation
The supervisor boundary is an explicit read-only capability **`compliance.supervise`** (migration
`n5s6u7p8v9w0`; sensitive; granted to administrator + compliance, NOT advisor). Every supervisory surface
(dashboard, client/household view, the Client 360 / Household 360 **Compliance Oversight** section, the
supervisory AI facts) requires it; a principal without it gets `None` / a suppressed section / no facts.
Advisors get a separate, narrower **advisor compliance tasks** projection — only the D.46 governed advisor
recommendations, never supervisory items, reviewer identities, or approval state. See
[`SUPERVISORY_WORKSPACE.md`](SUPERVISORY_WORKSPACE.md) and [`COMPLIANCE_GOVERNANCE.md`](COMPLIANCE_GOVERNANCE.md).

## Explainability
Every supervisory item / exception carries its explanation (why it is on the supervisor's desk), governing
policy, supporting evidence (references only), authoritative owner, required reviewer / escalation, deep
link, and recommended action. A non-explainable item is never emitted.

## Runtime & policy governance
Gated through the Runtime Engine (`compliance.intelligence.enabled` + `supervision.enabled` +
`supervisor.workspace.enabled`; no env fallback) AND the Policy Engine, alongside the `compliance.supervise`
RBAC check — never bypassing any of them.

## Integration
Client 360 + Household 360 gain a supervisor-only **Compliance Oversight** section; the Advisor Workspace
gains an advisor-visible **compliance tasks** panel; AI Assist **summarizes** supervisory counts (only for a
supervisor) and never approves/waives/suppresses/invents. The client portal is unchanged (no supervisory
functionality; D.43 reuse only). See [`SUPERVISORY_REGISTRY.md`](SUPERVISORY_REGISTRY.md).

## References
`app/services/compliance_intelligence/*`, `app/routes/compliance_intelligence.py`,
`docs/platform_architecture_manifest.yaml`, `tests/test_compliance_intelligence.py`, ADR-052.
