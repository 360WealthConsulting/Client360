"""Folder-centric staff Linkage Review UI (PR-5).

A thin staff surface over the EXISTING Exception Engine ``linkage`` queue (PR-3) and the PR-4 resolution
adapter — it creates NO new queue, matching engine, or resolution system, and duplicates no backend logic.
The queue is ``exception_engine.list_exceptions(domain='linkage')``; the evidence is the PR-2 bundle stored
in each exception's opened event; every resolution control calls ``resolve_linkage_exception`` (PR-4).

Read is gated by ``exception.read``; executing a resolution requires ``exception.write`` (read-only users
never see active controls). Linkage is firm-wide. A positive resolution requires an explicit confirmation
step; PR-4 fail-closed errors are surfaced, never suppressed; correcting an already-resolved subject is a
separate explicit supersede flow. No file movement, relocation, storage_uri, or document_sources changes.
"""
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, select

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import exception_engine as ee
from app.services.migration.linkage_resolution import (
    LinkageConflictError,
    LinkageResolutionError,
    resolve_linkage_exception,
)
from app.services.resolution_knowledge import (
    ResolutionConflictError,
    get_current_decision,
    get_reusable_resolution,
)
from app.templating import render_error

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_LINKAGE = "linkage"
_ACTIONS = ("link_person", "create_person", "link_household", "create_household",
            "link_business", "create_business", "firm_material", "defer")
_LINK_ACTIONS = {"link_person", "link_household", "link_business"}
_ENTITY_KIND = {"person": "people", "household": "households", "business": "relationship_entities"}


# --------------------------------------------------------------------------- data (testable, read-only)

def _opened_evidence(conn, exception_ids):
    """Batch-load each exception's opened-event {subject, evidence} (the PR-2 bundle) in one query."""
    events = ee.exception_events
    out: dict = {}
    if not exception_ids:
        return out
    rows = conn.execute(select(events.c.exception_id, events.c["metadata"]).where(and_(
        events.c.exception_id.in_(list(exception_ids)), events.c.event_type == "opened"))
        .order_by(events.c.id)).all()
    for eid, meta in rows:
        if eid not in out and isinstance(meta, dict):
            out[eid] = meta
    return out


def _candidate_summary(evidence):
    ev = evidence or {}
    return {"people": len(ev.get("person_candidates") or []),
            "households": len(ev.get("household_candidates") or []),
            "businesses": len(ev.get("business_candidates") or []),
            "source_contacts": len(ev.get("source_contact_candidates") or [])}


def linkage_queue(principal, *, status="open"):
    """Queue rows for the linkage review list. Read-only; firm-wide via list_exceptions(domain=linkage)."""
    open_only = status != "all"
    rows = ee.list_exceptions(principal, domain=_LINKAGE, open_only=open_only)
    from app.db import engine
    with engine.connect() as conn:
        meta = _opened_evidence(conn, [r["id"] for r in rows])
    queue = []
    for r in rows:
        m = meta.get(r["id"], {})
        subject = m.get("subject") or {}
        ev = m.get("evidence") or {}
        flags = ev.get("evidence_flags") or {}
        queue.append({
            "exception_id": r["id"], "status": r["status"], "sla_state": r.get("sla_state"),
            "opened_at": r["opened_at"], "owner_user_id": r.get("owner_user_id"),
            "display_name": subject.get("display_name") or r["title"],
            "source_system": subject.get("source_system") or "",
            "document_count": ev.get("document_count", 0),
            "confidence": ev.get("confidence"),
            "held_reason": ev.get("held_reason"),
            "suggested_action": (ev.get("suggested_action") or {}).get("action"),
            "candidate_summary": _candidate_summary(ev),
            "no_candidates": bool(flags.get("no_candidates")),
        })
    return queue


def linkage_detail_context(exception_id, principal, *, search_type=None, q=None):
    """Full detail context: the exception, PR-2 evidence bundle, durable resolution knowledge, and an
    optional safe canonical-entity search. Read-only."""
    row = ee.get_exception(exception_id, principal=principal, with_events=True)
    if row.get("domain") != _LINKAGE:
        return None
    opened = next((e for e in row.get("events", []) if e["event_type"] == "opened"), None)
    meta = (opened or {}).get("metadata") or {}
    subject = meta.get("subject") or {}
    evidence = meta.get("evidence") or {}
    current = reusable = None
    if subject.get("subject_key"):
        current = get_current_decision(subject["source_system"], subject["subject_type"],
                                       subject["subject_key"])
        reusable = get_reusable_resolution(subject["source_system"], subject["subject_type"],
                                           subject["subject_key"])
    search_results = _search_entities(search_type, q) if (search_type and q) else None
    return {
        "exception": row, "subject": subject, "evidence": evidence,
        "current_resolution": current, "reusable_resolution": reusable,
        "candidate_summary": _candidate_summary(evidence),
        "actions": _ACTIONS, "search_type": search_type, "search_q": q,
        "search_results": search_results,
        "can_write": principal.can("exception.write"),
        "is_open": row["status"] not in ("resolved", "cancelled"),
    }


