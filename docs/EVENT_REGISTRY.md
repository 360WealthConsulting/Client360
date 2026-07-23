# Event Registry (Phase D.34)

The **event registry** (`domain_event_contracts` + `domain_event_subscriptions`, served by
`app/services/events/registry.py`) is the durable, discoverable catalog of the domain-event model. The
in-code contracts (`contracts.py`) hold the executable typed contracts; the registry rows hold the
discoverable metadata. Governance reconciles the two.

## Contract fields (`domain_event_contracts`)

| Field | Meaning |
|---|---|
| `event_type` | Unique event identifier (e.g. `orchestration.lifecycle`) — the outbox routing/subscription key. |
| `category` | The event domain (workflow / orchestration / runtime / …). |
| `status` | `active` (publishable/subscribable) · `deprecated` · `retired`. |
| `schema_version` | The envelope schema version the contract targets (`app/platform/events.py`). |
| `owner` / `producer` | Who owns / emits the event (e.g. `orchestration.engine`). |
| `payload_schema` | `{field: type}` — a references-only contract (ids/codes; never PII/secrets). |
| `depends_on` | Other event types this one composes/causes (the causation graph). |

## Subscription fields (`domain_event_subscriptions`)

| Field | Meaning |
|---|---|
| `event_type` | The event subscribed to. |
| `consumer` | The subscribing consumer (e.g. `notification.dispatch`). |
| `status` | `active` · `inactive`. |
| `owner` / `description` | Ownership + intent. |

The registry row is the **discoverable, governable** record. The live outbox subscription (the actual
handler) is registered at startup through the existing consumer-registration mechanism
(`app/platform/outbox.py::subscribe`), dark-launched with the outbox dispatcher.

## Coverage (`registry.coverage()`)

- **Domain coverage** = event domains with a registered contract ÷ identified event domains = **100%**.
- **Consumer coverage** = active contracts with ≥1 active subscription ÷ active = **100%**.
- **Producer coverage** = active contracts with a declared producer ÷ active = **100%**.
- Current: 5 contracts (all active), 5 subscriptions (all active), 3 domains.

## Lifecycle

`registry.deprecate(event_type, reason=…)` then, once no producer/consumer references it,
`registry.retire(event_type)` — each records a major audit event. `subscriptions.add_subscription(...)`
/ `set_status(...)` manage the subscription registry.

## Versioning

The canonical `Envelope` carries `schema_version`; `upgrade_envelope()` normalizes older stored events
to the current shape (backward-compatible evolution), and an envelope newer than supported is rejected
(fail-closed). A contract's `schema_version` must match the code contract and be a supported version —
governance flags **version drift**.

## Routes (`/events`, reuse the `observability.*` capabilities)

`GET /events` (dashboard, `observability.view`) · `/registry` · `/subscriptions` · `/adoption` ·
`/graph` · `/contracts/{event_type}` (`observability.view`) · `GET /events/governance` · `/diagnostics`
· `/dead-letters` · `/{event_id}` · `/{event_id}/replay` (`observability.audit`) ·
`POST /events/governance/validate` (`observability.execute`).
