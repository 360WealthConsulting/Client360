# Client360 — Canonical Notification Channel Provider Registry (F5.2 / Epic 5)

The platform-level notification **channel provider contract and registry**, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). F5.2
establishes **provider contracts and honest outcomes only** — no async dispatch, event
consumption, preferences/consent, retry, audit/evidence, or routes; it enables no external
provider and adds no credentials.

`app/services/notification_providers.py`

## Reconciliation (ADR-013 / ADR-017 — wrap, least disruptive)
The existing portal providers (`app/portal/providers.py`:
`NotificationProvider`, `InAppNotificationProvider`, `DisabledNotificationHook`,
`NOTIFICATION_PROVIDERS`) are **preserved unchanged** and **wrapped** as the single
underlying implementation. This module is the **one canonical platform registration
source**; portal, benefits, and exception-SLA continue to import the legacy
`NOTIFICATION_PROVIDERS` unchanged (byte-for-byte behavior, incl. the exact `in_app`
result and disabled `reason`). No duplicate provider implementation is created — the
canonical registry adapts the same underlying provider instances. Later features (F5.4+)
migrate callers to the canonical service.

## Canonical registry authority (normative)
- **The canonical `NotificationProviderRegistry` is the single authoritative platform
  registration surface** for notification channel providers going forward.
- **The legacy `NOTIFICATION_PROVIDERS` dictionary exists only as a compatibility layer**
  for existing portal and benefits (and exception-SLA) code; it is not the platform
  registration surface.
- **New provider registrations after F5.2 must occur through the canonical registry**
  (`register(...)` / `build_default_registry`), **not** by mutating the legacy dictionary.
- **The legacy dictionary remains synchronized through the compatibility adapter until its
  consumers are migrated** in later Epic 5 phases: the wrapping in `build_default_registry`
  is that adapter — for the existing channels (`in_app`/`email`/`sms`/`push`) the canonical
  registry and the legacy dictionary **share the same underlying provider instances**, so
  they are inherently consistent and cannot diverge. (Consistency covers the wrapped legacy
  channels; new canonical-only registrations are not added to the legacy dictionary, which
  stays frozen to its existing consumers.) Once F5.4+ migrates those consumers onto the
  canonical registry, the legacy dictionary can be reduced to a thin compatibility view or
  retired.

## Canonical channels & initial state
| Channel | State |
|---|---|
| `in_app` | **enabled** (delivers) |
| `email` | disabled (hook) |
| `sms` | disabled (hook) |
| `push` | disabled (hook) |
No external communication is enabled; disabled channels never attempt delivery.

## Provider contract
```python
from app.services.notification_providers import build_default_registry, DeliveryResult
reg = build_default_registry()          # one provider per channel; wraps portal providers
p = reg.get("in_app")                    # raises for an unknown channel
p.identifier; p.channel; p.enabled; p.is_ready()
result = p.deliver_result(recipient="user:3", title="…", body="…")   # -> DeliveryResult
```
`ChannelProvider` exposes: `identifier`, `channel`, `enabled`, `is_ready()`, and a
deterministic, exception-normalized `deliver_result(...)`. `DeliveryResult` is structured
and content-free: `outcome` (`delivered`/`disabled`/`failed`), `channel`, `delivered`,
`provider_ref` (external reference when available), `failure_class`
(`provider_not_configured`/`provider_unavailable`/`provider_error`), and a human-safe
`description`. Suitable for later F5.5 dispatch.

## Honest outcomes (ADR-017 §10)
- **`delivered`** — the (enabled) provider delivered.
- **`disabled`** — a disabled/unconfigured provider returns this **explicitly, without any
  external attempt**; it never returns a false success.
- **`failed`** — an underlying provider error, normalized (exceptions never escape
  `deliver_result`).

## Registry
`NotificationProviderRegistry`: `register` (rejects a duplicate channel), `get` (raises for
an unknown channel), `channels()`, `states()` (enabled/ready/identifier metadata — no
content), `in` / `len`. `build_default_registry()` / `default_registry()` return the
canonical registry wrapping the portal providers.

## Architectural boundaries
The provider layer delivers only what the notification service gives it; it never
determines workflow/domain state, owns business events, selects recipients, bypasses
future consent/suppression, performs retries, mutates workflow/domain/evidence/audit
records, or makes the ledger authoritative. It exposes no credentials or content through
logs (`deliver_result` never logs or returns `title`/`body`).

## Out of scope (F5.3–F5.7)
Preferences/consent/suppression (F5.3), event consumers (F5.4), dispatch/retry worker
(F5.5), notification audit/evidence (F5.6), API/admin (F5.7), and any external provider
integration/credentials.

## Compatibility
No migration (head remains `f51n0t1c3d4e`); no new capability, route, or schema; portal +
benefits + exception-SLA behavior unchanged; email/SMS/push remain disabled.

## References
ADR-013, ADR-017; `docs/NOTIFICATIONS.md` (F5.1); `app/portal/providers.py`,
`app/portal/service.py`, `app/services/benefits_notifications.py`,
`app/services/exception_sla.py`.
