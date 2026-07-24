# Client 360 Workspace (Phase D.40)

> **D.44:** Client 360 gains a **Communications** section — a unified engagement summary + recent
> interactions across every channel, composed by the D.44 engagement layer over the authoritative
> subsystems (never a second store). See [`COMMUNICATION_ARCHITECTURE.md`](COMMUNICATION_ARCHITECTURE.md)
> and [`ENGAGEMENT_TIMELINE.md`](ENGAGEMENT_TIMELINE.md).
>
> **D.45:** Client 360 also gains a **Knowledge** section — connected entities (households, businesses,
> trusts, professionals, advisors, connected records) with an explanation of each relationship, composed by
> the D.45 knowledge layer over the authoritative relationship engine (never a graph database, never a second
> store). See [`KNOWLEDGE_GRAPH.md`](KNOWLEDGE_GRAPH.md) and [`ADR-050`](adr/ADR-050-enterprise-knowledge-graph.md).
>
> **D.46:** Client 360 also gains a **Recommendations** section — client-specific explainable recommendations
> (missing reviews, outstanding requests, planning opportunities, communication follow-up, compliance tasks),
> composed by the D.46 operational-intelligence layer over the authoritative recommendation sources (never a
> second recommendation engine, no ML). See [`OPERATIONAL_INTELLIGENCE.md`](OPERATIONAL_INTELLIGENCE.md) and
> [`ADR-051`](adr/ADR-051-operational-intelligence.md).
>
> **D.47:** Client 360 also gains a supervisor-only **Compliance Oversight** section (gated by
> `compliance.supervise`) — the client's open reviews, supervisory status, and outstanding exceptions,
> composed by the D.47 compliance-intelligence layer (never a second compliance engine). Advisors without the
> capability never see it. See [`COMPLIANCE_INTELLIGENCE.md`](COMPLIANCE_INTELLIGENCE.md) and
> [`ADR-052`](adr/ADR-052-compliance-intelligence.md).
>
> **D.48:** Client 360 also gains an executive-only **Executive** section (gated by `analytics.executive`) —
> firm executive context (KPIs + firm-intelligence observations) composed by the D.48 executive-intelligence
> layer over the single Analytics Registry (never a second analytics engine). See
> [`EXECUTIVE_REPORTING.md`](EXECUTIVE_REPORTING.md) and [`ADR-053`](adr/ADR-053-executive-reporting.md).

`GET /client/{id}` is the **master client record** — the primary operational screen. Open a person (or
household) and see, and act on, the whole client picture from one place. It is a **read-only COMPOSITION
surface** over the authoritative domain services; it is **not** a second client database and never the
source of truth. Every edit deep-links into the authoritative create workflow.

See also: [`ADR-045`](adr/ADR-045-client360-workspace.md), [`CLIENT360_WORKSPACE_ADAPTERS.md`](CLIENT360_WORKSPACE_ADAPTERS.md),
[`CLIENT360_WORKSPACE_ACTIONS.md`](CLIENT360_WORKSPACE_ACTIONS.md), [`CLIENT360_WORKSPACE_GOVERNANCE.md`](CLIENT360_WORKSPACE_GOVERNANCE.md).

## Invariants

- **Composition, not a new engine.** No second client database, no duplicated business logic, no shadow
  client record, no new table, no new projection. Each section reuses ONE authoritative domain read.
- **Never mutates.** Every edit is a deep link into the authoritative create workflow; the workspace
  only reads.
- **Record scope verified once at the boundary.** Enforcement across the domain reads is uneven, so
  `record_in_scope(principal, entity_type, id)` is checked ONCE up front (404 out of scope), then
  sections fan out. A section the principal lacks capability for is omitted (never shown-then-403);
  sections fail closed.
- **Runtime / Policy / RBAC / record scope / audit / outbox unchanged** — the workspace reads only.
- **Unmodelled concepts are honest.** Banking, retirement accounts, outside assets, liabilities, net
  worth, and client status/tier/risk are not modelled in the platform — surfaced as "not tracked",
  never fabricated.

