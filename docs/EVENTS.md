# Client360 — Event Envelope & Schema Versioning (E1.7 / Backlog F1.4)

The canonical, domain-agnostic contract for every Client360 event. It layers on
the F1.3 transactional outbox (the canonical transport) and **changes no outbox
guarantee** and **no database schema** — the envelope is serialized into the
existing `outbox_events.payload` JSON column.

## The envelope
`app/platform/events.py → Envelope`

| Field | Meaning |
|---|---|
| `schema_version` | Integer; enables backward-compatible evolution (current: **1**) |
| `event_id` | Stable idempotency key (uuid) |
| `event_type` | Routing/subscription key (domain-agnostic string) |
| `occurred_at` | ISO-8601 UTC timestamp |
| `correlation_id` | Ties related events across a flow (optional) |
| `causation_id` | The event that caused this one (optional) |
| `subject_ref` | What the event is about, e.g. `"account:123"` — a **reference only**, never PII (optional) |
| `producer` | Who emitted it, e.g. `"wealth.account_opening"` (optional) |
| `payload` | Business data — **references only**, never secrets/PII/return data |
| `metadata` | Transport/diagnostic context (optional) |

## Producing & consuming
```python
from app.platform import new_event, publish_event, subscribe, Envelope

# Produce — inside an existing transaction (atomic with the business change):
env = new_event("AccountFunded", {"account_ref": "account:5"},
                subject_ref="account:5", producer="wealth.funding",
                correlation_id=flow_id)
with engine.begin() as conn:
    ...                       # business writes
    publish_event(conn, env)  # canonical path

# Consume — handler receives an Envelope; must be idempotent:
def on_account_funded(event: Envelope) -> None:
    account = event.payload["account_ref"]
subscribe("AccountFunded", on_account_funded)
```

`publish_event` mirrors the envelope's `event_id`/`event_type` onto the outbox
row's `event_id`/`name`, so idempotency and subscriber routing are unchanged.

## Serialization rules
- `to_dict()` / `from_dict()` and `to_json()` / `from_json()` round-trip an
  envelope losslessly.
- Timestamps are ISO-8601 strings; payload/metadata are JSON objects.
- **References only** in `payload`, `subject_ref`, and `metadata` — never
  secrets, PII, SSNs, or tax-return data (Constitution §9).

## Schema versioning & compatibility guarantees
- **Additive evolution:** bump `SCHEMA_VERSION` and add an upgrade step in
  `upgrade_envelope()` when the envelope shape changes; consumers keep working.
- **Backward compatible reads:** `from_dict` runs `upgrade_envelope`, so older
  stored envelopes (or a dict missing `schema_version`) deserialize into the
  current shape.
- **Fail-closed on the future:** an envelope whose `schema_version` is newer than
  this code supports is rejected, never silently mis-read.
- **No field is dropped:** unknown fields from a newer minor shape are preserved
  under `metadata._unknown` rather than lost.
- **Domain-agnostic:** the envelope carries no domain knowledge, so it is stable
  for future workflow templates, the automation engine, and Microsoft / tax /
  insurance / portfolio workflows.

## Relationship to the outbox (F1.3)
- The outbox remains the transport; F1.4 only defines what a message *is*.
- Legacy F1.3 `publish(conn, name, payload)` (bare payload) still works and is
  delivered as `{"event_id", "name", "payload"}`; envelope rows (via
  `publish_event`) are delivered as an `Envelope`. The dispatcher distinguishes
  them with `is_envelope(...)`. See [OUTBOX.md](OUTBOX.md).

## Scope boundary
F1.4 delivers the envelope + versioning only. **Workflow templates, business
events, and application producers** are **F1.5+** and are not implemented here.

## Known gaps / future (non-blocking)
- Envelope fields are stored in the payload JSON, not as indexed columns; if
  correlation/subject tracing needs SQL querying at scale, promote selected
  fields to columns via an additive migration (future).
- A machine-readable schema registry (e.g., JSON Schema per `event_type`) can be
  added when concrete event types are introduced (F1.5+).
