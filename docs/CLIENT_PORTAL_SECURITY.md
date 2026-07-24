# Client Portal Security (Phase D.43)

The portal serves external principals over the same platform that serves staff. Its security posture is
**fail-closed and gated by default**. See [`ADR-048`](adr/ADR-048-secure-client-portal.md) and
[`CLIENT_PORTAL_ARCHITECTURE.md`](CLIENT_PORTAL_ARCHITECTURE.md).

## Authentication & sessions
- Activation and sign-in delegate to an external identity provider (`PortalIdentityProvider`); there is no
  local password store. Production integrates a real IdP; local/test uses a deterministic offline provider
  that only registers when NOT production-signed-off (see
  [`CLIENT_PORTAL_IDENTITY_AND_SCOPE.md`](CLIENT_PORTAL_IDENTITY_AND_SCOPE.md)).
- Invitations use **hashed tokens** (`sha256`), expiry, single-use acceptance, and replay protection
  (`with_for_update`, `accepted_at`/`used_at` guards). Tokens are never written to logs, never returned by
  the internal admin invite endpoint (delivery is out-of-band), and invalid/expired tokens fail with a safe
  message that does not disclose whether an account exists.
- MFA is required by default (`portal.mfa_required` defaults ON); `accept_invitation` refuses without MFA.
- Portal sessions are hashed, device-bound, expiring, and cryptographically isolated from staff sessions.

## Gating (no environment fallback)
Every external capability is evaluated through the governed Runtime Engine
(`runtime.consumption.feature_enabled`) with a **production-safe default of OFF** and no raw environment
fallback (`app/portal/gate.py`). External production access additionally AND-gates on a compliance sign-off
gate (`portal.production_signed_off`), which is OFF by default — so external client data is never served in
production until compliance records a decision (see
[`CLIENT_PORTAL_COMPLIANCE_GATE.md`](CLIENT_PORTAL_COMPLIANCE_GATE.md)).

## Authorization (grant-based, default-deny)
The portal authorizes via `portal_access_grants`, not RBAC. `portal_scope(account_id, permission=...)`
resolves only the person/household/organization ids reachable through a grant that explicitly allows the
requested permission (default-deny). Household access does NOT grant every member automatically, and no
portal path ever uses `record.read_all`. Out-of-scope access raises `PermissionError` and routes map it to
a 404 that does not disclose existence. See
[`CLIENT_PORTAL_IDENTITY_AND_SCOPE.md`](CLIENT_PORTAL_IDENTITY_AND_SCOPE.md).

## Data minimization & masking
Only fields declared in the visibility registry are ever externally served. Account numbers are masked to
last-4 (`mask_account_number`), the financial summary is a minimized read with a freshness marker, and the
portal never exposes internal notes, assignments, compliance reasoning, audit logs, advisor work, AI
briefs, or work-queue state.

## Transport headers
The portal branch sets `x-content-type-options: nosniff`, `referrer-policy: same-origin`,
`x-frame-options: DENY`, and a strict `content-security-policy` (`default-src 'self'; frame-ancestors
'none'; base-uri 'self'`).

## Audit
Every external mutation is audited (references only) via the authoritative audit ledger — no PII, no
tokens. Consent changes and admin invite/revoke actions each write an audit event.

## References
`app/portal/{gate,service,financial,consent,visibility}.py`, `app/security/middleware.py`,
`app/routes/portal_admin.py`, `tests/test_secure_client_portal.py`, ADR-048.
