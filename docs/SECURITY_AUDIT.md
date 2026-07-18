# Client360 — Security Audit & Policy Event Foundation (E2.5 / Backlog F2.5)

A canonical, **provider-agnostic, transport-agnostic** abstraction for security
auditing that **wraps and reuses** the two existing mechanisms and **preserves all
existing behavior**.

`app/security/audit_foundation.py`

## Mapping to the existing implementation (reconciliation)
| Existing | Role | F2.5 |
|---|---|---|
| `audit.write_audit_event` → `audit_events` (with `redact_metadata`) | Persistent DB audit log | `DbAuditSink` **delegates** to it — behavior unchanged (agreement-tested) |
| F2.1–F2.4 `emit_*` → F1.4 envelope via F1.3 outbox (`identity.*`/`authorization.*`/`object.*`/`field.*`) | Event-driven security events | `OutboxAuditSink` uses the same envelope + transport |
| `redaction`/`field_security` (F2.4) | Sensitive-value masking | Reused to scrub event attributes before publication |

F2.5 **formalizes** a service + models + taxonomy + sinks over these; it does not
replace either mechanism and introduces no new audit enforcement.

## Concepts
| Type | Purpose |
|---|---|
| `SecurityEvent` | Canonical event — `action`, `category`, actor, entity, `subject_ref`, `outcome`, `attributes` (references only), optional `template_id`. |
| Taxonomy | Categories `authentication`/`authorization`/`object`/`field`/`session`/`security`; `category_for_action()` maps canonical actions. |
| `AuditContext` | Ambient context — `request_id`, actor, correlation, ip, user-agent; `from_request()`. |
| `AuditResult` | Outcome — `recorded`, `sinks`, `event_id`, `audit_id`. |
| `AuditSink` (Protocol) | Provider-agnostic destination. |
| `OutboxAuditSink` / `DbAuditSink` | Built-in sinks (event-driven / persistent). |
| `AuditPolicy` (Protocol) + `RecordAllPolicy` | Gate whether an event is recorded. |
| `SecurityAuditService` | Records an event to its sinks under a policy. |

## API
```python
from app.security.audit_foundation import (
    SecurityEvent, AuditContext, default_security_audit_service, SecurityAuditService, DbAuditSink,
)

svc = default_security_audit_service()               # outbox sink (event-driven)
event = SecurityEvent(action="authorization.denied", actor_user_id=uid,
                      subject_ref="user:%d" % uid, outcome="denied",
                      attributes={"permission": "insurance.read"})  # references only
with engine.begin() as conn:
    result = svc.record(event, AuditContext.from_request(request), conn=conn)   # atomic

# Persist to the existing DB audit log instead of / in addition to the outbox:
SecurityAuditService(sinks=[DbAuditSink()]).record(event, ctx)
```

## Security event taxonomy
Canonical actions carry a category via `category_for_action`:
`identity.*` → authentication, `authorization.*` → authorization, `object.*` →
object, `field.*` → field, `session.*` → session, else `security`. This unifies the
event types already emitted by F2.1–F2.4 under one taxonomy.

## Provider abstraction (sinks) & extension points
Register future sinks (e.g. **SIEM**, **immutable audit storage**) without changing
producers:
```python
class SiemSink:
    sink_name = "siem"
    def record(self, event, context, *, conn=None): ...   # -> {"sink": "siem", ...}
register_sink(SiemSink())
SecurityAuditService(sinks=[get_sink("siem")])
```
The audit abstraction is provider- and transport-agnostic.

## Integration
- **Transactional Outbox (F1.3)** — `OutboxAuditSink` publishes in the caller's tx.
- **Event Envelope (F1.4)** — `SecurityEvent.to_envelope()` produces a canonical,
  versioned envelope.
- **Workflow Template Registry (F1.5)** — `for_workflow_template(action, template_id, …)`
  links an event to a registered template (validated against the registry).
- **Field Security (F2.4)** — attributes are scrubbed before publication.

## Security & privacy guarantees
- **Never publishes secrets/sensitive values** — attributes are scrubbed with the
  F2.4 field-security service (sensitive names masked); the DB sink applies the
  existing `redact_metadata`.
- **Deterministic** — `to_envelope(event_id=…, occurred_at=…)` is byte-stable;
  masking is a constant token.
- **Preserves existing behavior** — DB audit and outbox events are unchanged
  (agreement-tested); no API/response change.
- **Distinguishes categories** — the taxonomy separates authentication /
  authorization / object / field / session / generic events.

## Compatibility guarantees
- **Epic 1, F2.1–F2.4 preserved** — composes with outbox/envelope/registry, auth,
  authz, object security, and field security; changes none.
- **Provider- and transport-agnostic** — stable for future SIEM integration,
  compliance reporting, immutable audit storage, Microsoft identity / AD, and
  export/reporting controls.

## Classification / taxonomy note (per acceptance criteria)
The canonical action taxonomy is derived from the event types **already** emitted by
F2.1–F2.4; no new compliance-specific event types are introduced. A richer,
compliance-specific taxonomy is a **non-blocking future enhancement** — the sink and
policy registries are the extension points.

## Scope boundary
F2.5 delivers the security-audit foundation. **SIEM integration, compliance
reporting, regulatory audit workflows, retention policies, immutable storage,
security analytics, alerting, intrusion detection, tenant isolation, and delegated
administration are later backlog features** and are not implemented here.
