# Client Portal Operations (Phase D.43)

Operating, enabling, observing, and administering the Client Portal. See
[`ADR-048`](adr/ADR-048-secure-client-portal.md).

## Feature gates (all OFF by default)
Configured through the governed Runtime Engine (no environment fallback), read via `app/portal/gate.py`:

| Gate | Default | Effect |
| --- | --- | --- |
| `portal.enabled` | OFF | master portal switch |
| `portal.household_enabled` | OFF | household surfaces |
| `portal.documents.download_enabled` | OFF | document download |
| `portal.documents.upload_enabled` | OFF | client uploads |
| `portal.messaging_enabled` | OFF | secure messaging |
| `portal.appointments_enabled` | OFF | appointment requests |
| `portal.financial_summary_enabled` | OFF | masked financial summary |
| `portal.forms_enabled` | OFF | forms/signatures |
| `portal.mfa_required` | **ON** | require MFA for sign-in |
| `portal.production_signed_off` | OFF | compliance sign-off (blocks external production access) |
| `portal.local_identity_provider_enabled` | OFF | **controlled synthetic test only** — keeps the deterministic local IdP registered after sign-off |

`production_ready()` returns true only when `portal.enabled` AND `portal.production_signed_off` are both on
— so external production access is blocked until compliance records a decision (see
[`CLIENT_PORTAL_COMPLIANCE_GATE.md`](CLIENT_PORTAL_COMPLIANCE_GATE.md)).

## How the gates are governed
`gate()` evaluates **every** entry in `GATES` through the runtime feature-flag evaluator
(`consumption.feature_enabled`). Until migration `b5d82e04c917`, `portal.production_signed_off` and
`portal.mfa_required` existed only as `configuration_items`, so `feature_defined` was false for both and
`gate()` silently returned the hard-coded default — neither gate was actually governed. Both are now
seeded as feature flags at their existing effective values (sign-off disabled/rollout 0, MFA
enabled/rollout 100).

**Feature-flag metadata is authoritative for gate evaluation.** The historical `configuration_items` rows
for those two codes remain in place for compatibility and history, but they are no longer consulted by
`gate()`.

The governed write path for every portal gate — including sign-off and MFA — is therefore:

```
features.set_flag_status(principal, flag_id, "active")     # sets enabled
features.update_flag_rollout(principal, flag_id, 100)      # clears rollout_zero
runtime_engine.refresh(principal, actor_user_id=...)       # trigger defaults to "manual"
```

A gate is ON only when `status='active'` AND `enabled` AND `rollout_percentage >= 100`. Valid runtime
generation triggers are `manual`, `scheduled`, `startup`, `metadata_change`, `emergency` — a custom
trigger string violates `ck_runtime_generation_trigger`. No configuration-item write is required.

### Follow-up: `portal.mfa_required` vs. actual MFA enforcement
`portal.mfa_required` is now genuinely governed metadata, but **toggling it does not currently control MFA
enforcement**. The enforcement that protects the portal today is separate and unconditional:
`accept_invitation` rejects activation unless `mfa_verified` is true, and sign-in checks the
`portal_accounts.mfa_required` **column**. Neither reads the gate.

Do not assume this flag switches MFA on or off. Reconciling the gate with the unconditional checks is a
separate bounded architecture task; until it is done the unconditional checks are the control, and they
must not be weakened.

### Runtime governance
Portal gates are intentionally governed outside `runtime_behaviors` — they are enforced directly by
`app/portal/gate.py` and `app/services/features/portal_gate.py`, and validated by
`app/portal/governance.py::validate_portal`. `app/services/runtime/governance.py` therefore carries an
**explicit allow-list** (`_PORTAL_GATE_DEFINITIONS`) so an enabled portal gate is not reported as an
orphan definition. It is a literal list of the current gate codes, never a `portal.` prefix: an unlisted
portal code is still reported, and a test requires the list to equal `app.portal.gate.GATES` exactly, so
adding a gate forces a deliberate governance decision.

## Local test identity provider (`portal.local_identity_provider_enabled`)
Default **OFF**. The deterministic local provider normally registers only while the portal is *not*
production-signed-off. Because it is the only portal identity provider that exists, recording sign-off
would otherwise leave none registered and make even a synthetic production test impossible (both auth
surfaces resolve a provider and return 400 without one).

This gate authorizes the local provider independently of sign-off:

| `production_signed_off` | `local_identity_provider_enabled` | local provider |
| --- | --- | --- |
| OFF | OFF | registered (existing local/dev/CI behaviour) |
| OFF | ON | registered |
| **ON** | **OFF** | **not registered** — production protection intact |
| ON | ON | registered — governed synthetic-test window only |

It does **not** affect `production_ready()`, opens no portal surface, and grants no staff capability.
It is **not** a substitute for the real external identity provider: that remains an open compliance
requirement (see `CLIENT_PORTAL_COMPLIANCE_GATE.md`), and this flag **must be returned to OFF before any
real client is onboarded**.

Registration happens once, in the application startup lifespan. Changing this flag on a running process
has no effect until Client360 is restarted.

## Enabling for local/test
Locally the gates stay off but implementation proceeds behind them: the deterministic local identity
provider auto-registers at startup (only when not production-signed-off) so activation works offline, and
tests monkeypatch individual gates on. No external email/SMS/storage/signature/identity provider is used.

## Internal admin surface (`/admin/client-portal/*`, staff fork)
- `GET /admin/client-portal` — account list (HTML), capability `client.read`.
- `GET /admin/client-portal/accounts` — account list (JSON), `client.read`.
- `POST /admin/client-portal/invite` — invite an account, `client.write` + record scope on the person. The
  activation token is delivered out-of-band and **never** returned in the response or logged.
- `POST /admin/client-portal/accounts/{id}/revoke` — revoke account + deactivate grants, `client.write` +
  record scope.
- `GET /admin/client-portal/accounts/{id}/preview` — a permissions report (grant scope × visibility
  registry). This is NOT impersonation — no session is created. `client.read` + record scope.
- `GET /admin/client-portal/diagnostics` — internal diagnostics, `observability.audit`.

There is no unrestricted impersonation: staff can preview entitlements but cannot assume a portal session.

## Diagnostics & analytics
`app/portal/diagnostics.py` composes low-cardinality counters (`app/portal/stats.py`), the gate snapshot,
visibility coverage, and the governance report. It exposes aggregates only — no ids, emails, document
names, message text, or tokens — and is reachable only from the internal admin surface. Portal analytics
metrics are low-cardinality (composition, auth success/failure, activations, scope denials, uploads,
downloads, consents, notification failures) following the established in-process-counter pattern.

## Failure isolation
The portal runs behind the middleware fork; a portal failure returns a portal error and never breaks
internal staff surfaces. The local identity provider registration at startup is guarded so it can never
block application startup.

## References
`app/portal/{gate,diagnostics,stats,identity_local}.py`, `app/routes/portal_admin.py`, `app/main.py`
(startup registration), `docs/CLIENT_PORTAL_COMPLIANCE_GATE.md`, ADR-048.
