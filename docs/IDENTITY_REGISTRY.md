# Identity Registry (Phase D.65)

> **Two complementary identity registries, one authoritative owner.** There are two read-only composition
> registries named `IDENTITY_REGISTRY`: the **D.54 Security Operations** facet
> (`app/services/security_operations/registry.py` — 6 identity *classes*, one facet of the broader security
> posture) and this **D.65 Identity Governance** layer
> (`app/services/identity_governance/registry.py` — 8 identity *domains* in a dedicated identity / role /
> capability / authentication / authorization composition). **Both are read-only views over the SINGLE
> authoritative identity owner (`app/services/identity.py` + Security RBAC); neither owns, persists, or
> duplicates any identity.** Multiple composition layers over shared authoritative owners is the established
> platform pattern (the "no second system" rule bans a second identity provider / RBAC engine / store, not a
> second read-only view). The D.54 facet is described at the end of this document.

`IDENTITY_REGISTRY`, `CAPABILITY_REGISTRY`, and `AUTHENTICATION_REGISTRY` in
`app/services/identity_governance/registry.py` are declarative catalogs of the **8 identity domains**, **6
capability domains**, and **7 authentication domains** the firm's platform actually has. Metadata only — they
define no identity provider, directory, or user-management platform. Each entry names its authoritative owner,
read surface, **prohibited mutation surface** (the mutating entry point this layer must NEVER call), evidence
source, capabilities, runtime gate, identity scope, deep links, and config status.

## Identity domains (8)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| user_directory | identity | `list_identity_data` (users) | `invite_user` | configured |
| user_status | identity | `list_identity_data` (users.status) | `set_user_status` | configured |
| mfa_coverage | identity | `list_identity_data` (users.mfa_enabled) | `set_user_status` | configured |
| team_directory | identity | `list_identity_data` (teams) | `add_team_membership` | configured |
| session_management | security.service | `authentication.list_providers` | `create_session` | configured |
| identity_lifecycle | not_configured | n/a | n/a | **not_configured** |
| service_accounts | not_configured | n/a | n/a | **not_configured** |
| account_provisioning | not_configured | n/a | n/a | **not_configured** |

## Capability domains (6)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| capability_inventory | security.rbac | `list_identity_data` (capabilities) | `compose_role` | configured |
| sensitive_capabilities | security.rbac | `list_identity_data` (capabilities.sensitive) | `compose_role` | configured |
| capability_coverage | security.rbac | `list_identity_data` | `compose_role` | configured |
| least_privilege_indicators | security.rbac | `list_identity_data` (derived) | `compose_role` | configured |
| segregation_of_duties | not_configured | n/a | n/a | **not_configured** |
| entitlement_review | not_configured | n/a | n/a | **not_configured** |

## Authentication domains (7)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| authentication_providers | security.authentication | `list_providers` | `register_provider` | configured |
| session_inventory | security.service | `list_providers` | `create_session` | configured |
| mfa_enrollment | identity | `list_identity_data` (mfa_enabled) | `set_user_status` | configured |
| sso_providers | not_configured | n/a | n/a | **not_configured** |
| mfa_enforcement | not_configured | n/a | n/a | **not_configured** |
| api_authentication | not_configured | n/a | n/a | **not_configured** |
| password_management | not_configured | n/a | n/a | **not_configured** |

## The honest gaps

Identity lifecycle (JML), service accounts, account provisioning, segregation of duties, entitlement review,
SSO / external IdP, MFA enforcement, API-key / token authentication, and password management have **no
authoritative owner** in the platform — authentication is external (claims / auth_subject), only the `session`
provider is registered, and the `mfa_enabled` flag is enrollment, not enforcement. These are declared
`not_configured` and reported honestly, never a fabricated identity, provider, or session. **Counts, coverage,
and ratios only — never a password, token, session ID, or raw identity.**

## Sibling: the D.54 Security Operations identity registry

`app/services/security_operations/registry.py`'s `IDENTITY_REGISTRY` declares 6 identity **classes**
(`advisor_identities`, `employee_identities`, `service_accounts`, `system_identities`, `external_identities`,
`client_identities`), each naming its `authoritative_owner` (identity of record), `authentication_owner`, and
`authorization_owner`. It is the identity facet of the broader Security Operations posture layer (gated by
`identity.enabled`, surfaced at `/security-operations`). D.65 is the dedicated, deeper identity / access
governance layer (surfaced at `/identity-governance`). Both compose the same authoritative `identity` + Security
RBAC owners — neither duplicates them. See [ADR-059](adr/ADR-059-security-operations.md) and
[SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md).

## References
- `app/services/identity_governance/registry.py` (`IDENTITY_REGISTRY`, `CAPABILITY_REGISTRY`,
  `AUTHENTICATION_REGISTRY`, `_e`)
- `docs/ENTERPRISE_IDENTITY_GOVERNANCE.md`, `docs/IDENTITY_GOVERNANCE.md`, ADR-070
