# Authorization Registry (Phase D.65)

`AUTHORIZATION_REGISTRY` in `app/services/identity_governance/registry.py` is a declarative catalog of the **7
authorization domains** the firm's platform actually has. Metadata only — it defines no authorization engine and
never makes an authorization decision, registers a policy, or assigns a record. Five domains are **configured**
(composed from Security RBAC, the Policy engine, and Security Authorization); two are genuinely absent
(`not_configured`).

## Authorization domains (7)

| Domain | Owner | Read surface | Prohibited mutation | Config |
| --- | --- | --- | --- | --- |
| authorization_policies | security.rbac | `list_policies` | `register_policy` | configured |
| policy_engine_coverage | policy | `registry.coverage` | policy mutation | configured |
| record_scope_authorization | security.authorization | `record_in_scope` | `assign_record` | configured |
| capability_policy | security.rbac | `list_policies` | `register_policy` | configured |
| authorization_events | security.rbac | `emit_authorization_event` | n/a (append-only ledger) | configured |
| privileged_access_management | not_configured | n/a | n/a | **not_configured** |
| authorization_certification | not_configured | n/a | n/a | **not_configured** |

## What it exposes (and never exposes)

The authorization panels expose **counts, coverage, and status only** — the number of registered RBAC policies,
policy-engine decision-area coverage, whether the Capability policy is the registered default, whether
record-scope authorization is configured, and whether authorization events flow to the append-only audit
ledger. **Never an authorization decision, a policy payload, or an event payload.** The record-scope
authorization panel reports only that the owner (`record_in_scope` over `record_assignments`) is configured; a
per-record decision is composed ONLY at record scope (Client 360 / Household 360), and there it exposes ONLY the
current principal's OWN decision — **never another identity, a privileged role, a firm-wide permission map, or
an inferred authorization.**

## The honest gaps

Privileged access management (PAM) and authorization certification / access review have **no authoritative
owner** in the platform — declared `not_configured`, never a fabricated review or a certified decision.

## References
- `app/services/identity_governance/registry.py` (`AUTHORIZATION_REGISTRY`, `_e`)
- `docs/ENTERPRISE_IDENTITY_GOVERNANCE.md`, `docs/IDENTITY_GOVERNANCE.md`, ADR-070