def _search_entities(kind, q):
    """Safe, read-only canonical-entity search for when the right candidate is not already shown."""
    if kind not in _ENTITY_KIND or not (q or "").strip():
        return None
    from app.db import engine, households, people, relationship_entities
    like = f"%{q.strip()}%"
    with engine.connect() as c:
        if kind == "person":
            rows = c.execute(select(people.c.id, people.c.full_name, people.c.primary_email)
                             .where(and_(people.c.full_name.ilike(like), people.c.active.is_(True)))
                             .order_by(people.c.full_name).limit(20)).mappings().all()
            return [{"id": r["id"], "label": r["full_name"], "detail": r["primary_email"] or ""}
                    for r in rows]
        if kind == "household":
            rows = c.execute(select(households.c.id, households.c.name)
                             .where(households.c.name.ilike(like))
                             .order_by(households.c.name).limit(20)).mappings().all()
            return [{"id": r["id"], "label": r["name"], "detail": "household"} for r in rows]
        rows = c.execute(select(relationship_entities.c.id, relationship_entities.c.name,
                                relationship_entities.c.entity_type)
                         .where(and_(relationship_entities.c.name.ilike(like),
                                     relationship_entities.c.person_id.is_(None),
                                     relationship_entities.c.household_id.is_(None),
                                     relationship_entities.c.active.is_(True)))
                         .order_by(relationship_entities.c.name).limit(20)).mappings().all()
        return [{"id": r["id"], "label": r["name"], "detail": r["entity_type"]} for r in rows]


def _next_open_exception_id(principal, after_id):
    """The next open linkage exception to work after resolving one (for 'resolve and next')."""
    ids = sorted(r["exception_id"] for r in linkage_queue(principal, status="open")
                 if r["exception_id"] != after_id)
    nxt = [i for i in ids if i > after_id]
    return (nxt or ids or [None])[0]


# --------------------------------------------------------------------------- routes

@router.get("/linkage/review", response_class=HTMLResponse)
def review_queue(request: Request, status: str = "open",
                 principal: Principal = Depends(require_capability("exception.read"))):
    rows = linkage_queue(principal, status=status)
    return templates.TemplateResponse(request=request, name="linkage/review_list.html", context={
        "rows": rows, "status": status, "can_write": principal.can("exception.write"),
        "resolved": request.query_params.get("resolved"),
        "result": request.query_params.get("result")})


@router.get("/linkage/review/{exception_id:int}", response_class=HTMLResponse)
def review_detail(exception_id: int, request: Request, search_type: str = None, q: str = None,
                  principal: Principal = Depends(require_capability("exception.read"))):
    try:
        ctx = linkage_detail_context(exception_id, principal, search_type=search_type, q=q)
    except ee.ExceptionNotFoundError:
        return render_error(request, 404, detail="Linkage exception not found.")
    if ctx is None:
        return render_error(request, 404, detail="Not a linkage review item.")
    ctx["error"] = request.query_params.get("error")
    return templates.TemplateResponse(request=request, name="linkage/review_detail.html", context=ctx)


@router.get("/linkage/review/{exception_id:int}/confirm", response_class=HTMLResponse)
def review_confirm(exception_id: int, request: Request, action: str = "", target_entity_id: str = "",
                   source_contact_id: str = "", name: str = "", supersede: str = "",
                   principal: Principal = Depends(require_capability("exception.write"))):
    if action not in _ACTIONS:
        return render_error(request, 400, detail="Unknown resolution action.")
    ctx = linkage_detail_context(exception_id, principal)
    if ctx is None:
        return render_error(request, 404, detail="Not a linkage review item.")
    target_label = _target_label(action, target_entity_id, name, ctx)
    return templates.TemplateResponse(request=request, name="linkage/review_confirm.html", context={
        **ctx, "action": action, "target_entity_id": target_entity_id,
        "source_contact_id": source_contact_id, "name": name, "supersede": bool(supersede),
        "target_label": target_label})


@router.post("/linkage/review/{exception_id:int}/resolve")
async def review_resolve(exception_id: int, request: Request,
                         principal: Principal = Depends(require_capability("exception.write"))):
    form = parse_qs((await request.body()).decode("utf-8"))
    action = form.get("action", [""])[0]
    if action not in _ACTIONS:
        return render_error(request, 400, detail="Unknown resolution action.")
    if form.get("confirm", [""])[0] != "true":
        return render_error(request, 400, detail="Explicit confirmation is required.")

    def _int(key):
        raw = form.get(key, [""])[0].strip()
        return int(raw) if raw.isdigit() else None

    kwargs = {
        "target_entity_id": _int("target_entity_id"),
        "source_contact_id": _int("source_contact_id"),
        "name": (form.get("name", [""])[0].strip() or None),
        "notes": (form.get("notes", [""])[0].strip() or None),
        "supersede": form.get("supersede", [""])[0] == "true",
    }
    try:
        result = resolve_linkage_exception(exception_id, action, principal=principal,
                                           actor_user_id=principal.user_id, **kwargs)
    except (LinkageConflictError, LinkageResolutionError, ResolutionConflictError) as exc:
        # surface fail-closed errors without suppressing them
        from urllib.parse import quote
        return RedirectResponse(f"/linkage/review/{exception_id}?error={quote(str(exc))}", status_code=303)

    if action == "defer":
        return RedirectResponse(f"/linkage/review/{exception_id}?result=deferred", status_code=303)
    summary = (f"{action}: {result.get('documents_linked', 0)} document(s) linked to "
               f"{result.get('resulting_entity_type') or 'firm'} "
               f"{result.get('resulting_entity_id') or ''}").strip()
    nxt = _next_open_exception_id(principal, exception_id)
    if nxt is not None:
        from urllib.parse import quote
        return RedirectResponse(f"/linkage/review/{nxt}?result={quote(summary)}", status_code=303)
    from urllib.parse import quote
    return RedirectResponse(f"/linkage/review?resolved={exception_id}&result={quote(summary)}",
                            status_code=303)


def _target_label(action, target_entity_id, name, ctx):
    if action == "firm_material":
        return "Firm material (documents preserved, not assigned to any client)"
    if action == "defer":
        return "Defer (leave in the queue for later review)"
    if action in _LINK_ACTIONS:
        return f"existing entity id {target_entity_id}"
    return f"new entity '{name or ctx['subject'].get('display_name')}'"
