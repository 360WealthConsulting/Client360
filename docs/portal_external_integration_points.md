# Client Portal — External Integration Points Audit

The exact files/functions/config hooks for every external dependency the portal needs before a real
client. For each: **what exists**, **what is stubbed/disabled**, **what must be implemented/configured**,
and **code vs. configuration**. No vendors are chosen here.

Status legend: ✅ implemented · 🧪 dev/test double · ⛔ disabled/absent.

---

## 1. Client identity provider (OIDC / SAML)

- **Contract** — `app/portal/providers.py`
  - `PortalIdentityProvider.verify_activation(assertion: str) -> PortalIdentityResult(subject, mfa_verified, email)`
  - Registry: `PORTAL_IDENTITY_PROVIDERS = ProviderRegistry(...)`; resolved by key at login.
- **Consumed by** — `app/routes/portal_api.py :: login` (`POST /api/portal/login`) and
  `app/routes/portal.py :: invitation_accept` (`POST /api/v1/portal/auth/invitations/accept`). Both call
  `provider.verify_activation(...)` then `accept_invitation(...)` then `create_portal_session(...)`.
- **Registration hook** — `app/main.py` (~line 152) calls
  `app/portal/identity_local.py :: register_local_provider_if_permitted()`.
- **What exists:** ✅ the provider abstraction, registry, login wiring, and account linking are complete
  and are IdP-agnostic. Linking a subject to an account is the explicit, audited `accept_invitation` step
  — it never auto-links by email.
- **Stubbed/disabled:** 🧪 the only registered provider is `LocalTestIdentityProvider`
  (`app/portal/identity_local.py`), which accepts a deterministic `local:<subject>[:mfa]` assertion and
  registers **only when the portal is not production-signed-off** (so it can never satisfy a real
  activation in production).
- **Must implement/configure:** a real OIDC/SAML provider class implementing `verify_activation` (verify
  the IdP token/assertion → return `subject`, `mfa_verified`, optional `email`), registered against
  `PORTAL_IDENTITY_PROVIDERS` at startup for production.
- **Code vs config:** **Code** — a new provider class (~one small module) implementing one method, plus a
  registration line. Everything downstream (sessions, scope, audit) is already built. Provider endpoints,
  client IDs, and secrets are **config** (see runbook env vars).

## 2. MFA enforcement

- **Where:** `PortalIdentityResult.mfa_verified` (from the IdP) → `accept_invitation`
  (`app/portal/service.py`, raises `"MFA verification is required"` when false) → `create_portal_session`
  (blocks sign-in when `account.mfa_required` and not `mfa_enabled`). Gate `portal.mfa_required` defaults
  **True** (`app/portal/gate.py`).
- **What exists:** ✅ the platform *requires and records* IdP-asserted MFA and refuses activation/sign-in
  without it. The expectation is that **MFA is performed by the IdP**, not by this platform.
- **Stubbed/disabled:** 🧪 the local provider marks MFA verified only when the assertion carries the `mfa`
  marker (so the MFA-required path stays testable offline).
- **Must implement/configure:** the chosen IdP must be configured to enforce MFA and to surface an
  MFA-satisfied claim that the production provider class maps to `mfa_verified=True`.
- **Code vs config:** **Config** at the IdP + **a few lines of code** in the provider class to read the
  MFA claim. No platform enforcement changes needed.

## 3. Transactional email — invitation / activation

- **Where:** `app/portal/service.py :: invite_portal_account` mints a one-time token (72h) and **returns**
  the raw token to the caller; `app/routes/portal_admin.py` (`/invite`, `/invite-form`) deliberately
  **never returns or logs it** ("delivered out-of-band").
- **What exists:** ✅ token generation, hashing/storage, expiry, single-use acceptance, and audit.
- **Stubbed/disabled:** ⛔ there is **no email send** — the activation link is not delivered anywhere.
- **Must implement:** an email step that composes the activation URL from the token and sends it to the
  invitee via a transactional email provider. Cleanest insertion: a small delivery service invoked right
  after `invite_portal_account` (staff invite paths), keeping the token out of API responses/logs.
