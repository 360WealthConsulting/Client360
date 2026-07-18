# Client360 — Authorization (RBAC) Foundation (E2.2 / Backlog F2.2)

A canonical, **provider-agnostic** authorization abstraction over the existing
capability-based RBAC. It **wraps and reuses** the current model and **preserves
all existing enforcement behavior** — the default policy evaluates exactly the
same capability-membership check the app already uses.

`app/security/rbac.py`

## What already exists (reused, unchanged)
- Tables: `users → user_roles → roles → role_capabilities → capabilities`.
- `Principal.can(code)` — capability check; `Principal.capabilities` is resolved at
  session time.
- `app.security.policy.capability_codes_query(...)` — resolves a user's capability
  codes; `dependencies.require_capability(code)` — the FastAPI enforcement.
- `app.security.policy.has_record_scope` / `app.security.authorization.py` —
  **record/object-level** scope (**out of F2.2 scope**, untouched).

## Concepts
| Type | Purpose |
|---|---|
| `Permission` | A required permission — a capability `code`. |
| `AuthorizationContext` | A principal's authorization view: `user_id`, `capabilities`, `roles`, provider. `from_principal(...)`; `has(permission)`. |
| `AuthorizationResult` | Decision: `allowed`, `permission`, `user_id`, `reason`. Truthy when allowed. |
| `Policy` (Protocol) | Provider-agnostic evaluation contract. |
| `CapabilityPolicy` | Default policy — permit iff the capability is in the context (identical to `Principal.can`). |
| `AuthorizationService` | `authorize` / `is_permitted` / `require` against a policy. |

## API
```python
from app.security.rbac import (
    AuthorizationContext, default_authorization_service,
    authorization_context, resolve_capabilities, resolve_roles,
)

svc = default_authorization_service()
ctx = AuthorizationContext.from_principal(principal)      # capabilities already on Principal
svc.is_permitted(ctx, "insurance.read")                  # bool
svc.authorize(ctx, "insurance.read")                     # AuthorizationResult
svc.require(ctx, "insurance.read")                        # raises AuthorizationDenied if not allowed

resolve_capabilities(user_id)                             # frozenset[str] (reuses capability_codes_query)
resolve_roles(user_id)                                    # tuple[str, ...] (active role codes)
authorization_context(principal, include_roles=True)      # context enriched with role codes
```

## Enforcement preservation
`CapabilityPolicy` evaluates `permission.code in context.capabilities` — the **same
semantics** as `Principal.can` / `require_capability`. A test
(`test_service_agrees_with_principal_can`) asserts the abstraction agrees with the
existing check for granted and non-granted codes. The existing enforcement path
(middleware, `require_capability`, record scope) is **unchanged**.

## Policy abstraction & extension points
Register alternative policies for future **Active Directory / Microsoft Entra ID
groups, SAML/OIDC claims, or delegated administration**:
```python
class MyPolicy:
    policy_name = "entra-groups"
    def evaluate(self, context, permission): ...   # -> AuthorizationResult
register_policy(MyPolicy())
AuthorizationService(get_policy("entra-groups"))
```
The abstraction is provider-agnostic; new authorization sources slot in without
changing the context/result models or existing enforcement.

## Authorization events (F1.3 + F1.4)
`emit_authorization_event(conn, result)` publishes `authorization.granted` /
`authorization.denied` via the transactional outbox using the event envelope.
- **Reference-only payload** (`user_id`, `permission`, `allowed`) and
  `subject_ref="user:<id>"` — never PII.
- Written in the caller's transaction. **Extension point** — not wired into the
  existing enforcement path (unchanged).

## Compatibility guarantees
- **Epic 1 & F2.1 preserved** — composes with outbox/envelope/registry and the
  Authentication Foundation; changes none of them.
- **Existing RBAC preserved** — capability model, `Principal`, `require_capability`,
  and enforcement semantics reused unchanged; no schema change.
- **Provider-agnostic** — stable for future object security, field security, tenant
  isolation, AD/Entra ID groups, SAML/OIDC claims, and delegated administration.
- **Authorization (capability) only** — no object/field/record-level decisions.

## Scope boundary
F2.2 delivers the RBAC/authorization foundation. **Object ownership, row-level
security, field-level security, business approval workflows, and audit policy
enforcement are later backlog features** and are not implemented here.

## Known gaps / future (non-blocking)
- Record/object scope (`has_record_scope`, `authorization.py`) is intentionally
  separate and unwrapped (later object-security feature).
- Enforcement paths do not yet emit authorization events (extension point ready).
- No non-capability policy is implemented (extension point ready).
