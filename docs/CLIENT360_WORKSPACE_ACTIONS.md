# Client 360 Workspace Actions (Phase D.40)

The Client 360 Workspace performs **no mutation of its own**. Every action is a **deep link into the
authoritative create workflow**, prefilled with the client's id. This keeps the workspace a pure
composition surface and the owning domain service the sole mutation layer (which scopes, audits, and
publishes any domain event through the existing transactional outbox).

See [`CLIENT360_WORKSPACE.md`](CLIENT360_WORKSPACE.md), [`ADR-045`](adr/ADR-045-client360-workspace.md).

## Quick actions

Each quick action is shown only where the principal holds the capability (never shown-then-403) and
links into the authoritative surface with `?person_id=` / `?household_id=` prefill.

| Quick action | Deep link (authoritative surface) | Capability to show |
|---|---|---|
| Schedule Meeting | `/scheduling?person_id={id}` | scheduling.view |
| Upload Document | `/document-library?person_id={id}` | documents.view |
| Add Note | `/people/{id}/notes` | client.read |
| Create Task | `/operations/items?person_id={id}` | work.read |
| Start Tax Return | `/tax/intake?person_id={id}` | tax.read |
| Create Opportunity | `/opportunities?person_id={id}` | opportunity.view |
| Start Insurance Case | `/insurance?person_id={id}` | insurance.read |
| Send Secure Message | `/communications?person_id={id}` | communications.read |
| Generate Meeting Prep | `/workspace/meetings/{id}` | client.read |

The actual mutation happens on the target surface, gated by that surface's own write capability +
record scope, and audited/published by the owning service. The Client 360 Workspace never assigns,
completes, approves, or otherwise mutates a business record.

## Why deep links (not in-workspace mutation)

- The owning domain service remains the **sole mutation layer** — no duplicate mutation path, no
  double-audit, no duplicate domain event.
- The workspace stays a **read-only composition** — trivially safe to compose across 12 domains, and
  governance can prove it never mutates.
- Record scope + policy + runtime for the mutation are enforced by the authoritative surface exactly as
  they are for any other entry point.

## Item deep links (open source record)

Every composed item also deep-links to its source record so nothing is a dead end: documents →
`/document-library/{id}`, opportunities → `/opportunities/{id}`, compliance reviews →
`/compliance/reviews/{id}`, exceptions → `/exceptions/{id}`, advisor work → `/advisor-work/{id}`,
timeline events → their `source_url`, household members → `/client/{person_id}`.
