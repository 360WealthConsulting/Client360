# Household 360 Workspace (Phase D.41)

> **D.44:** Household 360 gains a **Communications** section — the household's unified engagement summary +
> recent interactions, composed by the D.44 engagement layer over the household's authoritative activity
> timeline (member-merged, deduped; never a second store). See
> [`COMMUNICATION_ARCHITECTURE.md`](COMMUNICATION_ARCHITECTURE.md) and
> [`ENGAGEMENT_TIMELINE.md`](ENGAGEMENT_TIMELINE.md).

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
