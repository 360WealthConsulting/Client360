# ADR-030 — Enterprise Security as a metadata domain that reuses auth/RBAC/crypto/audit; never replaces login/OAuth, never stores a plaintext secret

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Security); Security / Authorization (ADR-004/ADR-005 owners);
Business Operations Owner (Michael Shelton — security-governance requirements). Authorized compliance
reviewer: Not yet designated.

## Context
The platform already enforces security at runtime: session authentication (`app/security/service.py`
— `resolve_principal`/`create_session`/`user_sessions`, only the SHA-256 token hash stored), a
provider wrapper (`app/security/authentication.py`), Microsoft 365 OAuth (`app/services/
microsoft_identity.py`, MSAL + Fernet-encrypted token cache), capability RBAC (`Principal.can`,
`require_capability`, middleware `RULES`, `capabilities`/`roles`/`role_capabilities`), record scope
(`record_in_scope`/`accessible_person_ids`/`has_record_scope`, `record.read_all`/`record.write_all`),
three fail-closed Fernet field-crypto helpers (`token_crypto`/`benefits_crypto`/`integration_crypto`),
and a tamper-evident audit hash-chain (`app/security/audit.py::write_audit_event`, `audit_events`).
These are governed by **ADR-004** (server-side authorization + record scope) and **ADR-005**
(sensitive-data redaction; restricted-vs-missing; no keys/secrets in templates).

There was **no** domain that governs the firm's *security posture* — the policies, the secret
inventory and rotation schedule, the certificate inventory, the identity/authentication provider
catalog, and the security incident/finding lifecycle. The risk of adding one is that it re-implements
authentication/RBAC, holds plaintext secrets, or is treated as a source of truth for auth state.

## Decision
Enterprise Security is a new authoritative **security domain** that owns **security metadata only**
and is **never a source of truth for business entities** and **never for live auth state**.
- **Owns:** `security_policies` (unified `policy_type`: security/session/password/mfa/access/
  capability/role/api/encryption/key_rotation/authentication/federation), `security_configurations`
  (hardening baseline), `security_identity_providers` (authentication/identity/federation, disabled
  by default), `security_secret_references`, `security_certificate_references`, `security_exceptions`,
  `security_incidents`, `security_findings`, and the **append-only** `security_events` ledger.
- **Reuses, never replaces.** Authentication, session management, Microsoft 365 OAuth, capability
  RBAC, record scope, the Fernet field crypto, and the audit hash-chain are used as-is. Creating a
  policy/provider/configuration records **intent metadata** and **does not change** the login flow,
  the OAuth flow, the middleware `RULES`, or any user's live capabilities. Provider rows are seeded/
  created **disabled** (mirroring the D.24 disabled-port pattern).
- **Never a plaintext secret.** A secret reference is a **pointer** to an existing encrypted store
  (`microsoft_accounts`, an integration credential reference, an external vault) or **Fernet
  ciphertext** via a new fail-closed `app/security/security_crypto.py` (env `SECURITY_SECRET_KEY`,
  own `SecurityKeyMissing`). Ciphertext is **stripped from every response**; policy/provider `config`
  is rejected if it contains secret-looking keys. Rotation records metadata only (last/next rotation)
  and performs no key operation on the underlying store.
- **Certificates are metadata only.** `security_certificate_references` stores subject/issuer/serial/
  fingerprint/validity window and a storage pointer — **never** a private key or PEM.
- **API security is governance metadata.** API/rate/credential/OAuth/client policies live in
  `security_policies` (`policy_type='api'`); **no API gateway** is implemented and the authentication
  middleware is unchanged (the D.24 `integration_api_clients` remain the client registry).
- **Sensitive metadata stays server-side (ADR-005).** Secret ciphertext and keys never reach a
  template or response; a present/not-present indicator is all that is exposed. `security.audit`/
  `security.admin` are sensitive capabilities.
- **Integrations:** **Automation** runs scheduled security reviews (a new `security_review` dispatch
  job flags overdue rotations / expiring certificates as findings; the `JOB_TYPES` CHECK is widened).
  **Data Governance** stays authoritative for data-quality findings; a security finding **references**
  a governance finding (`governance_finding_id`), never owns it. **Analytics** consumes security
  statistics (open findings/incidents, overdue rotations, expired certificates, MFA coverage read
  from the existing `users.mfa_enabled`); Security never depends on Analytics. **Timeline** receives
  approved, **client-anchored** lifecycle events only (incident opened/resolved, secret rotated,
  certificate renewed, policy approved on a client-anchored item); firm-level security events record
  to `security_events` only and are **not** emitted per authentication.
- **Security of the domain itself:** capabilities `security.view/manage/execute/audit*/admin*`
  (`*` = sensitive), gated **in-route** (`/security` matches no middleware RULE). Record scope is
  enforced for client-anchored incidents (ADR-004).

