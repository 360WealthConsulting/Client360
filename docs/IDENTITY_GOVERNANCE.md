# Identity Governance (Phase D.65)

`app/services/identity_governance/governance.py` is a read-only checker that verifies the identity-governance
layer stays a **composition** over the authoritative identity / role / capability / authentication /
authorization owners and never becomes a second identity provider / authentication service / authorization
engine / RBAC system / directory / SSO platform / policy engine / user-management platform.
`validate_identity_governance()` returns `{ok, issue_count, findings}` and **never raises** into normal use (any
internal error is captured as a single finding).

## Invariants enforced

1. **No persistence / no writes.** No module defines a table, writes the DB (`.insert()` / `.update()` /
   `.delete()` / `sa.*`), publishes to the outbox (`publish_safe` / `publish_event`), or writes audit events
   (`write_audit`). The layer only composes reads — no shadow identity / role / capability / session / policy
   store.
2. **No mutation / no duplicate engine.** The layer never authenticates, authorizes, assigns a role, grants or
   revokes a permission, modifies a policy, creates an identity, or creates a session. `_FORBIDDEN_CALLS` scans
   for `invite_user(`, `set_user_status(`, `assign_role(`, `compose_role(`, `register_policy(`,
   `register_provider(`, `create_session(`, `revoke_session(`, `assign_record(`, `add_team_membership(`,
   `authenticate(`, `authorize(`, `grant(`, `revoke(`, `write_audit(`, `publish_safe(`, `publish_event(`.
3. **No raw environment gating.** No `os.getenv` / `os.environ` — gates flow through the Runtime Engine only.
4. **No second metrics registry.** No `_DEFS =` / `class Metric` in the layer.
5. **Reuses authoritative reads.** `service.py` + `panels.py` must reference the authoritative owners
   (identity / security.rbac / security.authentication / policy / security.authorization) AND the identity
   owner (`list_identity_data`).
6. **Explainability enforced.** `is_explainable` present in both `model.py` and `panels.py` (a non-explainable
   panel is never emitted).
7. **Registry integrity.** Every registry key is unique **across all five registries**; every **configured**
   entry names an authoritative owner (a configured entry with `owner == not_configured` is a finding); every
   entry is complete (owner + capabilities + deep links + runtime gate); config_status is one of `configured` /
   `not_configured`.
8. **Panel / dashboard integrity.** Every panel names owner + source + deep link + explanation + permission;
   every dashboard names owner + audience + gate + navigation + panels + required capabilities + governing
   services, references only registered panels, and has a valid lifecycle.
9. **Derived labeling.** Any value computed by the layer (`source` starting `identity_governance.compose`) must
   be labeled `derived` — an unlabeled derived summary is a finding.
10. **Governed gates present.** `gate.GATES` is non-empty.

## The honesty stance

Governance does not — and cannot — assert that a user was authenticated, a request was authorized, a role was
assigned, a permission was granted, or an access review was certified. Those are owned by the security-critical
authoritative owners (a second source of authorization truth would be a security vulnerability), and the
genuinely-absent domains (SSO, MFA enforcement, service accounts, API-key identities, access reviews, PAM,
segregation of duties, identity lifecycle, password management) are `not_configured` and reported honestly. The
checker's job is to keep the composition honest: **no fabricated user, identity, role, permission,
authentication provider, session, capability, policy assignment, or access review**, no exposure of any
password / secret / token / session ID / credential / raw identity / privileged-role membership / user-level
permission map, and no inferred authorization at record scope.

## Where it runs

- `tests/test_identity_governance.py` asserts `validate_identity_governance()["ok"]` is `True`.
- `app/services/identity_governance/diagnostics.py` surfaces the governance report on the
  `observability.audit`-gated diagnostics route.

## References
- `app/services/identity_governance/governance.py`, `diagnostics.py`
- `docs/ENTERPRISE_IDENTITY_GOVERNANCE.md`, `docs/IDENTITY_REGISTRY.md`, `docs/ROLE_REGISTRY.md`,
  `docs/AUTHORIZATION_REGISTRY.md`, ADR-070