- **Code vs config:** **Code** (compose + send) **+ config** (provider credentials, from-address,
  portal base URL for the link).

## 4. Transactional email — password reset

- **Where:** `app/portal/service.py :: request_password_reset` mints a reset token (30m) and returns it;
  the route `POST /api/v1/portal/auth/password-reset/request` (`app/routes/portal.py`) returns
  `{"status":"accepted"}` **without** the token (no user enumeration). `consume_password_reset` validates
  + single-uses it and hands off to the IdP.
- **What exists:** ✅ token lifecycle + enumeration-safe request endpoint + consume/handoff.
- **Stubbed/disabled:** ⛔ **no email send** of the reset link.
- **Must implement:** email delivery of the reset URL (same provider as invitations). Note password reset
  is IdP-mediated (there is no local password store) — the "reset" hands off to the identity provider.
- **Code vs config:** **Code + config**, same provider as §3.

## 5. Notification delivery — email / SMS (push)

- **Where:** portal providers `app/portal/providers.py :: NOTIFICATION_PROVIDERS`
  (`in_app` enabled; `email`, `sms`, `push` are `DisabledNotificationHook` → honest
  `{"delivered": False, "reason": "provider_not_configured"}`). Canonical platform registry
  `app/services/notification_providers.py :: build_default_registry` **wraps** these unchanged. Dispatch
  entry: `app/portal/service.py :: notify(...)`.
- **What exists:** ✅ channel contract, registry, honest disabled outcomes, idempotency, and the in-app
  channel. `notify()` records delivery state.
- **Stubbed/disabled:** ⛔ email/SMS/push are explicitly disabled hooks (no external calls).
- **Must implement:** real `NotificationProvider` implementations for the chosen channels, swapped in for
  the `DisabledNotificationHook`s (email likely shares §3/§4's provider). F5.2 note in-code confirms these
  are contract-only today (no async dispatch/retry/preferences).
- **Code vs config:** **Code** (provider adapters) **+ config** (credentials, from-address/number).

## 6. Production portal enable / compliance sign-off gates

- **Where:** `app/portal/gate.py :: GATES` (all default **OFF**; `portal.mfa_required` default ON),
  evaluated through the governed runtime snapshot
  (`app/services/runtime/consumption.feature_enabled`, production-safe `default=False`, **no raw env
  fallback**). `production_ready() = portal.enabled AND portal.production_signed_off`. Governance invariant
  `app/portal/governance.py` flags `production_ready_without_signoff`. Feature gates already enforced in
  `financial.py`, `appointments.py`, engagement, etc.
- **What exists:** ✅ the full gate model, production-safe defaults, and the AND-gate on compliance
  sign-off. Nothing external is served until a runtime snapshot enables `portal.enabled` **and** records
  `portal.production_signed_off`.
- **Stubbed/disabled:** ⛔ by default every gate is OFF (portal is dev/test-only until explicitly enabled).
- **Must implement/configure:** set the gates in the runtime snapshot for the target environment and
  record the compliance sign-off. This is the governed control that flips the portal "on."
- **Code vs config:** **Configuration** (runtime snapshot values + recorded sign-off). No code change.

---

## Summary — what actually blocks a real client

| Area | Blocker type | Code needed? |
|---|---|---|
| OIDC/SAML provider | Implement + configure | **Yes** (one provider class) + config |
| MFA | Configure at IdP | Minimal (map one claim) |
| Invitation email | Implement delivery | **Yes** + config |
| Password-reset email | Implement delivery | **Yes** (shares provider) + config |
| Notification email/SMS | Implement adapters | **Yes** + config |
| Production gates + sign-off | Configure | **No** (config only) |

Everything downstream of these hooks — sessions, scope, audit, upload, messaging, profile — is built and
tested. No vendor has been selected; each "Yes" above is a small, well-bounded adapter against an existing
contract, not a redesign.
