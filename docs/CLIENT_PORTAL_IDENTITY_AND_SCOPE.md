# Client Portal Identity & Scope (Phase D.43)

How external principals are identified, linked, and scoped. See
[`ADR-048`](adr/ADR-048-secure-client-portal.md).

## External identity model
- External principals are `PortalPrincipal(account_id, person_id, email, display_name)`, distinct from the
  internal staff `Principal`. The two never mix (middleware fork).
- A portal account (`portal_accounts`) links to exactly one `person_id` and carries an `auth_subject` set
  from the external identity provider at activation. Internal vs external users are therefore always
  distinguishable.
- **Explicit, auditable linking; never auto-link by email.** A portal account is linked to an identity
  subject only through the explicit `accept_invitation` step (which consumes a hashed invitation token and
  records the `auth_subject`). No path infers a link from a matching email address.

## Identity providers
`PORTAL_IDENTITY_PROVIDERS` is a registry of `PortalIdentityProvider` implementations, populated **only in
the FastAPI lifespan** (`app/main.py`) — registration is startup-only, with no hot reload. Each provider
declares two capability flags:

| Flag | Meaning |
| --- | --- |
| `supports_redirect_flow` | Implements `authorization_url()` + `exchange_code()` (browser authorization-code flow) |
| `production_capable` | May authenticate a **real** external client |

`verify_activation(assertion)` remains the posted-assertion path used by the synthetic provider.
`production_capable()` on the registry returns the keys eligible for production, and is what
`portal.gate.production_ready()` consults.

### Microsoft Entra External ID (production)
`app/portal/identity_microsoft.py` — `key="microsoft"`, redirect-capable, production-capable. It runs in a
**separate external tenant**, never the staff workforce tenant, so client identities and staff identities
are administratively distinct. (This is Entra External ID, *not* the deprecated Azure AD B2C.)

- **Authorization code + PKCE.** `/portal/auth/start` mints `state`, `nonce`, and a PKCE verifier, holds all
  three **server-side** in the session, and sends only the S256 `code_challenge` to the IdP.
  `/portal/auth/callback` compares `state` with `secrets.compare_digest` and **consumes** it, so a callback
  cannot be replayed. Both routes are pre-session, so they are listed in `PUBLIC_EXACT` and exempted in
  `portal_gate`.
- **ID-token validation.** Issuer, audience, signature (JWKS from OIDC discovery), and expiry are all
  checked, plus `nonce` equality against the server-held value.
- **Immutable subject binding.** The account key is `microsoft:<oid or sub>` — `oid` preferred. **Email is
  never the identity key** and never a fallback; a renamed or re-addressed client keeps the same account,
  and control of a mailbox does not confer access. `sign_in_with_subject()` resolves an *active* account by
  `auth_subject` alone.
- **MFA is fail-closed, and its enforcement authority is explicit.** `PORTAL_OIDC_MFA_MODE` names which
  authority proves MFA, and `_mfa_verified()` refuses anything else:
  - `claims` (**the default**) accepts a token only when its `amr`/`acr` claims match values configured
    for the tenant. With no configured values it returns **False**, so a misconfigured deployment refuses
    every sign-in rather than admitting an unverified one. The workforce tenant's AMR interpretation is
    deliberately **not** reused — the external tenant's real user-flow values must be established against
    the tenant and verified under control.
  - `conditional_access` delegates enforcement to the Entra Conditional Access policy protecting the
    portal application (`Client360 Portal - Require MFA`: state *On*, all users, target resource
    *Client360 Portal*, grant *Require multifactor authentication*). This tenant's validated production
    ID tokens carry **no `amr`, `acr` or `acrs` claim at all**, so claim evidence cannot exist and `claims`
    mode would refuse every otherwise-valid sign-in. In this mode `_mfa_verified()` is reached only after
    the authorization-code/PKCE exchange and **every** other check — signature, issuer, audience, expiry,
    nonce, immutable subject — has already passed, so the token could only have been issued to a session
    the policy already admitted.
  - Any other value is unknown configuration, proves nothing, and refuses sign-in.

  The default stays fail-closed and Conditional Access is **never inferred** from absent claim
  configuration: an operator must name the mode. Nothing downstream changes — `sign_in_with_subject()`
  still requires `mfa_verified=True`, and the mode only decides what may establish it.
- **No token retention.** Access and refresh tokens are never stored; the portal wants identity, not Graph.
  Authorization codes, tokens, client secrets, and invitation tokens are never logged.
- **Uniform errors.** Every failure returns the same `"Sign-in could not be completed."` and redirects to
  `/portal/login?error=failed`, so nothing distinguishes an unknown subject from a failed MFA or a bad
  token. Redirect targets are fixed in code — there is no caller-supplied `next`/`return_to`.

Configuration keys (`PORTAL_OIDC_*`) are declared in `app/config.py`; `PORTAL_OIDC_CLIENT_SECRET` is the
only secret. Nothing is registered until they are set, and startup warns on a partial configuration, on an
unknown `PORTAL_OIDC_MFA_MODE`, on `claims` mode with no MFA claim values, and on claim values left set
under `conditional_access` mode (where they are ignored).

### Local test provider (test only)
`app/portal/identity_local.py` provides a deterministic `LocalTestIdentityProvider` (assertion
`local:<subject>[:mfa]`) that is **never** `production_capable`. It:
- registers **only when NOT production-signed-off**, so it can never verify a real external activation in
  production;
- echoes the subject, marks MFA verified only when the `:mfa` marker is present, and returns no email
  (never auto-links).

## Scope resolver (dedicated, grant-based, fail-closed)
`portal_scope(account_id, *, permission=None)` is the dedicated external scope resolver. It:
- reads only **active** `portal_access_grants` for the account;
- when a `permission` is supplied, keeps only grants that explicitly allow it (**default-deny**), so a
  permission correlates to the specific grant covering a record, not "any grant on the account";
- resolves `household_ids`, `shared_household_ids` (joint/trusted/delegated), `person_ids`, and
  `organization_ids`;
- **never** uses `record.read_all` and **never** grants blanket household-wide member access — a member is
  reachable only through a grant that covers them.

`require_scope(...)` / `require_org_scope(...)` raise `PermissionError` when a person/household/organization
is outside the resolved set; routes translate that to a 404 that does not disclose existence.

## Access types
`self`, `joint`, `trusted`, `delegated` (and employer/organization grants). Only joint/trusted/delegated
expand to other members of the shared household; `self` reaches only the account's own person and household
and never another household's members.

## References
`app/portal/service.py` (`portal_scope`, `require_scope`, `accept_invitation`),
`app/portal/providers.py`, `app/portal/identity_local.py`, `tests/test_secure_client_portal.py`
(`test_self_grant_does_not_reach_other_household`, identity provider tests), ADR-048.
