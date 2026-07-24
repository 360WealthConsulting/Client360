# Communication Registry (Phase D.44)

`app/services/communications/engagement/registry.py` is the **single declarative catalog** of every
interaction type the engagement layer knows about. It is the one place a new communication type is
registered, and the classifier that maps a raw authoritative timeline `(source, event_type)` onto a
governed interaction type. See [`ADR-049`](adr/ADR-049-unified-communications.md).

## Interaction type record
Each `InteractionType` declares:
- `key` — the interaction type;
- `source_service` — the module/service that reads it;
- `authoritative_owner` — the subsystem that OWNS the record (mutations happen only there);
- `visibility` — `internal` | `external` | `both`;
- `retention_class` — `standard` | `regulatory` | `transient` (declarative data-lifecycle bucket;
  enforcement stays with the owner);
- `participant_type` — `advisor_client` | `staff_client` | `inbound` | `system` | `client_action`;
- `rendering_adapter` / `search_adapter` — the adapter keys that normalize + supply searchable text;
- `deep_link` — the authoritative surface to act on the interaction;
- `supported_actions` — governed actions (each a deep link, never inline mutation);
- `lifecycle` — `active` | `experimental` | `deprecated` | `retired`;
- `compliance_owner`;
- `timeline_signals` — the authoritative `(source, event_type)` pairs that classify onto this type.

## Registered types
`secure_message`, `communication` (staff), `email`, `appointment`, `document`, `document_request`,
`signature_request`, `client_request`, `workflow_milestone`, `note`, and `notification`. Regulatory-retention
types: secure messages, staff communications, signature requests.

## Classification
`classify(source, event_type)` maps an authoritative timeline event onto an interaction type — preferring
the exact `(source, event_type)` signal, then falling back to `event_type` alone (the composed
`activity_timeline` projection normalizes the source label but preserves the original event type).
Non-communication events (portfolio import, assignment change, SLA risk, …) classify to `None` and are
dropped from the engagement view.

## Onboarding a new interaction type
1. Add an `InteractionType` (via the `_t(...)` helper) to `REGISTRY` with its owner, visibility, retention,
   deep link, and — if it flows through the timeline — its `timeline_signals`.
2. If it needs bespoke normalization (unusual subject/preview/direction), extend the relevant adapter.
3. Governance automatically verifies the new type is fully declared; the count guard in
   `tests/test_unified_communications.py` locks the registry size.

Nothing else changes — no migration, no capability, no route. Onboarding is declarative.

## Visibility & the portal
`INTERNAL_ONLY_TYPES` (communication, email, document, workflow_milestone, note) must never be surfaced to
an external portal principal; the portal composition filters them out and governance asserts no portal
adapter emits them. `coverage()` reports totals (types / timeline-backed / internal-only / external /
regulatory / signals) and feeds diagnostics.

## References
`app/services/communications/engagement/registry.py`, `app/services/communications/engagement/model.py`,
`app/services/communications/engagement/governance.py`, `tests/test_unified_communications.py`, ADR-049.