## Alternatives considered
1. **Re-implement authentication/RBAC inside the Security domain.** Rejected: ADR-004 keeps a single
   server-side authorization implementation; a second one is a bypass surface. Security governs
   posture; the existing modules enforce it.
2. **Store provider/secret values in the Security tables (plaintext).** Rejected: secrets are pointers
   or Fernet ciphertext; ADR-005 forbids keys/secrets leaving the server.
3. **Duplicate a Fernet helper or reuse an existing domain's key.** Rejected: each domain has its own
   fail-closed key (`token`/`benefits`/`integration`); Security gets `SECURITY_SECRET_KEY`, isolating
   blast radius. The helper mirrors the existing three exactly (no new crypto).
4. **Emit a timeline/audit event for every authentication.** Rejected: ADR-009 keeps the timeline a
   curated projection; only approved, client-anchored lifecycle events are published. Authentication
   events already flow through the outbox (`emit_authentication_event`).
5. **Build an API gateway / enforce API keys + rate limits in middleware.** Rejected: this phase is
   governance metadata only; a real gateway is a separately-approved change (future ADR).

## Reasons for the decision
The firm needs one authoritative model of *which security policies are in force, which secrets and
certificates exist and when they rotate/expire, which identity providers are configured, and which
security incidents/findings are open* — with audit and analytics — without re-implementing
authentication, without holding a plaintext secret, and without becoming a second source of truth for
auth state. A metadata domain that reuses the existing auth/RBAC/crypto/audit delivers this while
preserving ADR-004, ADR-005, and ADR-015.

## Consequences
### Positive consequences
- One authoritative security-posture domain (policies/configurations/providers/secrets/certificates/
  incidents/findings) reusing the existing authentication, RBAC, record scope, Fernet crypto, and
  audit hash-chain.
- No duplicated auth/crypto, no plaintext secrets, no API gateway; the login/OAuth/middleware are
  unchanged. Automation gains security reviews; Analytics gains security metrics; the timeline
  receives only approved client-anchored events.

### Negative consequences and tradeoffs
- Policies/configurations are **descriptive metadata**: approving a "session timeout" policy does not
  itself change the runtime timeout (the runtime posture lives in the existing modules/config). This
  is documented; wiring a policy to runtime enforcement would be a future, separately-approved change.
- Secret rotation and certificate renewal record metadata only — they do not perform the actual key
  rotation / certificate issuance against the external store.
- The D.22 `JOB_TYPES` CHECK constraints were widened again to admit `security_review` (a documented,
  reversible cross-domain migration touch).

## Enforcement
- `app/database/security_tables.py::define_security_tables` (registered in `app/database/schema.py`;
  reflected in `app/db.py`). Migration `w7a8b9c0d1e2` (9 tables + append-only `security_events` ledger
  with `prevent_security_event_mutation()` + `security_events_immutable` trigger + 5 `security.*`
  capabilities + widened automation `JOB_TYPES` + hardening configuration seeds). Services
  `app/services/security/{common,policies,providers,secrets,incidents,scans,service}.py`;
  `app/security/security_crypto.py` (fail-closed Fernet). Routes `app/routes/security.py` (in-route
  `security.*` gating; `/security` matches no middleware RULE; ciphertext stripped from responses).
  Automation `security_review` handler in `app/services/automation/dispatch.py`. The authentication,
  session, Microsoft 365 OAuth, RBAC middleware, existing Fernet helpers, audit hash-chain, and the
  D.5 golden are untouched. Security is registered in `source_producer_modules` (must not import
  composition layers). Tests: `tests/test_security_platform.py`; manifest / platform-architecture /
  route-count guards updated.

## Exceptions
None currently approved. `administrator`/`record.read_all` scope bypass remains as defined by ADR-004.

## Revisit conditions
Wiring a policy to runtime enforcement, performing real secret rotation / certificate issuance against
an external store, implementing an API gateway or API-key/rate-limit enforcement middleware, or
replacing any part of authentication/OAuth would each warrant a new or superseding ADR (and, for
auth/credential changes, security sign-off).

## References
- `app/services/security/`, `app/routes/security.py`, `app/database/security_tables.py`,
  `app/security/security_crypto.py`, migration `migrations/versions/w7a8b9c0d1e2_security_platform.py`
- Reused infra: `app/security/{service,authentication,authorization,audit,middleware,dependencies}.py`,
  `app/services/microsoft_identity.py`, `app/security/{token_crypto,benefits_crypto,integration_crypto}.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_security_platform.py`; relates to ADR-004, ADR-005, ADR-009, ADR-015, ADR-016, ADR-027,
  ADR-028, ADR-029
