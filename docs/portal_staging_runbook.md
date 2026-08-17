# First-Client Staging Runbook

Purpose: stand up the **current** codebase in a **staging** environment good enough to exercise the
client portal end-to-end with **one fake/test client**. This is dev→staging only.

> **Guardrails.** This runbook targets a **separate staging host + staging database**. Do **not** deploy
> to, query, restart, migrate, or touch the Windows production server, the production database, production
> files, or the running OCR/import job. Nothing here requires production.

**Key fact that makes a test client possible today:** while the portal is **not production-signed-off**,
the deterministic local identity provider is registered, so a fake client can activate and sign in
**without** a real IdP or email. Items needing a real provider are marked **[PROVIDER DECISION]** and are
only required before a *real* client — not for this staging exercise.

---

## 1. Prerequisites

- A staging host (Linux or Windows) with Python 3.12 and a **staging** PostgreSQL database (empty).
- A clone of `release/0.13.0`, isolated venv, dependencies installed.
- No shared filesystem or credentials with production.

## 2. Environment variables & secrets

Canonical lists live in `app/deploy/checks.py` (`PRODUCTION_REQUIRED`, `RECOMMENDED`).

| Var | Staging (test client) | Notes |
|---|---|---|
| `CLIENT360_ENVIRONMENT` | `development` (or `staging`) | Keep **not** `production` so the local IdP registers and strict secret checks relax. Setting `production` forces real OIDC + SESSION_SECRET. |
| `DATABASE_URL` | **required** | Staging Postgres DSN. No built-in default. |
| `SESSION_SECRET` | **set it** (see §3) | Signed session cookies. |
| `CLIENT360_DEV_AUTH` | optional (staff dev sign-in) | Must be **unset** in production; fine in staging. |
| `CLIENT360_DATA_ROOT` | recommended | Base for persistent doc storage (see §5). |
| `VAULT_STORAGE_ROOT` | recommended | Where client-vault bytes land (see §5). |
| `PUBLIC_PORTAL_URL` / `PUBLIC_STAFF_URL` | recommended | Base URLs used when composing links (invitation/reset emails). |
| `ALLOWED_HOSTS`, `TRUSTED_PROXY` | recommended | Host allow-list / proxy trust if fronted by a reverse proxy. |
| `LOG_DIR` | recommended | Log destination. |
| `OIDC_ISSUER` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | **[PROVIDER DECISION]** | **Staff** SSO. Only required when `CLIENT360_ENVIRONMENT=production`. Not needed for a staging test client. |
| Portal client IdP config | **[PROVIDER DECISION]** | The *portal* client IdP is separate from staff OIDC (see §8). Not needed for a test client. |
| Email provider creds | **[PROVIDER DECISION]** | For invitation/reset/notification email (see §11). Not needed for a test client. |

## 3. SESSION_SECRET

