# Household 360 Workspace (Phase D.41)

> **D.44:** Household 360 gains a **Communications** section — the household's unified engagement summary +
> recent interactions, composed by the D.44 engagement layer over the household's authoritative activity
> timeline (member-merged, deduped; never a second store). See
> [`COMMUNICATION_ARCHITECTURE.md`](COMMUNICATION_ARCHITECTURE.md) and
> [`ENGAGEMENT_TIMELINE.md`](ENGAGEMENT_TIMELINE.md).
>
> **D.45:** Household 360 also gains a **Knowledge** section — the household knowledge graph (connected
> businesses, trusts, shared advisors, professionals) with relationship explanations, composed by the D.45
> knowledge layer over each member's authoritative relationship graph (never a graph database). See
> [`KNOWLEDGE_GRAPH.md`](KNOWLEDGE_GRAPH.md) and [`ADR-050`](adr/ADR-050-enterprise-knowledge-graph.md).
>
> **D.46:** Household 360 also gains a **Recommendations** section — household-aggregated (deduplicated across
> members, household-prioritized) explainable recommendations, composed by the D.46 operational-intelligence
> layer over the authoritative recommendation sources (never a second recommendation engine). See
> [`OPERATIONAL_INTELLIGENCE.md`](OPERATIONAL_INTELLIGENCE.md) and [`ADR-051`](adr/ADR-051-operational-intelligence.md).
>
> **D.47:** Household 360 also gains a supervisor-only **Compliance Oversight** section (gated by
> `compliance.supervise`) — the household's compliance status aggregated across members (deduplicated),
> composed by the D.47 compliance-intelligence layer. See [`COMPLIANCE_INTELLIGENCE.md`](COMPLIANCE_INTELLIGENCE.md)
> and [`ADR-052`](adr/ADR-052-compliance-intelligence.md).
>
> **D.48:** Household 360 also gains an executive-only **Executive** section (gated by `analytics.executive`)
> — firm executive context composed by the D.48 executive-intelligence layer over the single Analytics
> Registry. See [`EXECUTIVE_REPORTING.md`](EXECUTIVE_REPORTING.md) and [`ADR-053`](adr/ADR-053-executive-reporting.md).
>
> **D.49:** Household 360 also gains an **Operational Workload** section (gated by `capacity.read`) — a
> household-scoped work-queue count rollup composed by the D.49 practice-management layer (never re-summed
> across incompatible units). See [`PRACTICE_MANAGEMENT.md`](PRACTICE_MANAGEMENT.md) and
> [`ADR-054`](adr/ADR-054-practice-management.md).
>
> **D.50:** Household 360 also gains a **Document Intelligence** section (gated by `documents.view`) —
> aggregated household document status (counts + status by classification/lifecycle, deduped by document id)
> composed by the D.50 document-intelligence layer over the authoritative Document Platform entity read.
> Counts + status only — never document content; never a second DMS. See
> [`DOCUMENT_INTELLIGENCE.md`](DOCUMENT_INTELLIGENCE.md) and [`ADR-055`](adr/ADR-055-document-intelligence.md).
>
> **D.51:** Household 360 also gains an **Automation History** section (gated by `automation.view`) — a
> household-scoped workflow count rollup composed by the D.51 automation-orchestration layer (never a second
> workflow engine). See [`AUTOMATION_ORCHESTRATION.md`](AUTOMATION_ORCHESTRATION.md) and
> [`ADR-056`](adr/ADR-056-automation-orchestration.md).
>
> **D.52:** Household 360 also gains a **Data Governance** section (gated by `governance.view`) — aggregated
> household provenance (source-lineage record counts across members) composed by the D.52 data-governance
> layer over the authoritative person lineage. Counts + source systems only — never a payload; never
> merges/duplicates an identity; never a second master-data store. See [`DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md)
> and [`ADR-057`](adr/ADR-057-data-governance.md).
>
> **D.53:** Household 360 also gains an **External Integrations** section (gated by `integration.view`) — the
> external systems the household's members connected from (source-system provenance across members), composed
> by the D.53 integration-hub layer over the authoritative person lineage. Counts + source-system names only —
> never a payload; never connects/syncs anything; never a second integration platform. See
> [`INTEGRATION_HUB.md`](INTEGRATION_HUB.md) and [`ADR-058`](adr/ADR-058-integration-hub.md).
>
> **D.54:** Household 360 also gains a **Security & Access** section (gated by `security.view`) — who can
> access the household + its members' records (record-assignment access grants), composed by the D.54
> security-operations layer over the authoritative authorization owner. Counts only — never a payload; never
> authenticates/authorizes/alters anything; never a second IAM/RBAC engine. See
> [`SECURITY_OPERATIONS.md`](SECURITY_OPERATIONS.md) and [`ADR-059`](adr/ADR-059-security-operations.md).
>
> **D.55:** Household 360 also gains a **Business Continuity** section (gated by `observability.view`) — the
> firm-level operational resilience posture protecting the household's data, composed by the D.55
> business-continuity layer over the authoritative Observability + Runtime owners. Counts + status only —
> never an infrastructure payload; never backs up/restores/alters anything; never a second
> backup/monitoring/DR engine. See [`BUSINESS_CONTINUITY.md`](BUSINESS_CONTINUITY.md) and
> [`ADR-060`](adr/ADR-060-business-continuity.md).
>
> **D.56:** Household 360 also gains a **Technology Dependencies** section (gated by `integration.view`) — the
> external vendors / systems the household's data depends on, composed by the D.56 vendor-management layer over
> the authoritative Integration Hub per-entity read. Counts + vendor names only — never a payload; never
> modifies a vendor/integration; never a second vendor platform. See
> [`VENDOR_MANAGEMENT.md`](VENDOR_MANAGEMENT.md) and [`ADR-061`](adr/ADR-061-vendor-management.md).
>
> **D.57:** Household 360 also gains a **Financial Relationship** section (gated by `analytics.executive`) —
> the advisory revenue basis (the household members' AUM) the firm relationship rests on, composed by the D.57
> financial-operations layer over the authoritative portfolio owner. Aggregate total only — never a payload;
> per-household fee / commission billing has no authoritative owner (`not_configured`); never bills/invoices/
> posts anything; never a second accounting or billing engine. See
> [`FINANCIAL_OPERATIONS.md`](FINANCIAL_OPERATIONS.md) and [`ADR-062`](adr/ADR-062-financial-operations.md).
>
> **D.58:** Household 360 also gains a **Risk & Controls** section (gated by `compliance.supervise`) —
> authorized member- and household-level risk signals (compliance / documentation / data-quality / integration
> dependencies) aggregated by the D.58 enterprise-risk layer over ONLY the authoritative owners that support
> record scope; shared household findings are deduplicated by composing the household-scoped owner reads.
> Counts + status only — never a payload; never a second GRC/risk engine; an absent signal never certifies
> compliance. See [`ENTERPRISE_RISK_MANAGEMENT.md`](ENTERPRISE_RISK_MANAGEMENT.md) and
> [`ADR-063`](adr/ADR-063-enterprise-risk-management.md).

`GET /client/household/{household_id}` is the **Household 360 Workspace** — open one household and
understand who belongs to it, each member's role and status, the combined operational picture,
member-specific information, shared relationships, current work and deadlines, and where to act. It is a
read-only **COMPOSITION** over the authoritative domain services; it is **not** a second household
database and never the source of truth. It upgrades the D.40 household path in place (bookmark-compatible).

See also: [`ADR-046`](adr/ADR-046-household360-workspace.md), [`HOUSEHOLD360_WORKSPACE_ADAPTERS.md`](HOUSEHOLD360_WORKSPACE_ADAPTERS.md),
[`HOUSEHOLD360_WORKSPACE_ACTIONS.md`](HOUSEHOLD360_WORKSPACE_ACTIONS.md), [`HOUSEHOLD360_WORKSPACE_GOVERNANCE.md`](HOUSEHOLD360_WORKSPACE_GOVERNANCE.md),
[`CLIENT360_WORKSPACE.md`](CLIENT360_WORKSPACE.md).

## Household vs person responsibilities

The **household** workspace summarizes and navigates (context, member directory, rollups, snapshot,
graph). The **person** workspace (`/client/{person_id}`) remains the member-detail surface. Full member
sections are not duplicated in the household screen — each member deep-links to `/client/{person_id}`,
and the person workspace links back to `/client/household/{id}` (reciprocal navigation).

## Record scope + member visibility (fail closed)

- **Boundary:** `record_in_scope(principal, "household", id)` is verified ONCE (404 out of scope).
- **Member visibility:** each roster member is gated by `accessible_person_ids` — the existing rule that
  inherits household→member access (team-aware). A household-assigned advisor sees its members; members
  outside the principal's scope are **suppressed**, not shown. `record_in_scope("person", member_id)` is
  deliberately NOT used for fan-out (it does not inherit household access and would drop every member for
  a household-only advisor). `record.read_all` sees all members.

## Sections

| Section | Authoritative composition | Capability |
|---|---|---|
| Summary | household + primary + `resolve_assignments("household")` + last/next activity | client.read |
| Member Directory | roster (`get_household_portfolio` members) + per-member indicators; deep-links `/client/{id}` | client.read |
| Financial Rollup | **`get_household_portfolio`** total (reused, never re-summed) + per-member AUM + contribution % | client.read |
| Tax | per-member `client_engagement_summary` + open tax exceptions | tax.read |
| Insurance | per-member `client_policy_summary` + `reviews_due_for_people` | insurance.read |
| Benefits | per-member `client_benefits_summary` | benefits.read |
| Opportunities | `opportunities_for_people` (member-attributed) | opportunity.view |
| Documents | `documents_for_entity("household")` ∪ per-member, deduped by document id | documents.view |
| Meetings | household + member calendar-event timeline, deduped, upcoming/previous | client.read |
| Compliance | per-member `person_reviews` + `open_exceptions_for_people` + household count (provenance labelled) | compliance.review.read |
| Unified Work | **D.39** `compose_queue(filters={"household_id": hid})`, member-attributed | work.read |
| Activity Timeline | **`household_timeline`** (merges members, dedups by `event_id`, deterministic) | timeline.read |
| Relationship Graph | per-member one-hop `build_relationship_graph` + memberships, node/edge deduped, depth-capped, cycle-protected | client.read |

## Financial rollup constraints

The household portfolio total **reuses the single authoritative `get_household_portfolio` aggregation**
(never re-summing member portfolios). Per-member AUM + `member_contribution = member_aum / household_aum`
(zero-guarded) are shown. **Insurance face, opportunity revenue, benefit values, and tax figures are
presented side by side and NEVER summed** into assets. Banking, retirement accounts, outside assets,
liabilities, and **net worth** are not modelled — shown as **"Not tracked"**, never fabricated. There is
no composite household score.

## Timeline deduplication

The household timeline reuses `household_timeline`, which already merges the household + members, dedups
by `event_id`, and orders deterministically. A defensive composition-layer dedup pass reports a
`dedup_count`. No timeline rows are written from the workspace.

## Relationship graph

Composed from each member's **one-hop** `build_relationship_graph` + household memberships, with node/
edge **dedup**, a **depth cap** (one hop), and **cycle protection** (key-deduped nodes/edges). Read-only;
never creates or mutates a relationship (no new relationship engine).

## Snapshot

`GET /client/household/{id}/snapshot` (AI-ready JSON, same security as the page): household name, primary
member, member count, active members, portfolio assets, open work, open opportunities, upcoming meetings,
compliance items, connected businesses/estate entities. Incompatible figures side by side — never a
composite score.

## Quick actions

Household-aware deep links into the authoritative create workflow (prefilled with the household id, and
the primary member for person-scoped surfaces): Schedule Household Meeting, Upload Household Document, Add
Household Note, Create Task, Start Tax Work, Create Opportunity, Start Insurance Case, Send Secure
Message, Generate Household Meeting Prep. The workspace never mutates — see
[`HOUSEHOLD360_WORKSPACE_ACTIONS.md`](HOUSEHOLD360_WORKSPACE_ACTIONS.md).

## Routes / diagnostics / capabilities

- `GET /client/household/{id}` — the Household 360 workspace (role-aware tabs).
- `GET /client/household/{id}/snapshot` — AI-ready household snapshot JSON.
- `GET /client/household/{id}/diagnostics` — composition diagnostics + governance (`observability.audit`).

**No migration, no new table, no new projection, no new capability.** Migration head unchanged.
Diagnostics report per-section timing, member count, scoped member count, suppressed members, sections
built/suppressed, failed adapters, stale sources, timeline dedup count, graph node/edge counts, cycle
protection, record-scope validation, and quick-action availability.
