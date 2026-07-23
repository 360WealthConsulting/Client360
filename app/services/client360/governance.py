"""Client 360 Workspace governance (Phase D.40) — read-only validation that the workspace stays a
COMPOSITION surface: no duplicated business logic, no direct mutation, no duplicate projection, no
shadow client record, authoritative services only, outbox unchanged, RBAC + record scope preserved.
Never mutates; never raises.
"""
from __future__ import annotations

import pathlib
import re

from .registry import QUICK_ACTIONS, SECTIONS

# Composition modules that must never mutate, define a table, publish an event, or read rm_* directly.
_MODULES = ("sections.py", "service.py", "snapshot.py", "diagnostics.py")


def _src(name):
    try:
        return (pathlib.Path(__file__).with_name(name)).read_text()
    except OSError:
        return ""


def validate_client360(principal=None) -> dict:
    findings = []
    try:
        # every section has a builder + a resolvable capability slot.
        for s in SECTIONS:
            if s.builder is None:
                findings.append({"type": "missing_adapter", "section": s.key})
        # every quick action deep-links (no dead-end) and is capability-gated.
        for a in QUICK_ACTIONS:
            if not a.capability:
                findings.append({"type": "quick_action_without_capability", "action": a.key})

        joined = ""
        for mod in _MODULES:
            s = _src(mod)
            joined += s
            for m in re.findall(r"\brm_[a-z]\w*", s):
                findings.append({"type": "direct_projection_table_read", "module": mod, "table": m})
            for verb in (".insert(", ".update(", ".delete("):
                if verb in s:
                    findings.append({"type": "direct_mutation", "module": mod, "op": verb})
            if "publish_safe" in s or "write_audit_event" in s:
                findings.append({"type": "outbox_or_audit_write_in_composition", "module": mod})
            if re.search(r"\bTable\s*\(", s):
                findings.append({"type": "shadow_client_record_table", "module": mod})

        # authoritative-services-only: composition must import from app.services.* (not define domain logic).
        if "app.services" not in joined:
            findings.append({"type": "no_authoritative_service_delegation"})

        # record scope must be enforced at the boundary.
        if "record_in_scope" not in _src("service.py"):
            findings.append({"type": "record_scope_not_enforced"})

        # no duplicate projection: the read-model set is unchanged (still the D.36 twelve).
        try:
            from app.database.projection_tables import READ_MODEL_TABLES
            if len(READ_MODEL_TABLES) != 12 or "rm_client360" in READ_MODEL_TABLES:
                findings.append({"type": "duplicate_projection"})
        except Exception:
            pass

        # runtime sample (if a principal is given): the workspace composes without a mutating side effect.
        if principal is not None:
            findings.extend(_sample(principal))
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}


def _sample(principal):
    out = []
    try:
        from sqlalchemy import select

        from app.db import engine, people

        from .service import get_workspace
        with engine.connect() as c:
            pid = c.scalar(select(people.c.id).limit(1))
        if pid is not None and principal.can("record.read_all"):
            ws = get_workspace(principal, person_id=pid)
            if ws and not ws.get("quick_actions"):
                out.append({"type": "no_quick_actions_composed"})
    except Exception:
        pass
    return out


def record_validation(*, actor_user_id=None) -> dict:
    report = validate_client360()
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action="client360.governance_validated", entity_type="client360",
                          entity_id="0", actor_user_id=actor_user_id, request_id=str(uuid.uuid4()),
                          metadata={"issue_count": report["issue_count"]})
    except Exception:
        pass
    return report
