# Engagement Timeline (Phase D.44)

The unified engagement timeline is a **composed, communication-focused view** over the authoritative
activity timeline. It never replaces or duplicates the timeline service — it composes over it. See
[`COMMUNICATION_ARCHITECTURE.md`](COMMUNICATION_ARCHITECTURE.md) and
[`ADR-049`](adr/ADR-049-unified-communications.md).

## Guarantees
- **Chronological** — newest-first, with a deterministic secondary sort by interaction id for stable order.
- **Deduplicated** — the authoritative projection already dedups by `event_id`; interaction ids are
  source-qualified and unique.
- **Source attribution** — each row carries its `source_system` (authoritative owner) and `interaction_type`.
- **Deep links** — every row links back to the authoritative surface to act (never inline mutation).
- **Pagination** — `page` / `page_size` (capped at 100) over the composed set.
- **Filtering** — by interaction type, unread, action-required, attachment, visibility, direction, source,
  and date window.
- **Search** — free-text, delegated to the authoritative timeline search (via the adapter) then filtered.
- **Person + household aggregation** — person timeline, or household timeline (member-merged by the
  authoritative `household_timeline`).

## Composition
`engagement_timeline(principal, *, person_id|household_id, ...)`:
1. Gate check (`communications.enabled` + `advisor.timeline.enabled` / `household.timeline.enabled`).
2. `adapters.timeline_interactions(...)` → the authoritative `activity_timeline` projection (record-scoped,
   deduped, redacted), classified onto interaction types; non-communication activity dropped.
3. Interaction-attribute filters, sort, paginate.
4. Returns `{enabled, rows, total, page, page_size, pages, suppressed}` — or `None` when out of scope
   (the route returns 404).

The window is the projection's recent set (top ~100 events); deep history stays in the authoritative
timeline. Suppression (filtered count) is reported for transparency.

## Search
`search_interactions(principal, *, person_id|household_id, query, **filters)` — gated by
`engagement.search.enabled`, delegates the text match + scope to the authoritative timeline, then applies
interaction filters. Supported filters: interaction type, unread, action-required, attachment, visibility
(internal / external / both), direction, source; anchored by person or household; date window; free text.

## Summary
`engagement_summary(principal, *, person_id|household_id)` — a compact, low-detail summary (total, unread,
action-required, distribution by type, safe last-interaction descriptor). Backs the Client 360 / Household
360 Communications sections and (through them) AI Assist grounding. Counts only — never message bodies.

## Client (portal) engagement
`portal_engagement(portal_principal)` — the external client's recent-activity surface, composed from the
D.43 portal scoped reads (secure messages, notifications, document requests, appointments). Gated by
`portal.timeline.enabled` (OFF by default — opt-in). Only externally-visible interaction types are produced.

## References
`app/services/communications/engagement/service.py`, `app/services/communications/engagement/adapters/*`,
`app/routes/engagement.py`, `tests/test_unified_communications.py`, ADR-049.
