# Client360 — Field-Level Security Foundation (E2.4 / Backlog F2.4)

A canonical, **provider-agnostic, transport-agnostic** abstraction for field
visibility and redaction that **wraps and reuses** the existing implementation and
**preserves all existing behavior**.

`app/security/field_security.py`

## Mapping to the existing implementation (reconciliation)
| Existing | Role | F2.4 |
|---|---|---|
| `redaction.SENSITIVE` (regex `token\|secret\|password\|tax\|ssn\|content\|body`) | Sensitive-field-name **classification** | **Reused as-is** via `is_sensitive()` — no new classification invented |
| `redaction.redact_metadata(dict)` | Masks sensitive keys with `[REDACTED]` (used by `audit.write_audit_event`) | The default policy reproduces this exactly; **agreement-tested**; `redaction.py` and its callers unchanged |

F2.4 **formalizes** these behind a service + models; it does not replace them and
introduces no new enforcement.

## Concepts
| Type | Purpose |
|---|---|
| `FieldDescriptor` | A field by `name`; `.sensitive` via the existing classification. |
| `FieldSecurityContext` | The subject (`Principal`, optional — the default policy is name-based). |
| `FieldAccessResult` | Decision: `field`, `visibility`, `user_id`, `reason`. **No value.** |
| Visibility | `visible` · `masked` · `omitted` · `denied`. |
| `FieldSecurityPolicy` (Protocol) | Decides from field **name + context** (never the value). |
| `SensitiveNameRedactionPolicy` | Default — mask sensitive names, show the rest (= `redact_metadata`). |
| `FieldSecurityService` | `evaluate` / `visibility` / `apply` / `redact_mapping`. |

## API
```python
from app.security.field_security import (
    default_field_security_service, FieldSecurityContext,
)
svc = default_field_security_service()
ctx = FieldSecurityContext.for_principal(principal)   # or .system()

svc.visibility(ctx, "ssn")                 # "masked"
svc.apply(ctx, "ssn", "123-45-6789")       # ("masked", "[REDACTED]")
svc.redact_mapping(ctx, {"ssn": "…", "name": "Bob"})   # {"ssn": "[REDACTED]", "name": "Bob"}
```
With the default policy, `redact_mapping(...)` is **identical** to
`redaction.redact_metadata(...)` (proven by `test_redact_mapping_agrees_with_redact_metadata`).

## Visibility decisions (deterministic)
- **visible** — value shown as-is.
- **masked** — value replaced with the constant `MASK_TOKEN = "[REDACTED]"`.
- **omitted** — field removed from output.
- **denied** — access denied (field dropped; used by fail-closed).

Masking is a constant token, so behavior is **deterministic** for the same policy,
context, and value.

## Security & privacy guarantees
- **Fail closed** — if a policy raises or returns an unknown visibility, the field
  is `DENIED` and dropped; a field that cannot be evaluated is **never exposed**.
- **No value leakage** — `FieldAccessResult` and field-security **events** carry the
  field *name* + decision only, **never the value** (Constitution §9).
- **Policies never see values** — decisions are made from the field name + context,
  so a policy cannot leak a value.
- **Deterministic** — same policy/context/value ⇒ same decision and output.
- **Existing behavior preserved** — `redaction.py`, `audit.write_audit_event`, and
  all response/serialization behavior are unchanged.

## Policy abstraction & extension points
Register alternative policies for future **compliance-specific redaction, export
controls, or capability-gated field access**:
```python
class MyPolicy:
    policy_name = "compliance"
    def evaluate(self, context, descriptor): ...   # -> FieldAccessResult (name+context only)
register_field_policy(MyPolicy())
FieldSecurityService(get_field_policy("compliance"))
```

## Field-security events (F1.3 + F1.4)
`emit_field_security_event(conn, result)` publishes `field.masked` / `field.omitted`
/ `field.denied` via the transactional outbox using the event envelope (nothing for
`visible`). The payload is **reference-only** — `user_id`, field `name`, visibility —
**never the value**. Written in the caller's transaction. Extension point — not
wired into the existing redaction path (unchanged).

## Compatibility guarantees
- **Epic 1, F2.1–F2.3 preserved** — composes with outbox/envelope/registry, auth,
  authz, and object security; changes none.
- **Existing redaction preserved** — classification and masking reused unchanged;
  no schema change; no route/response change.
- **Provider-agnostic & transport-agnostic** — stable for future tenant isolation,
  Microsoft identity / AD groups, delegated administration, audit policy,
  compliance-specific redaction, and export/reporting controls.
- **Field visibility/redaction only** — no business, tenant, or compliance logic.

## Classification note (per acceptance criteria)
A canonical sensitive-data classification **already exists** (`redaction.SENSITIVE`)
and is reused. This feature does **not** invent a new one. A richer,
metadata-driven classification (per-field sensitivity levels, capability-gated
visibility) is a **non-blocking future enhancement** — the policy registry is the
extension point for it.

## Scope boundary
F2.4 delivers the field-security foundation. **Tenant isolation, business approval
workflows, audit policy, DLP, delegated administration, compliance certification,
and domain suitability/licensing rules are later backlog features** and are not
implemented here.
