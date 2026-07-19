# Client360 — Notification Preferences, Consent & Suppression (F5.3 / Epic 5)

The canonical **decision layer** that determines whether a notification intent is eligible
for delivery through a channel, governed by
[ADR-017](architecture/adr/ADR-017-notifications-and-communications-architecture.md). F5.3
answers **allowed / suppressed / disabled / not_applicable** with a stable reason code — it
**never** delivers, retries, calls providers, consumes events, mutates workflow/domain/
ledger state, emits audit/evidence, or exposes content.

`app/services/notification_preferences.py` · migration `f53p1r2c3n4t`

## Existing-system reconciliation
The repository has **no** pre-existing preference/consent model — no preference/consent
tables, no opt-in/opt-out or do-not-contact columns, no contact-method preferences. The
portal `settings.html` page is descriptive text only. Classification:
- **Authoritative:** *(new)* the F5.3 `notification_preferences` and `notification_consents`
  tables. The **F5.2 provider-state** remains authoritative for whether a *channel* is
  disabled.
- **Advisory:** portal `settings.html` (describes the fixed channel state).
- **Legacy / Absent:** no prior preference, consent, opt-out, or do-not-contact records.
No existing source of truth is duplicated.

## Preference vs. consent (separate concepts, never conflated)
- **Preference** (`notification_preferences`): *how* a recipient wishes to be contacted —
  `opted_in` / `opted_out` / `default`.
- **Consent** (`notification_consents`): *whether* communication is legally/operationally
  permitted — `granted` / `withdrawn`, with `effective_at` / `expires_at` / `revoked_at`.
A positive preference **never** overrides missing, withdrawn, or expired consent, and a
preference is **never** proof of consent. They are stored in **separate tables**.

## Schema (additive — migration `f53p1r2c3n4t`)
Two tables (created by migration; reflected at runtime; not declared in `schema.py`):
- `notification_preferences`: `preference_uid` (unique), `recipient_type`/`recipient_ref`,
  `channel` (or `*`), `purpose` (or `*`), `preference_state` (CHECK), `source_ref`,
  `effective_at`, `created_at`, `updated_at`. Unique scope
  `(recipient_type, recipient_ref, channel, purpose)`.
- `notification_consents`: `consent_uid` (unique), `recipient_type`/`recipient_ref`,
  `channel`/`purpose`, `consent_state` (CHECK), `authority_ref`, `source_ref`,
  `effective_at`, `expires_at`, `revoked_at`, `created_at`, `updated_at`. Unique scope.
Reference-only — **no** notification `title`/`body` is stored. No immutability trigger
(current-state rows; see History).

## Decision contract
```python
from app.services.notification_preferences import evaluate_delivery, DeliveryDecision
d = evaluate_delivery("user", "user:3", "in_app", "workflow.sla.escalated")  # -> DeliveryDecision
```
`DeliveryDecision` (structured, content-free): `decision` (`allowed`/`suppressed`/`disabled`/
`not_applicable`), `channel`, `recipient_ref`, `purpose`, `reason_code`, human-safe `reason`,
`source_ref` (preference/consent uid), `effective_ref` (timestamp/version). Reason codes:
`channel_allowed`, `provider_channel_disabled`, `global_suppression`, `recipient_opted_out`,
`consent_missing`, `consent_expired`, `no_applicable_preference`.

## Precedence (normative, deterministic)
1. **Unknown channel** → `not_applicable` (`no_applicable_preference`).
2. **Provider channel disabled** (F5.2 provider-state) → `disabled` (`provider_channel_disabled`).
   A positive preference never enables a disabled provider; email/SMS/push stay disabled.
3. **Global/compliance suppression** (do-not-contact: a `withdrawn` consent scoped to `*`/`*`)
   → `suppressed` (`global_suppression`). Overrides a positive preference.
4. **Recipient opt-out** (preference) → `suppressed` (`recipient_opted_out`).
5. **Required consent** (for channels in `CONSENT_REQUIRED_CHANNELS = {email, sms, push}`)
   missing/withdrawn/not-yet-effective → `suppressed` (`consent_missing`); expired →
   `suppressed` (`consent_expired`). Absence of a preference is **never** consent.
6. Otherwise → `allowed` (`channel_allowed`). In-app is allowed by default (no consent
   required), consistent with existing product behavior.

Scope resolution is most-specific-wins: `(channel, purpose)` > `(channel, *)` > `(*, purpose)`
> `(*, *)`.

## History & authority
- **Currently effective record:** the single current-state row per scope (unique
  `(recipient, channel, purpose)`).
- **Mutable vs versioned:** rows are current-state and mutable for their own lifecycle
  (state + timestamps). **Consent withdrawal is `consent_state='withdrawn'` + `revoked_at`,
  never a delete.**
- **Explainability & F5.6:** each decision carries `source_ref` (the preference/consent uid)
  and `effective_ref`, so a later **F5.6** audit can reference the exact decision inputs.
- **Append-only history (future):** a versioned/append-only change history is an additive
  F5.6 table (e.g. `notification_preference_history` / `notification_consent_history`) and
  requires **no** destructive redesign — the `*_uid`, timestamps, and scope-unique current
  rows are forward-compatible.

## F5.1 / F5.2 integration boundaries
Uses F5.1-compatible recipient references (`recipient_type`/`recipient_ref`) and F5.2 channel
names, and reads the **F5.2 provider-state** (`is_ready()`) to decide "disabled". It does
**not** update notification-ledger rows, invoke `deliver_result`, register providers, alter
provider readiness, or create notification intents. F5.4/F5.5 call this decision layer later.

## Security & privacy
No `record.read_all`/capabilities; no routes; no provider credentials/config; no external
provider enabled; no copied domain/contact records (references only); human-safe reasons
carry no protected data; **no silent default consent**; no notification content anywhere.

## Compatibility
Additive, reversible migration (single head `f53p1r2c3n4t`); reflection preserved; no
destructive DDL; no table dropped/renamed; no existing data migrated; portal/benefits and
existing behavior unchanged; email/SMS/push remain disabled.

## References
ADR-013, ADR-017; `docs/NOTIFICATIONS.md` (F5.1), `docs/NOTIFICATION_PROVIDERS.md` (F5.2);
`app/services/notification_providers.py`.
