# Client360 — Authentication Foundation (E2.1 / Backlog F2.1)

A thin, **provider-agnostic** authentication layer that establishes the canonical
identity abstraction future Identity & Security features build on. It **wraps and
reuses** the existing session authentication — it does not reimplement it — and
makes **no authorization decisions** (RBAC/capabilities and object/field
permissions are later backlog features).

`app/security/authentication.py`

## What already exists (reused, unchanged)
- `AuthenticationMiddleware` resolves the session token and sets
  `request.state.principal`.
- `app.security.service.resolve_principal(token)` validates a session (not revoked,
  not expired, active user) and returns a `Principal(user_id, email, display_name,
  capabilities)`. `create_session` / `revoke_session` manage sessions.

F2.1 adds a stable façade over this so future features don't reach into middleware
internals or the `Principal` shape directly.

## Concepts
| Type | Purpose |
|---|---|
| `AuthenticatedIdentity` | Provider-agnostic *who* — `user_id`, `email`, `display_name`, `provider`, `authenticated_at`. **No capabilities** (authentication ≠ authorization). `subject_ref` = `"user:<id>"` for events/logs (never leaks PII). |
| `AuthenticationContext` | Per-request state — identity (or none), provider, session-present, request_id. `is_authenticated`. |
| `AuthenticationProvider` (Protocol) | Pluggable provider contract — `provider_name` + `resolve_identity(token)`. |
| `SessionAuthenticationService` | Default provider; delegates to `resolve_principal` (no duplication). |

## Lifecycle & API
```python
from app.security.authentication import (
    resolve_identity, authenticate_token, current_context,
    register_provider, get_provider, AuthenticatedIdentity,
)

resolve_identity(token)                 # -> AuthenticatedIdentity | None (session validation)
authenticate_token(token)               # -> AuthenticationContext (anonymous if invalid/absent)
current_context(request)                # -> AuthenticationContext from request.state (read-only)
AuthenticatedIdentity.from_principal(p) # bridge from the existing Principal
```

`current_context(request)` projects the middleware-populated `request.state`
(`principal`, `request_id`) into a context. It performs no authorization and does
not change middleware behavior — it is the stable accessor future features should
use.

## Extension points (provider-agnostic)
Register additional providers for future **Microsoft identity, Active Directory,
SSO, or MFA**:
```python
class MyProvider:
    provider_name = "sso"
    def resolve_identity(self, token): ...   # -> AuthenticatedIdentity | None
register_provider(MyProvider())
resolve_identity(token, provider="sso")
```
The interface is credential/token-agnostic, so new mechanisms slot in without
changing the identity or context models.

## Authentication events (F1.3 + F1.4)
`emit_authentication_event(conn, event_type, identity=...)` publishes a canonical
event via the transactional outbox (F1.3) using the event envelope (F1.4):
- Event types: `identity.authenticated`, `identity.authentication_failed`,
  `identity.session_revoked`.
- **Reference-only payload** (`user_id`, `provider`) and `subject_ref="user:<id>"`
  — **never** email or other PII (Constitution §9).
- Written in the caller's transaction (atomic with the auth state change).

This is an **extension point**: it is available and tested, but not wired into the
existing login flow (that flow is unchanged). A future feature can call it inside
the session-creation transaction to emit login events.

## Compatibility guarantees
- **Epic 1 preserved** — composes with the outbox (F1.3), envelope (F1.4), and
  workflow registry (F1.5); changes none of them.
- **Existing auth preserved** — middleware, sessions, and `resolve_principal` are
  reused unchanged; no schema change; no route/behavior change.
- **Provider-agnostic** — stable for future RBAC, object security, Microsoft
  identity, Active Directory, SSO, and MFA.
- **Authentication only** — no authorization decisions are made here.

## Scope boundary
F2.1 delivers the authentication foundation. **Authorization, RBAC, permissions,
field-level security, and audit policy are subsequent backlog features** and are
not implemented here.

## Known gaps / future (non-blocking)
- Login/logout flows do not yet emit authentication events (extension point ready);
  wiring is a future step.
- MFA is represented only as a provider extension point; no MFA provider is
  implemented.
- `current_context` is a read-only projection; if desired, a future step could set
  `request.state.auth_context` in middleware (a deliberate, separate change).
