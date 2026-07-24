# ADR-049 — Unified Communications & Client Engagement Layer: Composition, Not a Second Messaging or Timeline System

## Status
Accepted

## Date
2026-07-23

## Decision owners
Platform Architecture; Domain Owner (Client Experience / Communications); Reliability / Operations;
Security / Authorization (RBAC ownership); Compliance; Business Operations Owner (Michael Shelton).

## Context
The mandatory D.44 audit found that the platform already has every communication primitive it needs, each
owned by an authoritative subsystem:

* **Timeline system of record** — `app/services/timeline.py` writing `timeline_events`; and a **read-only
  cross-domain projection** `app/services/activity_timeline/` that already composes multiple domains with an
  adapter architecture (scoped via `record_in_scope`, deduplicated by `event_id`, ordered, redacted,
  paginated). Most interaction types already flow into `timeline_events`: secure messages
  (`secure_message`), staff communications (`conversation_opened` / `communication_logged`), ingested email
  (`email_received`), appointments (`calendar_event`), documents (`document_uploaded`), document requests
  (`document_requested`), signatures (`signature_requested` / `signature_completed`), workflow milestones,
  and notes.
* **Staff communications** — `app/services/communications/` (D.18) owns `communication_*` tables.
* **Portal messaging + notifications** — D.43 `app/portal/service.py` (`portal_threads` / `portal_messages`
  / `portal_notifications`), append-only.
* **Canonical notification ledger** — `app/services/notifications.py`. **Email** — Microsoft Graph
  ingestion into `timeline_events` + `microsoft_unmatched_messages`. **SMS** — not modeled (disabled hooks
  only).

Building a second messaging / notification / timeline / document / scheduling engine — or copying source
content into a new "interactions" store — would violate the platform's core "no second system" invariant
and duplicate the deduplication/scope/redaction work the activity timeline already does.

## Decision
Phase D.44 adds a **governed composition layer** under the existing communications domain
(`app/services/communications/engagement/`) that provides one unified interaction/engagement view WITHOUT
any new store:

1. A declarative **interaction registry** (`registry.py`) — the single catalog of every interaction type
   (authoritative owner, source service, visibility, retention class, participant type, rendering + search
   adapter, deep link, supported actions, lifecycle, compliance owner). It also **classifies** a raw
   authoritative timeline `(source, event_type)` onto a governed interaction type, so onboarding a source
   is declarative here — not scattered type-checks across surfaces.
2. A normalized **unified interaction model** (`model.py`) that REFERENCES authoritative records (source
   system + ids + deep link) and never copies source content (`preview` is a short derived snippet only).
3. Read-only, scope-aware, **fail-closed adapters** (`adapters/`): the advisor spine delegates to the
   authoritative `activity_timeline` projection (reusing its scope/dedup/redaction/order) and classifies
   its rows; the client spine reuses the D.43 portal scoped reads. Non-communication activity is dropped.
4. The **engagement service** (`service.py`) — composes, filters, searches, paginates, and summarizes; the
   summary backs the Client 360 / Household 360 sections and (through them) AI Assist grounding.
5. **Runtime gates** (`gate.py`, governed by the Runtime Engine, no env fallback), **low-cardinality
   analytics** (three metrics), **internal diagnostics** (`observability.audit`), and a read-only
   **governance** checker enforcing the invariants.

No migration, no new table, no new capability (reuses `communications.view` / `observability.audit`), and
**no new outbox contract** — the layer only consumes existing authoritative reads.

## Alternatives considered
- **A new `interactions` table + ingestion pipeline.** Rejected: a second store, duplicates content, and
  re-implements dedup/scope/redaction the activity timeline already owns.
- **A second timeline service.** Rejected: `activity_timeline` already composes domains with an adapter
  model; D.44 composes over it and classifies, adding communication semantics.
- **Adapters that re-query each domain directly.** Rejected for the advisor spine: it would double-count
  interactions already deduplicated in `timeline_events` and re-implement scope. The advisor spine
  delegates to the authoritative projection; only the client (portal) spine reads source functions,
  because portal principals cannot use the staff timeline.
- **AI Assist querying communications directly.** Rejected: AI consumes the composed Client 360 /
  Household 360 communications summary (grounded counts only), preserving the "reuse summaries, no raw
  domain fan-out" invariant.

## Reasons for the decision
Composition over the authoritative projection gives one interaction history for free — already scoped,
deduplicated, ordered, and redacted — while the registry adds the missing governance artifact (a declarative
catalog + classification). Every interaction stays owned by its subsystem; the layer only reads, classifies,
filters, searches, and summarizes. Deep links (never inline mutation) send the user to the authoritative
surface to act.

## Consequences

### Positive consequences
- One governed engagement surface across every channel, with no second store and no copied content.
- New sources are onboarded declaratively in the registry; governance verifies completeness.
- Zero schema change: no migration, no table, no capability, no outbox contract — minimal surface area.
- Advisor (Client 360 / Household 360 tabs, `/engagement`), AI grounding, and the client portal all reuse
  the same composition.

### Negative consequences and tradeoffs
- The advisor engagement timeline is a recent-interactions view bounded by the projection window
  (top ~100 recent events), not a deep archive read; deep history stays in the authoritative timeline.
- Classification depends on the authoritative `event_type`; a new interaction type must register its
  timeline signals to appear.

## Enforcement
`tests/test_unified_communications.py` (registry completeness, adapter isolation + fail-closed, composition
+ ordering + classification, dedup, search + filters, visibility, scope → None out of scope, runtime gates,
portal/Client 360/Household 360 integration, AI grounding, analytics, diagnostics, governance, and the
architecture invariants — no DB write / no outbox / no audit write / no table in any engagement module).
`app/services/communications/engagement/governance.py` enforces the invariants at runtime. Route count,
migration head, and section registry are guarded by `tests/test_platform_architecture.py`,
`tests/test_client360_workspace.py`, and `docs/platform_architecture_manifest.yaml`.

## Exceptions
The client (portal) spine reads the D.43 portal source functions directly rather than the activity timeline,
because external portal principals are not staff principals and cannot use the record-scoped staff timeline.
It only ever produces externally-visible interaction types (governance-verified).

## Revisit conditions
Revisit when SMS or outbound email transport is implemented (new interaction sources), when a deep archive
engagement view is required (beyond the recent-interactions window), or if any engagement lifecycle event
gains a consumer that would justify an outbox contract.

## References
- `app/services/communications/engagement/*` (`registry.py`, `model.py`, `service.py`, `gate.py`,
  `stats.py`, `metrics.py`, `diagnostics.py`, `governance.py`, `adapters/timeline.py`, `adapters/portal.py`)
- `app/routes/engagement.py`; portal routes in `app/routes/portal.py`; Client 360 section in
  `app/services/client360/{registry,sections}.py`; Household 360 section in
  `app/services/client360/household.py`; AI grounding in `app/services/ai_assist/context.py`;
  analytics in `app/services/analytics/{sources,metrics}.py`
- `docs/COMMUNICATION_ARCHITECTURE.md`, `docs/ENGAGEMENT_TIMELINE.md`, `docs/COMMUNICATION_REGISTRY.md`,
  `docs/COMMUNICATION_GOVERNANCE.md`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`,
  `tests/test_unified_communications.py`; relates to ADR-004, ADR-013, ADR-018, ADR-028, ADR-030, ADR-038
  through ADR-048
