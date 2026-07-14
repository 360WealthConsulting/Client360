# Firm Identity, Capability Authorization, and Audit

## Security model

Client360 authenticates staff through a provider-neutral identity port. The initial adapter implements OpenID Connect discovery, authorization-code exchange, issuer/audience validation, and signed ID-token validation. OIDC claims are normalized into `IdentityClaims`; authorization services never inspect vendor payloads.

Authorization is capability based. Roles are database-configured bundles of capabilities, and application code checks capability codes such as `client.read`, `identity.manage`, or `audit.read`. It never checks role names. Effective-dated user roles, team memberships, and record assignments allow access to change without destroying history. `record.read_all` and `record.write_all` are explicit privileged capabilities; otherwise direct person/household access requires an active assignment.

## Authentication configuration

Required production environment variables:

- `CLIENT360_ENVIRONMENT=production`
- `SESSION_SECRET` containing a strong, independently managed secret
- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET` when required by the provider
- `OIDC_REQUIRE_MFA=true`

Production sessions are signed, HTTPS-only, SameSite=Lax, limited to eight hours, persisted only as SHA-256 token hashes, and revocable. Disabling a user revokes all active sessions. The application rejects mismatched browser origins for state-changing requests and sends restrictive content, frame, MIME, and referrer headers.

## Initial administrator

After the migration, create exactly one initial administrator with:

`python -m app.security.bootstrap --email <email> --name <name> --subject <oidc-subject>`

Bootstrap fails after the first user exists. All later users, roles, memberships, and assignments are managed through the authenticated administration APIs/UI.

## Audit model

Audit events capture actor, action, protected entity, outcome, correlation/request ID, time, network/client context, and redacted metadata. Database triggers reject update and delete operations, making the table append-only. Audit events are separate from client timeline events. Sensitive metadata keys—tokens, secrets, passwords, tax identifiers, content, and message bodies—are redacted before persistence.

## Capability administration

The migration seeds least-privilege capability-composed Administrator, Advisor, Operations, and Compliance roles plus Wealth, Tax, Insurance, Operations, and Compliance teams. Role composition is data, not code. Changes take effect on the next request because effective capabilities are resolved from active role assignments for each authenticated session.

Authenticated clients can inspect the current identity and effective capability set at `GET /api/v1/session`. Every successful protected mutation and every protected document access is recorded by the security middleware in addition to domain-specific identity, assignment, authentication, and denial events.

Before production activation, leadership must review the seeded compositions, OIDC/MFA settings, privileged-capability ownership, session lifetime, assignment policy, audit retention/export controls, and segregation of duties.
