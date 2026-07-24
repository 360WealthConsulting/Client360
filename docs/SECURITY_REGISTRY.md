# Security Registry (Phase D.54)

The **security registry** (`SECURITY_REGISTRY` in `app/services/security_operations/registry.py`) is the
declarative catalog of the firm's security domains and, for each, the **authoritative owners** it is composed
from. It is metadata only: the Security Operations layer owns no authentication, MFA, session, audit, or
policy state — it references the owners and explains the result with a deep link.

## Security domains

Each domain declares its `category`, `authoritative_owner`, `provider_owner`, `monitoring_owner`,
`runtime_gate`, and `deep_links`.

| Domain | Category | Authoritative owner | Provider owner | Monitoring owner |
| --- | --- | --- | --- | --- |
| `authentication` | authentication | security.service | security.providers | security.service |
| `mfa` | mfa | security.service | security.policies | analytics |
| `sessions` | session | security.service | security.service | security.audit |
| `audit` | audit | security.audit | security.audit | security.audit |
| `policies` | policy | security.policies | security.policies | security.service |
| `monitoring` | monitoring | security.incidents | observability | observability |

## Ownership boundaries (never re-implemented here)

- **Authentication** is owned by `app/security/service.py` + the OIDC providers (`security.providers`
  metadata). The registry names the authentication + provider owner; the layer never authenticates.
- **MFA** is owned by the `users.mfa_enabled` flag (set by authentication) and surfaced via the Analytics
  Registry counter `security_mfa_enabled_users`. The registry names MFA as a domain; the layer computes a
  coverage indicator only — never a second MFA provider.
- **Sessions** are owned by `user_sessions` (`create_session` / `revoke_session` — writes never called);
  session activity is surfaced through the hash-chain audit log. The registry names the session owner.
- **Audit** is owned by the hash-chain audit log `app/security/audit_export.py` (`read_audit_events`,
  `verify_integrity`). The registry names the audit owner; the layer never writes an audit event.
- **Policies** are owned by `security.policies`; **monitoring** by `security.incidents` + `observability`.
  The layer composes their read-only metrics/status.

## How the registry is used

The dashboards compose the security-domain reads: `security_providers`, `mfa_policies`, `security_overview`,
`open_incidents`, and the audit panels, plus `registered_security_domains` (this registry). Governance
validates that every domain declares all six fields (category, authoritative / provider / monitoring owner,
runtime gate, deep links), that keys are unique, and that the layer contains no auth/audit **mutation** call.

See [IDENTITY_REGISTRY.md](IDENTITY_REGISTRY.md), [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md), and
[ADR-059](adr/ADR-059-security-operations.md).
