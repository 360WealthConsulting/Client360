"""Domain Event routes (Phase D.34) — /events.

New ``/events`` prefix. It matches no middleware RULE, so each endpoint enforces its D.26
``observability.*`` capability in-route (reusing the existing observability capabilities — no new
capabilities, no RBAC changes; the event model is platform operational infrastructure). Contract +
subscription + adoption reads require ``observability.view``; the governance report, diagnostics,
dead-letters, and per-event replay require ``observability.audit``; running governance validation
requires ``observability.execute``. The event model reuses the transactional outbox as the bus; this
surface only reports/administers the model and never bypasses RBAC. Diagnostics + replay are read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.events import diagnostics, governance, registry, replay
from app.services.events.common import as_json, stats
from app.templating import install_filters

router = APIRouter(prefix="/events", tags=["domain-events"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("observability.view"))):
    gov = governance.validate()
    return templates.TemplateResponse(request=request, name="events/overview.html", context={
        "principal": principal, "adoption": registry.adoption(principal),
        "contracts": registry.list_contracts(), "stats": stats(),
        "governance": {"ok": gov["ok"], "issue_count": gov["issue_count"],
                       "coverage_pct": gov["coverage"].get("coverage_pct")},
        "can_admin": principal.can("observability.execute")})


@router.get("/registry")
def registry_list(request: Request, status: str | None = None, category: str | None = None,
                  principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"contracts": [
        {"event_type": c["event_type"], "category": c["category"], "name": c["name"], "status": c["status"],
         "schema_version": c["schema_version"], "producer": c["producer"],
         "payload_schema": c["payload_schema"], "depends_on": c["depends_on"]}
        for c in registry.list_contracts(status=status, category=category)]})


@router.get("/subscriptions")
def subscriptions(request: Request, event_type: str | None = None,
                  principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"subscriptions": as_json(registry.list_subscriptions(event_type=event_type))})


@router.get("/adoption")
def adoption(request: Request, principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse(as_json(registry.adoption(principal)))


@router.get("/graph")
def graph(request: Request, principal: Principal = Depends(require_capability("observability.view"))):
    return JSONResponse({"dependency_graph": registry.dependency_graph()})


@router.get("/governance")
def governance_report(request: Request,
                      principal: Principal = Depends(require_capability("observability.audit"))):
    """The domain-event governance report: unregistered/orphan contracts, orphan subscriptions,
    producers without consumers, schema violations, version drift, deprecated references."""
    return JSONResponse(as_json(governance.validate()))


@router.post("/governance/validate")
def governance_validate(request: Request,
                        principal: Principal = Depends(require_capability("observability.execute"))):
    return JSONResponse(as_json(governance.record_validation(actor_user_id=principal.user_id)))


@router.get("/diagnostics")
def event_diagnostics(request: Request,
                      principal: Principal = Depends(require_capability("observability.audit"))):
    """Event-flow diagnostics: per-type counts, delivery status, subscriber health (read-only)."""
    return JSONResponse(as_json({"counts": diagnostics.event_counts(),
                                 "subscribers": diagnostics.subscriber_health()}))


@router.get("/dead-letters")
def dead_letters(request: Request,
                 principal: Principal = Depends(require_capability("observability.audit"))):
    return JSONResponse({"dead_letters": as_json(diagnostics.dead_letters())})


@router.get("/contracts/{event_type}")
def contract_detail(event_type: str, request: Request,
                    principal: Principal = Depends(require_capability("observability.view"))):
    row = registry.get_contract(event_type)
    if row is None:
        raise HTTPException(404, f"unknown event contract {event_type!r}")
    return JSONResponse(as_json({**row, "subscribers": registry.subscribers_of(event_type)}))


@router.get("/{event_id}")
def event_detail(event_id: str, request: Request,
                 principal: Principal = Depends(require_capability("observability.audit"))):
    diag = diagnostics.event_diagnostics(event_id)
    if not diag:
        raise HTTPException(404, f"event {event_id} not found")
    return JSONResponse(as_json(diag))


@router.get("/{event_id}/replay")
def event_replay(event_id: str, request: Request,
                 principal: Principal = Depends(require_capability("observability.audit"))):
    """Deterministically reconstruct an event from the outbox log (read-only; never mutates state)."""
    try:
        return JSONResponse(as_json(replay.replay(event_id)))
    except Exception as exc:
        raise HTTPException(404, str(exc)) from exc
