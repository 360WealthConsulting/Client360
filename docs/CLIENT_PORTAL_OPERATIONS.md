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

`production_ready()` returns true only when `portal.enabled` AND `portal.production_signed_off` are both on
— so external production access is blocked until compliance records a decision (see
[`CLIENT_PORTAL_COMPLIANCE_GATE.md`](CLIENT_PORTAL_COMPLIANCE_GATE.md)).

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
