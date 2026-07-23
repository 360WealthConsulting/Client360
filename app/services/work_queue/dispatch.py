"""Unified Work Queue action dispatch (Phase D.39).

The dispatch layer validates a requested action (domain + action allow-list), checks the required
capability, and DELEGATES to the authoritative owning service — which enforces record scope, appends its
own ledger/audit, and publishes any domain event through the existing outbox. The dispatch layer holds
NO business-state mutation of its own, records NO duplicate audit event, and publishes NO domain event.
Assignment reuses the single authoritative assignment engine (``work_management.assign_work`` +
``authorize_assignment_target``, which enforces write scope). Bulk actions dispatch each item
individually and report partial success honestly.
"""
from __future__ import annotations

import threading
import uuid

# In-process action counters (diagnostics only — not authoritative).
_lock = threading.RLock()
_STATS = {"success": 0, "denied": 0, "error": 0, "bulk_calls": 0, "bulk_partial": 0}


def action_stats() -> dict:
    with _lock:
        return dict(_STATS)


def reset_action_stats():
    with _lock:
        for k in _STATS:
            _STATS[k] = 0


def _record(outcome):
    with _lock:
        _STATS[outcome if outcome in _STATS else "error"] = _STATS.get(outcome, 0) + 1

# Actions each source domain supports (mirrors the adapters' allowed_actions; "open" is a plain link).
ALLOWED_ACTIONS = {
    "tasks": {"claim", "assign"},
    "workflow": {"claim", "assign", "complete"},
    "exceptions": {"claim", "assign", "acknowledge", "resolve"},
    "advisor_work": set(),
    "compliance": set(),
    "documents": {"claim", "assign", "approve"},
    "tax": {"claim", "assign"},
    "insurance": set(),
    "opportunities": set(),
    "meetings": set(),
}
# Domains whose items assign through the authoritative record-assignment engine.
ASSIGN_ENTITY = {"tasks": "task", "workflow": "workflow_step", "exceptions": "exception",
                 "documents": "document", "tax": "tax_return"}
# Route-level capability floor per action (the owning service re-checks the real capability + scope).
ACTION_CAPABILITY = {"claim": "work.write", "assign": "work.write", "complete": "work.write",
                     "acknowledge": "exception.write", "resolve": "exception.write",
                     "approve": "documents.write"}

# Bulk is allowed ONLY for these proven-safe, semantically-identical actions.
BULK_ACTIONS = {"claim", "assign", "acknowledge"}


def _ok(message, extra=None):
    return {"ok": True, "outcome": "success", "message": message, **(extra or {})}


def _fail(message, outcome="error"):
    return {"ok": False, "outcome": outcome, "message": message}


def parse_key(work_item_key):
    parts = str(work_item_key or "").split(":", 2)
    if len(parts) != 3 or not parts[2]:
        return None
    domain, stype, sid = parts
    try:
        sid = int(sid)
    except ValueError:
        pass
    return domain, stype, sid


def dispatch_action(principal, *, work_item_key, action, params=None, request_id=None):
    """Validate + delegate one action to the authoritative service. Never mutates directly."""
    params = params or {}
    parsed = parse_key(work_item_key)
    if not parsed:
        return _fail("invalid work item key")
    domain, _stype, sid = parsed
    if action == "open":
        return _fail("'open' is a link, not a dispatched action")
    if action not in ALLOWED_ACTIONS.get(domain, set()):
        return _fail(f"action '{action}' is not supported for {domain}")
    cap = ACTION_CAPABILITY.get(action)
    if cap and not principal.can(cap):
        return _fail(f"capability '{cap}' required", outcome="denied")
    request_id = request_id or f"work-queue-{uuid.uuid4()}"
    try:
        result = _delegate(principal, domain, sid, action, params, request_id)
        _record("success" if result["ok"] else "error")
        return result
    except PermissionError as exc:
        _record("denied")
        return _fail(str(exc) or "outside your authorized scope", outcome="denied")
    except Exception as exc:   # a domain ValueError/StaleError etc. — report, never raise into the route
        _record("error")
        return _fail(str(exc) or "action failed")


def _delegate(principal, domain, sid, action, params, request_id):
    if action in ("claim", "assign"):
        return _assign(principal, domain, sid, action, params, request_id)
    if action == "complete" and domain == "workflow":
        from app.services.workflow_orchestration.service import complete_step
        complete_step(principal, sid, actor_user_id=principal.user_id)
        return _ok("workflow step completed")
    if action in ("acknowledge", "resolve") and domain == "exceptions":
        from app.services import exception_engine
        if action == "acknowledge":
            exception_engine.acknowledge(sid, principal=principal, actor_user_id=principal.user_id,
                                         request_id=request_id)
            return _ok("exception acknowledged")
        exception_engine.resolve(sid, params.get("resolution_code") or "resolved", principal=principal,
                                 actor_user_id=principal.user_id, notes=params.get("note"),
                                 request_id=request_id)
        return _ok("exception resolved")
    if action == "approve" and domain == "documents":
        from app.services.document_platform.service import approve
        approve(principal, sid, actor_user_id=principal.user_id, note=params.get("note"))
        return _ok("document approved")
    return _fail("unsupported action")


def _assign(principal, domain, sid, action, params, request_id):
    entity_type = ASSIGN_ENTITY.get(domain)
    if entity_type is None:
        return _fail(f"{domain} does not support assignment from the queue")
    if action == "claim":
        target = principal.user_id
    else:
        target = params.get("user_id")
        if not target:
            return _fail("a user_id is required to assign")
        target = int(target)
    from app.services.work_management import assign_work, authorize_assignment_target
    authorize_assignment_target(principal, entity_type, sid)   # raises PermissionError if out of scope
    aid = assign_work(entity_type=entity_type, entity_id=sid, assignment_role="secondary",
                      actor_user_id=principal.user_id, user_id=target, request_id=request_id)
    return _ok("claimed" if action == "claim" else "assigned", {"assignment_id": aid})


def dispatch_bulk(principal, *, work_item_keys, action, params=None, request_id=None):
    """Dispatch one action across many items — each delegated individually, partial success reported
    honestly. Only proven-safe, semantically-identical actions are bulk-eligible."""
    if action not in BULK_ACTIONS:
        return {"ok": False, "message": f"'{action}' is not a bulk-safe action",
                "total": 0, "succeeded": 0, "failed": 0, "results": []}
    results = []
    for key in work_item_keys:
        res = dispatch_action(principal, work_item_key=key, action=action, params=params,
                              request_id=request_id)
        results.append({"work_item_key": key, **res})
    succeeded = sum(1 for r in results if r["ok"])
    with _lock:
        _STATS["bulk_calls"] += 1
        if 0 < succeeded < len(results):
            _STATS["bulk_partial"] += 1
    return {"ok": succeeded == len(results) and results != [], "action": action,
            "total": len(results), "succeeded": succeeded,
            "failed": len(results) - succeeded, "results": results}
