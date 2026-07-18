# Client360 — Object-Level Security Foundation (E2.3 / Backlog F2.3)

A canonical, **provider-agnostic** abstraction for object-level authorization that
**wraps and reuses** the existing record-scope enforcement and **preserves all
existing behavior**.

`app/security/object_security.py`

## Mapping to the existing implementation (reconciliation)
| Existing | Role | F2.3 |
|---|---|---|
| `policy.has_record_scope` | Low-level DB check: bypass caps `record.read_all`/`record.write_all` + a `record_assignments` row | Reached via the default policy |
| `authorization.record_in_scope` | Canonical per-record access helper (delegates to `has_record_scope`, manages connection, honors `write`) | **Delegated to** by `RecordScopePolicy` — enforcement unchanged |
| `authorization.organization_in_scope` / `benefits_in_scope` | Anchored record-scope variants | Left as-is (a future policy/anchor can wrap them) |
| `record_assignments` | Ownership/assignment model (user/team ↔ entity, effective-date window) | Surfaced via `resolve_assignments` / `resolve_owners` |

F2.3 **formalizes** these behind a service + models; it does not replace them and
introduces no new enforcement.

## Concepts
| Type | Purpose |
|---|---|
| `ObjectRef` | A secured object — `entity_type` + `entity_id`; `ref` = `"person:42"`. |
| `ObjectSecurityContext` | The subject (`Principal`) and optional DB connection for a decision. |
| `ObjectAccessResult` | Decision: `allowed`, entity, `write`, `user_id`, `reason`. Truthy when allowed. |
| `ObjectSecurityPolicy` (Protocol) | Provider-agnostic evaluation contract. |
| `RecordScopePolicy` | Default policy — delegates to `record_in_scope` (identical enforcement). |
| `ObjectSecurityService` | `can_access` / `evaluate` / `require`. |

## API
```python
from app.security.object_security import (
    ObjectRef, ObjectSecurityContext, default_object_security_service,
    resolve_assignments, resolve_owners,
)

svc = default_object_security_service()
ctx = ObjectSecurityContext.for_principal(principal)            # from AuthenticationMiddleware's Principal
svc.can_access(ctx, ObjectRef("person", 42))                   # read
svc.can_access(ctx, ObjectRef("household", 7), write=True)     # write
svc.require(ctx, ObjectRef("person", 42))                      # raises ObjectAccessDenied if denied

resolve_assignments("person", 42)   # active assignment rows (user/team, assignment_type)
resolve_owners("person", 42)        # frozenset[user_id] with an active user-assignment
```

## Enforcement preservation
`RecordScopePolicy.evaluate` calls `record_in_scope(principal, entity_type,
entity_id, write=..., connection=...)` — the **same** enforcement the app already
uses (bypass capabilities + record assignments). A test
(`test_access_requires_assignment_and_agrees_with_record_in_scope`) asserts the
service agrees with `record_in_scope`. The existing enforcement path
(`record_in_scope`, `has_record_scope`, dependencies, middleware) is **unchanged**.

## Policy abstraction & extension points
Register alternative object policies for future **tenant isolation, Active
Directory groups, or delegated administration**:
```python
class MyPolicy:
    policy_name = "tenant"
    def evaluate(self, context, obj, write): ...   # -> ObjectAccessResult
register_object_policy(MyPolicy())
ObjectSecurityService(get_object_policy("tenant"))
```

## Object-security events (F1.3 + F1.4)
`emit_object_access_event(conn, result)` publishes `object.access_granted` /
`object.access_denied` via the transactional outbox using the event envelope.
- **Reference-only payload** (`user_id`, `entity_type`, `entity_id`, `write`,
  `allowed`) and `subject_ref="<type>:<id>"` — never PII.
- Written in the caller's transaction. **Extension point** — not wired into the
  existing enforcement path (unchanged).

## Compatibility guarantees
- **Epic 1, F2.1, F2.2 preserved** — composes with outbox/envelope/registry, the
  Authentication Foundation, and the Authorization Foundation; changes none.
- **Existing object security preserved** — `record_in_scope` / `has_record_scope` /
  `record_assignments` reused unchanged; no schema change; no route/behavior change.
- **Provider-agnostic** — stable for future field-level security, tenant isolation,
  Microsoft identity / AD groups, delegated administration, and audit policy.
- **Object-level (record) only** — no field-level or business decisions.

## Scope boundary
F2.3 delivers the object-security foundation. **Field-level security, tenant
isolation, workflow approval rules, business authorization logic, audit policy, and
delegated administration are later backlog features** and are not implemented here.

## Known gaps / future (non-blocking)
- Organization/benefits anchored scope (`organization_in_scope`,
  `benefits_in_scope`) is not yet wrapped by a dedicated policy (a future policy can
  do so without changing enforcement).
- Enforcement paths do not yet emit object-security events (extension point ready).
- No non-record-scope policy is implemented (extension point ready).
