"""Enterprise Compliance Intelligence & Supervisory Operations engine (Phase D.47).

A READ-ONLY composition over the authoritative compliance/review/exception/audit/approval/licensing services
that gives supervisors ONE explainable operational view. It is NOT a second compliance/approval/workflow/
audit engine: it never submits/assigns/decides a review, never opens/resolves an exception, never writes the
audit log, and never mutates. Every supervisory item/exception is explainable (explanation + evidence + deep
link into an authoritative workflow) — non-explainable ones are dropped.

Supervisor-vs-advisor separation is enforced: the supervisory surfaces require the ``compliance.supervise``
capability (``gate.supervisor_authorized``) and return ``None`` otherwise; the advisor-visible compliance
TASKS are a separate, narrower projection (``advisor_compliance_tasks``) that never exposes supervisory-only
findings. Gate- and policy-aware; returns ``None`` when unauthorized or out of scope so the route emits 403/404.
"""
from __future__ import annotations

import time

from . import gate, stats
from .adapters import compliance_exceptions, licensing_items, review_items


def _accessible_person_ids(principal):
    try:
        from app.db import engine
        from app.security.authorization import accessible_person_ids
        with engine.connect() as conn:
            return accessible_person_ids(conn, principal)
    except Exception:
        return set()


def _in_scope(principal, entity_type, entity_id):
    try:
        from app.security.authorization import record_in_scope
        return record_in_scope(principal, entity_type, entity_id)
    except Exception:
        return False


def _prioritize_items(items):
    return sorted(items, key=lambda i: (i.priority_rank, i.item_id), reverse=True)


def _prioritize_exceptions(excs):
    return sorted(excs, key=lambda e: (e.severity_rank, e.exception_id), reverse=True)


def _dedupe(objs, key):
    seen, out = set(), []
    for o in objs:
        k = key(o)
        if k in seen:
            stats.note("suppressed")
            continue
        seen.add(k)
        out.append(o)
    return out


def _emit_items(items):
    out = []
    for i in items:
        if not i.is_explainable:
            stats.note("missing_evidence")
            continue
        out.append(i)
    return out


def _emit_exceptions(excs):
    out = []
    for e in excs:
        if not e.is_explainable:
            stats.note("missing_evidence")
            continue
        out.append(e)
    return out


def _disabled():
    return {"enabled": False, "reviews": [], "exceptions": [], "counts": {}}


def _package(items, exceptions):
    by_type, by_severity = {}, {}
    for i in items:
        by_type[i.review_type] = by_type.get(i.review_type, 0) + 1
    for e in exceptions:
        by_severity[e.severity] = by_severity.get(e.severity, 0) + 1
    return {"enabled": True,
            "reviews": [i.to_dict() for i in items],
            "exceptions": [e.to_dict() for e in exceptions],
            "counts": {"open_reviews": len(items), "open_exceptions": len(exceptions),
                       "by_review_type": by_type, "by_severity": by_severity,
                       "pending_approvals": sum(1 for i in items if i.status == "pending_review"),
                       "blocked": sum(1 for i in items
                                      if i.status == "blocked_pending_authorized_reviewer")}}


def supervisory_dashboard(principal):
    """The enterprise supervisory workspace: open reviews, pending approvals, compliance exceptions, advisor
    workload, aging reviews, licensing/CE, documentation gaps. Supervisor-only (``compliance.supervise``).
    Returns None when unauthorized; disabled envelope when gated off."""
    if not gate.enabled() or not gate.gate("supervisor.workspace.enabled"):
        return {**_disabled(), "workload": {}}
    if not gate.supervisor_authorized(principal):
        stats.note("authorization_failures")
        return None
    if not gate.policy_ok("dashboard"):
        return {**_disabled(), "enabled": True, "denied": "policy", "workload": {}}
    t0 = time.monotonic()
    r_items, r_exc = review_items(principal)
    l_items, l_exc = licensing_items(principal)
    exc = compliance_exceptions(principal, _accessible_person_ids(principal))
    items = _emit_items(_dedupe(_prioritize_items(r_items + l_items), lambda i: i.item_id))
    exceptions = _emit_exceptions(_dedupe(_prioritize_exceptions(r_exc + l_exc + exc),
                                          lambda e: e.exception_id))
    result = _package(items, exceptions)
    # Advisor workload distribution (reuse the authoritative work-queue summary).
    try:
        from app.services.work_queue.summary import work_queue_summary
        s = work_queue_summary(principal)
        result["workload"] = {"by_domain": s.get("by_domain", {}), "my_overdue": s.get("my_overdue", 0),
                              "sla_breaches": s.get("sla_breaches", 0),
                              "unassigned_team": s.get("unassigned_team", 0)}
    except Exception:
        result["workload"] = {}
        stats.note("adapter_failures", source="work_queue")
    stats.note("dashboards")
    stats.note("compositions")
    stats.note_ms((time.monotonic() - t0) * 1000)
    return result


