# Household 360 Workspace Actions (Phase D.41)

The Household 360 Workspace performs **no mutation of its own**. Every action is a **deep link into the
authoritative create workflow**, prefilled with the household id (and the primary member for
person-scoped surfaces). The owning domain service remains the sole mutation layer (which scopes,
audits, and publishes any domain event through the existing transactional outbox).

See [`HOUSEHOLD360_WORKSPACE.md`](HOUSEHOLD360_WORKSPACE.md), [`ADR-046`](adr/ADR-046-household360-workspace.md).

## Quick actions

Each is shown only where the principal holds the capability (never shown-then-403) and links into the
authoritative surface prefilled with the household id (or the primary member for person-scoped targets).

| Quick action | Deep link | Capability to show |
|---|---|---|
| Schedule Household Meeting | `/scheduling?household_id={hid}` | scheduling.view |
| Upload Household Document | `/document-library?household_id={hid}` | documents.view |
| Add Household Note | `/people/{primary_member_id}/notes` | client.read |
| Create Task | `/operations/items?household_id={hid}` | work.read |
| Start Tax Work | `/tax/intake?household_id={hid}` | tax.read |
| Create Opportunity | `/opportunities?household_id={hid}` | opportunity.view |
| Start Insurance Case | `/insurance?household_id={hid}` | insurance.read |
| Send Secure Message | `/communications?household_id={hid}` | communications.read |
| Generate Household Meeting Prep | `/workspace/meetings/{primary_member_id}` | client.read |

The actual mutation happens on the target surface, gated by that surface's own write capability + record
scope, and audited/published by the owning service. The Household 360 package adds **no hidden mutation
endpoints**.

## Why deep links (not in-workspace mutation)

- The owning domain service remains the **sole mutation layer** — no duplicate mutation path, no
  double-audit, no duplicate domain event, no new event bus.
- The workspace stays a **read-only composition** — trivially safe to compose across members and domains,
  and governance can prove it never mutates.
- Record scope + policy + runtime for the mutation are enforced by the authoritative surface exactly as
  for any other entry point.

## Prefill rules

- Household-scoped surfaces (scheduling, document-library, operations, tax intake, opportunities,
  insurance, communications) are prefilled with `household_id`.
- Person-scoped surfaces (person notes, meeting prep) are prefilled with the **primary member** id
  (`is_primary` on `household_relationships`).
- A quick action is only offered when a target for its prefill exists (e.g. person-scoped actions are
  hidden if the household has no primary member).

## Item + member deep links

Nothing is a dead end: each member row → `/client/{person_id}`; work items → their owning surface via
`deep_link`; opportunities → `/opportunities/{id}`; compliance reviews → `/compliance/reviews/{id}`;
documents → `/document-library/{id}`; timeline events → their `source_url`; the person workspace links
back to `/client/household/{id}`.
