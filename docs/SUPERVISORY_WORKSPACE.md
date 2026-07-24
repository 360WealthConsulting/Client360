# Supervisory Workspace (Phase D.47)

The supervisory workspace is the enterprise operational view for compliance supervisors, composed read-only
from the authoritative compliance/review/exception/licensing services. See
[`COMPLIANCE_INTELLIGENCE.md`](COMPLIANCE_INTELLIGENCE.md) and
[`ADR-052`](adr/ADR-052-compliance-intelligence.md).

## Access — supervisor-only
Every supervisory surface requires the **`compliance.supervise`** capability (administrator + compliance;
NOT advisor). Routes gate on it (`require_capability("compliance.supervise")`), the engine re-checks it
(`gate.supervisor_authorized`), and the Client 360 / Household 360 sections are capability-gated on it —
three layers of the supervisor-vs-advisor boundary. A principal without it receives 403 / `None` / a
suppressed section.

## The dashboard (`/supervision`)
Shows: open reviews, pending approvals, compliance exceptions, advisor workload, aging/blocked reviews,
documentation gaps, licensing/CE, and each item's authoritative deep link. It composes:
- **reviews** — open `compliance_reviews` needing oversight (`adapters/reviews`);
- **exceptions** — registered compliance exceptions derived from the exception engine + portfolio cadence
  (`adapters/exceptions`) + blocked-review approvals;
- **licensing** — producer license / CE records (`adapters/licensing`, fail-closed);
- **workload** — the authoritative work-queue distribution.

Every action deep-links into the authoritative workflow (`/compliance/reviews`, `/compliance`, `/insurance`,
`/annual-review`, `/work`) — the workspace itself performs no mutation.

## API
- `GET /supervision` (HTML) — the workspace.
- `GET /api/v1/supervision/dashboard` — firm-wide supervisory dashboard.
- `GET /api/v1/supervision/client?person_id=|household_id=` — supervisory client/household view.
- `GET /api/v1/supervision/summary` — compact counts (backs the sections + AI).
- `GET /api/v1/supervision/registry` — the declarative review + exception catalogs.
- `GET /api/v1/supervision/metrics` — low-cardinality metrics.
- `GET /supervision/diagnostics` — internal diagnostics (`observability.audit`).

## Client 360 / Household 360
A supervisor-only **Compliance Oversight** section surfaces the client/household supervisory view (open
reviews + supervisory status + outstanding exceptions). Household aggregates across members and deduplicates.
Advisors never see this section (it is suppressed for them).

## Advisor Workspace (advisor-visible tasks only)
The advisor home carries a **compliance tasks** panel — ONLY the D.46 governed advisor compliance
recommendations, never supervisory findings, reviewer identities, or approval state. This is the
advisor-safe projection.

## AI Assist
AI **summarizes** supervisory counts (open reviews / open exceptions) — and only when the composed section
is present (i.e. the principal is a supervisor), so supervisory facts never reach an advisor. AI never
approves, waives, suppresses, or invents a compliance finding.

## References
`app/routes/compliance_intelligence.py`, `app/services/compliance_intelligence/service.py`,
`app/services/workspace/service.py`, `app/services/ai_assist/context.py`,
`tests/test_compliance_intelligence.py`, ADR-052.