- Generate a strong random value and set `SESSION_SECRET` in the staging environment
  (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
- If unset, the app boots with an **insecure dev fallback** (`config.py` warns). Acceptable for a throwaway
  staging box; **never** for anything internet-facing. In `production` mode a missing secret is fatal.

## 4. Database setup & migrations

```
# against the STAGING DATABASE_URL only
.venv/bin/alembic upgrade head
```

- Migrations are managed by Alembic (`alembic.ini`, `migrations/env.py`).
- `/readiness` verifies the DB's `alembic_version` matches the code's expected head (see §13); run the
  upgrade before serving traffic.

## 5. Document storage / data-root configuration

- Precedence (see `app/services/storage_paths.py`): per-source env var → `CLIENT360_DATA_ROOT`-derived
  default → legacy default.
- For staging, set `CLIENT360_DATA_ROOT` to a dedicated staging directory and `VAULT_STORAGE_ROOT` to a
  staging vault path (client uploads land here). `storage.storage_root()` creates it if missing.
- Confirm the vault path is **writable** — `/readiness` runs `vault_storage_writable()` and reports it.

## 6. Upload security checks (verify, no action)

Already enforced on the client path (`app/services/vault/storage.py` + `app/portal/vault_documents.py`;
see `docs/portal_upload_security_audit.md`): extension allow-list, 50 MB streamed size cap, opaque storage
keys, path-traversal defense, SHA-256, **content sniffing** (bytes must match extension), and
pending-review quarantine (uploads aren't official until staff approve). **AV scanning and
encrypted-document policy are gaps** (vendor/product decisions) — acceptable for a fake client, **not** for
a real one.

## 7. Production-mode portal gates

- Gates live in `app/portal/gate.py` (all default **OFF**; `portal.mfa_required` ON) and are evaluated via
  the governed runtime snapshot (no raw env fallback).
- **For a staging test client, leave the portal NOT production-signed-off.** That keeps the local IdP
  active and lets you exercise the flows. To enable individual surfaces (documents/messaging/etc.) set the
  matching gate in the staging runtime snapshot.
- `production_ready()` (portal.enabled **AND** portal.production_signed_off) must remain **false** until a
  real IdP + email + compliance sign-off are in place. `app/portal/governance.py` flags
  `production_ready_without_signoff`.

## 8. Client identity-provider configuration points — **[PROVIDER DECISION]**

- Contract: `app/portal/providers.py :: PortalIdentityProvider.verify_activation`; registry
  `PORTAL_IDENTITY_PROVIDERS`. Consumed by `app/routes/portal_api.py :: login` and
  `app/routes/portal.py :: invitation_accept`.
- **Staging:** use the registered `LocalTestIdentityProvider` (`identity_provider="local"`, assertion
  `local:<subject>:mfa`). No config needed.
- **Before a real client:** implement a production provider class (verify the OIDC/SAML token → return
  `subject`, `mfa_verified`, `email`) and register it at startup. MFA is expected to be enforced **by the
  IdP** and surfaced as `mfa_verified` (see `docs/portal_external_integration_points.md`).

## 9. Invitation / activation flow (works in staging today)

1. Staff invite a client — UI `POST /admin/client-portal/invite-form` or API `POST
   /admin/client-portal/invite` (needs `client.write` + record scope on the person). This creates an
   `invited` account and a one-time token (72h).
2. **The activation token is delivered out-of-band and never returned/logged.** In staging, read it from
   the return of `invite_portal_account` (e.g. a short admin script against the staging DB) — do **not**
   add code that exposes it in a response.
3. Client activates — `POST /api/portal/login` with `{token, identity_provider:"local",
   identity_assertion:"local:<subject>:mfa", device_fingerprint}`. Activation **requires MFA-verified**
   (the `:mfa` marker) and establishes a portal session.
4. Client lands on `/portal/` (dashboard) — exercise Documents, Upload, Messages, Profile, sign-out.

## 10. Password-reset flow (works in staging; email is the gap)

- Request: `POST /api/v1/portal/auth/password-reset/request` → mints a 30-min token, returns `accepted`
  (no user enumeration, token not in the response).
- In staging, read the token from `request_password_reset`'s return; in production it must arrive by
  email (§11).
- Consume: `POST /api/v1/portal/auth/password-reset/consume` → validates + single-uses the token, hands
  off to the IdP (there is no local password store).

## 11. Transactional email integration points — **[PROVIDER DECISION]**

- **No email is sent today.** Invitation and reset tokens are minted and returned to the caller only;
  notification email/SMS/push are `DisabledNotificationHook`s (`app/portal/providers.py`).
- Before a real client: implement email delivery for (a) invitation activation link, (b) password-reset
  link, and real `NotificationProvider` adapters for email/SMS. Insertion points and contracts are in
  `docs/portal_external_integration_points.md`. Compose links from `PUBLIC_PORTAL_URL`; never log tokens.

## 12. Service startup / restart order

1. Ensure `DATABASE_URL` (staging) reachable.
2. `alembic upgrade head` (§4) — **before** serving.
3. Ensure `CLIENT360_DATA_ROOT` / `VAULT_STORAGE_ROOT` exist and are writable.
4. Start the app: `python -m uvicorn app.main:app --host <host> --port <port>` (staging: no `--reload`;
   front with a reverse proxy / process manager as desired). App startup registers the local portal IdP
   when not production-signed-off.
5. Restart = stop the process, then repeat from step 2 (re-run migrations only if the code changed).

## 13. Health checks

- **Liveness:** `GET /health` → `200 {"status":"ok"}` (DB-independent).
- **Readiness:** `GET /readiness` → checks DB connectivity, **alembic head in sync**, sync health,
  scheduler, `config_ready()`, and `vault_storage_writable()`. Treat non-`migrations_in_sync` or
  non-writable vault as **not ready**.

## 14. Smoke tests — run before inviting the test client

Service-level (no server needed):
```
.venv/bin/python -m app.deploy.smoke
# expect: route_registration OK, auth_gating OK, RESULT: OK
```
Live HTTP (against the running staging URL):
```
.venv/bin/python -m app.deploy.smoke --url http://<staging-host>:<port>
# expects: /health 200, /readiness 200|503, /portal/login 200,
#          /home & /work 401|302|303 (gated), /static/css/main.css 200
```
Then a manual portal pass as the test client:
- Sign in via the local IdP (§9). Confirm `/portal/` renders and the nav shows Documents/Upload/Messages/Profile/Sign-out.
- **Upload:** upload a genuine PDF → lands "Awaiting review"; upload a non-PDF renamed `.pdf` → rejected with a friendly banner (content sniffing).
- **Documents:** a staff-approved, client-visible doc downloads; a non-visible doc is absent.
- **Messages:** start a thread and reply; have staff reply from `/admin/client-portal/threads/{id}`; confirm the client sees the reply but **not** internal notes.
- **Profile:** change phone/email; confirm legal name is read-only and the change is audited.
- **Isolation:** with a second test client, confirm no cross-client documents/messages (mirrors `tests/test_portal_security_review.py`).
- **Logout:** sign out → session invalid (re-request redirects to `/portal/login`).

## 15. Rollback

- **App:** stop the process and redeploy the previous release tag/commit; restart from §12.
- **Database:** `alembic downgrade <previous_head>` **only** if a migration was applied in this staging
  deploy and is reversible; otherwise restore the staging DB from a pre-deploy snapshot. Never run
  downgrades against production.
- **Storage:** client uploads are additive and quarantined (pending); no destructive change to roll back.
- **Gates:** flipping a portal gate back OFF in the runtime snapshot immediately disables that surface.

## 16. Go / no-go before inviting a **real** client (not just a test client)

All must be true (none required for the fake-client staging exercise):
- [ ] Production portal IdP implemented + registered; MFA enforced at the IdP. **[PROVIDER DECISION]**
- [ ] Invitation + password-reset email delivery implemented. **[PROVIDER DECISION]**
- [ ] Notification email/SMS adapters implemented (or explicitly deferred). **[PROVIDER DECISION]**
- [ ] AV/malware scan on uploads implemented; encrypted-document policy decided. **[PROVIDER DECISION]**
- [ ] `SESSION_SECRET` strong and unique; `CLIENT360_ENVIRONMENT=production`; `CLIENT360_DEV_AUTH` unset.
- [ ] Data-root/vault storage provisioned, backed up, writable; `/readiness` green.
- [ ] Compliance sign-off recorded and `portal.production_signed_off` enabled in the runtime snapshot.