def client_compliance(principal, person_id):
    """Supervisory compliance view for one client (open reviews + supervisory status + outstanding
    exceptions). Supervisor-only; None when unauthorized or out of scope."""
    if not gate.enabled() or not gate.gate("supervision.enabled"):
        return _disabled()
    if not gate.supervisor_authorized(principal):
        stats.note("authorization_failures")
        return None
    if not _in_scope(principal, "person", person_id):
        return None
    return _client_view(principal, person_id=person_id)


def household_compliance(principal, household_id):
    """Supervisory compliance view aggregated across household members. Supervisor-only; None when
    unauthorized or out of scope. Duplicate items/exceptions across members collapse."""
    if not gate.enabled() or not gate.gate("supervision.enabled"):
        return _disabled()
    if not gate.supervisor_authorized(principal):
        stats.note("authorization_failures")
        return None
    if not _in_scope(principal, "household", household_id):
        return None
    return _client_view(principal, household_id=household_id)


def _client_view(principal, *, person_id=None, household_id=None):
    t0 = time.monotonic()
    r_items, r_exc = review_items(principal, person_id=person_id)
    if person_id is not None:
        pids = {person_id}
    else:
        try:
            from sqlalchemy import select

            from app.db import engine, people
            with engine.connect() as conn:
                pids = set(conn.scalars(select(people.c.id).where(people.c.household_id == household_id)))
        except Exception:
            pids = set()
    exc = compliance_exceptions(principal, pids)
    items = _emit_items(_dedupe(_prioritize_items(r_items), lambda i: (i.review_type, i.title)))
    exceptions = _emit_exceptions(_dedupe(_prioritize_exceptions(r_exc + exc),
                                          lambda e: (e.exception_type, e.title)))
    result = _package(items, exceptions)
    stats.note("compositions")
    stats.note_ms((time.monotonic() - t0) * 1000)
    return result


def compliance_summary(principal, *, person_id=None, household_id=None):
    """Compact supervisory summary (counts) for the Client 360 / Household 360 sections and (through them) AI
    grounding. Supervisor-only — returns a disabled envelope for non-supervisors (never leaks counts to
    advisors). Never raises."""
    if not gate.enabled():
        return {"enabled": False, "supervisor": False, "open_reviews": 0, "open_exceptions": 0}
    if not gate.supervisor_authorized(principal):
        return {"enabled": True, "supervisor": False, "open_reviews": 0, "open_exceptions": 0}
    if person_id is not None:
        result = client_compliance(principal, person_id)
    elif household_id is not None:
        result = household_compliance(principal, household_id)
    else:
        return {"enabled": True, "supervisor": True, "open_reviews": 0, "open_exceptions": 0}
    if result is None or not result.get("enabled"):
        return {"enabled": True, "supervisor": True, "open_reviews": 0, "open_exceptions": 0}
    stats.note("summaries")
    c = result["counts"]
    return {"enabled": True, "supervisor": True, "open_reviews": c["open_reviews"],
            "open_exceptions": c["open_exceptions"], "pending_approvals": c["pending_approvals"],
            "blocked": c["blocked"], "by_severity": c["by_severity"]}


def advisor_compliance_tasks(principal, *, person_id=None):
    """The ADVISOR-visible compliance tasks — the narrow, non-supervisory projection. It surfaces ONLY the
    advisor-facing governed compliance recommendations already exposed by the D.46 layer (category
    ``governed``). It NEVER returns supervisory items, exceptions, reviewer identities, or approval state,
    so supervisory-only findings can never leak to an advisor. Gated by the ordinary client read path."""
    if not gate.enabled():
        return {"enabled": False, "tasks": []}
    try:
        from app.services.recommendations import client_recommendations, workspace_recommendations
        result = client_recommendations(principal, person_id) if person_id is not None \
            else workspace_recommendations(principal)
    except Exception:
        return {"enabled": True, "tasks": []}
    if result is None or not result.get("enabled"):
        return {"enabled": True, "tasks": []}
    tasks = [r for r in result.get("recommendations", []) if r.get("category") == "governed"]
    return {"enabled": True, "tasks": tasks, "total": len(tasks)}
