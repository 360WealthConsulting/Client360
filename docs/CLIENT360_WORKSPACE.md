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
>
> **D.49:** Client 360 also gains an **Operational Workload** section (gated by `capacity.read`) — a compact
> per-client work-queue rollup composed by the D.49 practice-management layer (never a second work engine).
> See [`PRACTICE_MANAGEMENT.md`](PRACTICE_MANAGEMENT.md) and [`ADR-054`](adr/ADR-054-practice-management.md).
>
> **D.50:** Client 360 also gains a **Document Intelligence** section (gated by `documents.view`) — a compact
> per-client document rollup (counts + status by classification/lifecycle + open documentation gaps) composed
> by the D.50 document-intelligence layer over the authoritative Document Platform + Compliance Intelligence.
> Counts + status only — never document content; never a second DMS. See
> [`DOCUMENT_INTELLIGENCE.md`](DOCUMENT_INTELLIGENCE.md) and [`ADR-055`](adr/ADR-055-document-intelligence.md).
>
> **D.51:** Client 360 also gains an **Automation History** section (gated by `automation.view`) — a compact
> per-client workflow rollup (counts + status) composed by the D.51 automation-orchestration layer over the
> Workflow Orchestration facade (never a second workflow engine). See
> [`AUTOMATION_ORCHESTRATION.md`](AUTOMATION_ORCHESTRATION.md) and [`ADR-056`](adr/ADR-056-automation-orchestration.md).
>
> **D.52:** Client 360 also gains a **Data Governance** section (gated by `governance.view`) — a compact
> per-client provenance rollup (source-lineage record count + confirmed links + source systems) composed by
> the D.52 data-governance layer over the authoritative person lineage (`governance.mdm.person_lineage`,
> reading `person_source_links`). Counts + source systems only — never a payload; never merges/alters an
> identity; never a second master-data store. See [`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md) and
> [`ADR-057`](adr/ADR-057-data-governance.md).
>
> **D.53:** Client 360 also gains an **External Integrations** section (gated by `integration.view`) — the
> external systems the client's data connected from (source-system provenance), composed by the D.53
> integration-hub layer over the authoritative person lineage. Counts + source-system names only — never a
> payload; never connects/syncs anything; never a second integration platform. See
> [`INTEGRATION_HUB.md`](INTEGRATION_HUB.md) and [`ADR-058`](adr/ADR-058-integration-hub.md).
>
> **D.54:** Client 360 also gains a **Security & Access** section (gated by `security.view`) — who can access
> this client's record (record-assignment access grants), composed by the D.54 security-operations layer over
> the authoritative authorization owner. Counts only — never a payload; never authenticates/authorizes/alters
> anything; never a second IAM/RBAC engine. See [`SECURITY_OPERATIONS.md`](SECURITY_OPERATIONS.md) and
> [`ADR-059`](adr/ADR-059-security-operations.md).
>
> **D.55:** Client 360 also gains a **Business Continuity** section (gated by `observability.view`) — the
> firm-level operational resilience posture (resilience score + infrastructure availability + backup
> coverage) protecting the client's data, composed by the D.55 business-continuity layer over the
> authoritative Observability + Runtime owners. Counts + status only — never an infrastructure payload; never
> backs up/restores/alters anything; never a second backup/monitoring/DR engine. See
> [`BUSINESS_CONTINUITY.md`](BUSINESS_CONTINUITY.md) and [`ADR-060`](adr/ADR-060-business-continuity.md).
>
> **D.56:** Client 360 also gains a **Technology Dependencies** section (gated by `integration.view`) — the
> external vendors / systems the client's data depends on, composed by the D.56 vendor-management layer over
> the authoritative Integration Hub per-entity read. Counts + vendor names only — never a payload; never
> modifies a vendor/integration; never a second vendor platform. See
> [`VENDOR_MANAGEMENT.md`](VENDOR_MANAGEMENT.md) and [`ADR-061`](adr/ADR-061-vendor-management.md).
>
> **D.57:** Client 360 also gains a **Financial Relationship** section (gated by `analytics.executive`) — the
> advisory revenue basis (the client's AUM) the firm relationship rests on, composed by the D.57
> financial-operations layer over the authoritative portfolio owner. Aggregate total only — never a payload;
> per-client fee / commission billing has no authoritative owner (`not_configured`) and is never fabricated;
> never bills/invoices/posts anything; never a second accounting or billing engine. See
> [`FINANCIAL_OPERATIONS.md`](FINANCIAL_OPERATIONS.md) and [`ADR-062`](adr/ADR-062-financial-operations.md).
>
> **D.58:** Client 360 also gains a **Risk & Controls** section (gated by `compliance.supervise`) —
> client-relevant, authorized risk signals (open compliance exceptions, documentation gaps, data-quality
> issues, integration dependencies) composed by the D.58 enterprise-risk layer over ONLY the authoritative
> owners that support per-client record scope. Firm-wide incidents / findings are never exposed here. Counts +
> status only — never a payload; never a second GRC/risk engine; an absent signal never certifies compliance.
> See [`ENTERPRISE_RISK_MANAGEMENT.md`](ENTERPRISE_RISK_MANAGEMENT.md) and
> [`ADR-063`](adr/ADR-063-enterprise-risk-management.md).
>
> **D.59:** Client 360 also gains an **Evidence & Supervisory Readiness** section (gated by
> `compliance.supervise`) — client-relevant, authorized evidence signals (documentation completeness, open
> client-specific compliance exceptions, suitability / replacement / workflow-approval evidence via open
> reviews) composed by the D.59 regulatory-readiness layer over ONLY the authoritative owners that support
> per-client record scope. Firm-wide examination posture, firm-wide incidents, unrelated supervisory findings,
> other clients' evidence, and confidential regulator information are never exposed. Counts + status only —
> never a payload; never a second compliance/evidence engine; operational readiness is not regulatory
> certification. See [`REGULATORY_EXAMINATION_READINESS.md`](REGULATORY_EXAMINATION_READINESS.md) and
> [`ADR-064`](adr/ADR-064-regulatory-examination-readiness.md).
>
> **D.60:** Client 360 also gains an **Operational Impact** section (gated by `observability.view`) — the
> external services / vendors the client's data depends on, composed by the D.60 operational-resilience layer
> over ONLY the genuinely record-scoped Integration Hub per-entity read. **Firm-wide operational information
> (incidents, alerts, service health) is never exposed at client scope**; per-client incident impact has no
> authoritative owner (not_configured). Counts only — never a payload; never a second incident/monitoring
> engine; operational posture is not a certification that production is healthy. See
> [`ENTERPRISE_OPERATIONAL_RESILIENCE.md`](ENTERPRISE_OPERATIONAL_RESILIENCE.md) and
> [`ADR-065`](adr/ADR-065-operational-resilience.md).
>
> **D.61:** Client 360 also gains a **Servicing Team** section (gated by `capacity.read`) — ONLY the
> record-scoped staffing directly related to servicing this client (who is assigned to the record), composed by
> the D.61 capacity-planning layer over the authoritative authorization owner
> (`object_security.resolve_assignments`). **Employee workload, firm utilization, and unrelated staffing data
> are never exposed at client scope.** Counts only — never an employee detail; never a second HR/scheduling
> engine; an operational summary, never an HR record. See
> [`ENTERPRISE_CAPACITY_PLANNING.md`](ENTERPRISE_CAPACITY_PLANNING.md) and
> [`ADR-066`](adr/ADR-066-capacity-planning.md).
>
> **D.62:** Client 360 also gains a **Documentation** section (gated by `documents.view`) — ONLY the
> record-scoped documentation relevant to servicing this client (this client's document count + documentation
> gaps), composed by the D.62 knowledge-management layer over the authoritative Document Intelligence per-entity
> read. **Internal SOPs, unrelated documentation, confidential operational procedures, and firm-wide
> documentation metrics are never exposed at client scope.** Counts + status only — never document contents;
> never a second wiki/DMS; a documentation-coverage summary, never fabricated knowledge. See
> [`ENTERPRISE_KNOWLEDGE_MANAGEMENT.md`](ENTERPRISE_KNOWLEDGE_MANAGEMENT.md) and
> [`ADR-067`](adr/ADR-067-knowledge-management.md).

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
