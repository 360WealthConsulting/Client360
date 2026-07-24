# Identity Registry (Phase D.54)

The **identity registry** (`IDENTITY_REGISTRY` in `app/services/security_operations/registry.py`) is the
declarative catalog of the firm's user/identity classes and, for each, the **authoritative owners** it is
composed from. It is metadata only: the Security Operations layer owns no identities and authenticates
nothing — it references the owners and explains the result with a deep link. **No identity is duplicated.**

## Identity classes

Each identity class declares its `authoritative_owner` (the identity of record), `authentication_owner` (the
authoritative authentication owner), `authorization_owner` (the authoritative authorization/RBAC owner),
`runtime_gate`, and `deep_links`.

| Identity class | Authoritative owner | Authentication owner | Authorization owner |
| --- | --- | --- | --- |
| `advisor_identities` | identity | security.service | security.rbac |
| `employee_identities` | identity | security.service | security.rbac |
| `service_accounts` | identity | integration.connectors | security.rbac |
| `system_identities` | identity | security.service | security.rbac |
| `external_identities` | identity | security.providers | security.rbac |
| `client_identities` | identity | portal | security.rbac |

## Ownership boundaries (never re-implemented here)

- **Identities** are owned by `app/services/identity.py` (`list_identity_data`, `users` table). The registry
  names the identity owner; the layer **never calls** `invite_user` / `set_user_status` — governance forbids
  it. The layer never duplicates a user, role, or capability.
- **Authentication** is owned by `app/security/service.py` (`create_session`, `resolve_principal`) + OIDC
  providers. The registry names the authentication owner; the layer never authenticates or issues a token.
- **Authorization / RBAC** is owned by `app/security/rbac.py` (`resolve_capabilities`, `resolve_roles`) +
  `app/security/authorization.py`. The registry names the authorization owner; the layer never assigns a role
  or alters a permission.
- **Service-account / system-identity** concepts do not exist in the identity model today (there is no
  `is_system` on `users`); the registry declares them as classes for governance completeness, composed from
  the same Identity owner.

## How the registry is used

The identity-governance dashboard composes `registered_identities` (this registry), `user_inventory` (users
by status), and `teams_roles` (from the Identity owner). Governance validates that every identity class
declares all three owner fields + runtime gate + deep links, and that keys are unique.

See [SECURITY_REGISTRY.md](SECURITY_REGISTRY.md), [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md), and
[ADR-059](adr/ADR-059-security-operations.md).
