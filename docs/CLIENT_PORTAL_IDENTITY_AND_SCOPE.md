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
- `PORTAL_IDENTITY_PROVIDERS` is a registry of `PortalIdentityProvider` implementations. Activation calls
  `verify_activation(assertion) -> PortalIdentityResult(subject, mfa_verified, email)`.
- Production integrates a real IdP. For local/test/CI, `app/portal/identity_local.py` provides a
  deterministic `LocalTestIdentityProvider` (assertion `local:<subject>[:mfa]`) that:
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
