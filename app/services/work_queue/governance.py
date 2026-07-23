"""Unified Work Queue governance (Phase D.39) — read-only validation that the queue stays a composition
surface and never a shadow engine. Static-scans the queue modules and checks the adapter/dispatch config
for the invariants the queue must hold. Never mutates; never raises.
"""
from __future__ import annotations

import pathlib
import re

from .adapters import ADAPTERS, DOMAIN_ADAPTER, DOMAIN_CAPABILITY, SOURCE_DOMAINS
from .dispatch import ACTION_CAPABILITY, ALLOWED_ACTIONS
from .views import BUILTIN_VIEWS, KNOWN_FILTER_KEYS

# Queue modules that must never mutate authoritative state or read rm_* tables directly.
_READ_ONLY_MODULES = ("adapters.py", "service.py", "summary.py", "diagnostics.py")
# Authoritative services the dispatch layer must delegate mutations to.
_REQUIRED_DELEGATES = ("work_management", "workflow_orchestration", "exception_engine",
                       "document_platform")


def _src(name):
    try:
        return (pathlib.Path(__file__).with_name(name)).read_text()
    except OSError:
        return ""


def validate_work_queue(principal=None) -> dict:
    """Run every queue-governance check → ``{ok, issue_count, findings}``. Never raises."""
    findings = []
    try:
        emitted = {"tasks", "workflow", "exceptions"} | {a.domain for a in ADAPTERS if a.domain != "core"}

        # every source domain is adapted, capability-mapped, and dispatch-known.
        for d in SOURCE_DOMAINS:
            if d not in emitted:
                findings.append({"type": "adapter_without_source", "domain": d})
            if d not in DOMAIN_CAPABILITY:
                findings.append({"type": "source_without_capability_mapping", "domain": d})
            if d not in DOMAIN_ADAPTER:
                findings.append({"type": "source_without_dispatch_adapter", "domain": d})
            if d not in ALLOWED_ACTIONS:
                findings.append({"type": "source_without_action_map", "domain": d})

        # a registered adapter that produces no known source domain (unused adapter).
        for a in ADAPTERS:
            doms = {"tasks", "workflow", "exceptions"} if a.domain == "core" else {a.domain}
            if not (doms & set(SOURCE_DOMAINS)):
                findings.append({"type": "adapter_with_no_queue_usage", "adapter": a.__class__.__name__})

        # duplicate adapters for a domain.
        seen = set()
        for a in ADAPTERS:
            if a.domain in seen and a.domain != "core":
                findings.append({"type": "duplicate_source_adapter", "domain": a.domain})
            seen.add(a.domain)

        # every dispatch-able action has a route-capability floor.
        for d, actions in ALLOWED_ACTIONS.items():
            for act in actions:
                if act not in ACTION_CAPABILITY:
                    findings.append({"type": "action_without_capability", "domain": d, "action": act})

        # dispatch must delegate to authoritative services (no self-mutation).
        dispatch_src = _src("dispatch.py")
        for svc in _REQUIRED_DELEGATES:
            if svc not in dispatch_src:
                findings.append({"type": "dispatch_missing_delegate", "service": svc})

        # queue read modules must not mutate authoritative state or read rm_* directly.
        for mod in _READ_ONLY_MODULES:
            s = _src(mod)
            for m in re.findall(r"\brm_[a-z]\w*", s):
                findings.append({"type": "direct_projection_table_read", "module": mod, "table": m})
            for verb in (".insert(", ".update(", ".delete("):
                if verb in s:
                    findings.append({"type": "direct_authoritative_mutation", "module": mod, "op": verb})

        # built-in views must use only known filter keys.
        for key, spec in BUILTIN_VIEWS.items():
            for fk in (spec.get("filters") or {}):
                if fk not in KNOWN_FILTER_KEYS:
                    findings.append({"type": "unknown_filter_key", "view": key, "key": fk})

        # stored saved-views must not carry unknown filter keys.
        findings.extend(_scan_saved_views())

        # adopted projections the queue counts rely on must not be stale beyond threshold.
        findings.extend(_scan_stale_projections())

        # runtime sample (only if a principal is supplied): every item has a deep link + stable key.
        if principal is not None:
            findings.extend(_scan_items(principal))
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}


def _scan_saved_views():
    out = []
    try:
        from sqlalchemy import select

        from app.db import engine, work_queue_saved_views
        with engine.connect() as c:
            for row in c.execute(select(work_queue_saved_views.c.id,
                                         work_queue_saved_views.c.filters)).mappings():
                for fk in (row["filters"] or {}):
                    if fk not in KNOWN_FILTER_KEYS:
                        out.append({"type": "saved_view_unknown_filter", "view_id": row["id"], "key": fk})
    except Exception:
        pass
    return out


def _scan_stale_projections():
    out = []
    try:
        from app.services.projections import engine as pengine
        from app.services.projections.adoption import FRESHNESS_LAG_THRESHOLD
        for pid in ("operations.tasks", "exception.dashboard", "compliance.queue", "tax.pipeline",
                    "insurance.pipeline", "opportunity.pipeline", "document.status"):
            st = pengine.state(pid)
            if st.get("rebuild_count", 0) > 0 and pengine.lag(pid) > FRESHNESS_LAG_THRESHOLD:
                out.append({"type": "stale_projection", "projection": pid, "lag": pengine.lag(pid)})
    except Exception:
        pass
    return out


def _scan_items(principal):
    out = []
    try:
        from .service import compose_queue
        for it in compose_queue(principal, page=1, page_size=50)["items"]:
            if not it.deep_link:
                out.append({"type": "dead_end_work_item", "key": it.work_item_key})
            if not it.work_item_key:
                out.append({"type": "work_item_without_source_reference"})
    except Exception:
        pass
    return out


def record_validation(*, actor_user_id=None) -> dict:
    report = validate_work_queue()
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action="work_queue.governance_validated", entity_type="work_queue",
                          entity_id="0", actor_user_id=actor_user_id,
                          request_id=str(uuid.uuid4()), metadata={"issue_count": report["issue_count"]})
    except Exception:
        pass
    return report