## Layout (12 sections)

| Section | Reuses (authoritative) | Capability |
|---|---|---|
| Summary | `get_client_snapshot` + `resolve_assignments` + members + last/next activity (timeline) | client.read |
| Financial | `get_person_portfolio`/`get_household_portfolio` (single `aggregate_portfolio` math) + insurance face + benefit relationships — **side by side, never summed** | client.read |
| Tax | `client_engagement_summary` + open tax exceptions | tax.read |
| Insurance | `client_policy_summary` + `reviews_due_for_people` (renewals) | insurance.read |
| Benefits | `client_benefits_summary` | benefits.read |
| Opportunities | `opportunities_for_person` + reused Advisor Intelligence recommendations | opportunity.view |
| Documents | `documents_for_entity` | documents.view |
| Meetings | calendar-event timeline (`recent_events`) — upcoming + previous | client.read |
| Compliance | `person_reviews` + annual-review session/history + `open_exceptions_for_client` | compliance.review.read |
| Activity | `client_timeline`/`household_timeline` (references only — never duplicates event storage) | timeline.read |
| Relationships | `build_relationship_graph` + `get_person_households` + `resolve_assignments` (read-only graph) | client.read |
| Work | `person_work` (open advisor work) | advisor_work.read |

Financial breadth is bounded by what the platform models (portfolio AUM/cash/allocation + insurance face
+ benefit relationships). Banking / retirement accounts / outside assets / liabilities / net worth do
not exist as domains and are shown as "not tracked".

## Client Snapshot

A compact executive summary (page header + AI-ready JSON at `GET /client/{id}/snapshot`): assets (AUM,
cash, household AUM), revenue (open pipeline), tax, insurance, compliance, upcoming deadlines, open
work, last communication, next activity. **Never summed into a single composite** (units differ).

## Relationship graph (read-only)

`build_relationship_graph(person_id)` grouped by category — **family** (spouse/child/parent/sibling),
**business** (owner/employer/employee/partner), **professional** (CPA/attorney/advisor/banker),
**estate** (trustee/executor/beneficiary/POA), plus household members and assigned advisors/team. Nodes
that are people deep-link to their own `/client/{id}`.

## Quick actions (deep links)

Schedule Meeting, Upload Document, Add Note, Create Task, Start Tax Return, Create Opportunity, Start
Insurance Case, Send Secure Message, Generate Meeting Prep — each a deep link into the authoritative
create surface, prefilled with the client's id, shown only where the capability is held. See
[`CLIENT360_WORKSPACE_ACTIONS.md`](CLIENT360_WORKSPACE_ACTIONS.md).

## Routes

- `GET /client/{person_id}` — person workspace (role-aware tabs, `?tab=`).
- `GET /client/{person_id}/snapshot` — AI-ready snapshot JSON.
- `GET /client/{person_id}/diagnostics` — composition diagnostics + governance (`observability.audit`).
- `GET /client/household/{household_id}` (+ `/snapshot`, `/diagnostics`) — the **Household 360 Workspace**
  (Phase D.41): member directory + member-level rollups + household relationship graph + snapshot. The
  person workspace remains the member-detail surface; the two navigate reciprocally. See
  [`HOUSEHOLD360_WORKSPACE.md`](HOUSEHOLD360_WORKSPACE.md), [`ADR-046`](adr/ADR-046-household360-workspace.md).

## Diagnostics

`GET /client/{id}/diagnostics` reports composition timings (per section + total), sections built,
suppressed capabilities, missing adapters, stale (errored) sources, record-scope validation, and
projection/fallback usage (per-client reads are authoritative composition — not projection-backed).

## Capabilities / migration

**No migration, no new table, no new capability.** The page reuses `client.read`; each section tab
reuses its domain read capability; diagnostics reuse `observability.audit`. Migration head is unchanged.
