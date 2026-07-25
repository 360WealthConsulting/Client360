# Role Registry (Phase D.65)

`ROLE_REGISTRY` in `app/services/identity_governance/registry.py` is a declarative catalog of the **7 role
domains** the firm's platform actually has. Metadata only — it defines no RBAC system and never assigns a role
or recomposes a role's capabilities. Five domains are **configured** (composed from the Identity service /
Security RBAC); two are genuinely absent (`not_configured`).

## Role domains (7)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| role_inventory | security.rbac | `list_identity_data` (roles) | `compose_role` | configured |
| role_activation | security.rbac | `list_identity_data` (roles.active) | `compose_role` | configured |
| system_roles | security.rbac | `list_identity_data` (roles.system_role) | `compose_role` | configured |
| user_role_assignments | security.rbac | `resolve_roles` | `assign_role` | configured |
| role_capability_mappings | security.rbac | `resolve_capabilities` | `compose_role` | configured |
| birthright_roles | not_configured | n/a | n/a | **not_configured** |
| role_certification | not_configured | n/a | n/a | **not_configured** |

## What it exposes (and never exposes)

The role panels expose **counts and coverage ratios only** — the number of roles, active vs inactive, system vs
custom, and a DERIVED role-capability governance-posture indicator (roles + capabilities + system-role count).
**A role definition is not an assignment.** The detailed per-role capability map and per-user role membership
are surfaced ONLY at the authoritative admin surface (`/admin`, gated by `identity.manage`) — never in the
governance composition. Assignment is enforced by the authoritative `assign_role` with a ceiling check (a
`role.manage` holder cannot assign a role granting capabilities they do not hold); this layer never assigns.

## The honest gaps

Birthright roles / an entitlement catalog and role certification / access review have **no authoritative owner**
in the platform — declared `not_configured`, never a fabricated assignment or certification.

## References
- `app/services/identity_governance/registry.py` (`ROLE_REGISTRY`, `_e`)
- `docs/ENTERPRISE_IDENTITY_GOVERNANCE.md`, `docs/IDENTITY_GOVERNANCE.md`, ADR-070
