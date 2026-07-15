"""Exception Engine API + staff console (Release 0.9.10 / Sprint 5.5, Phase 6).

Thin HTTP layer over the canonical ``exception_engine`` and ``exception_work``
services — no business logic here. Capability is gated by the middleware
(``exception.read`` for GET, ``exception.write`` for mutations via the .read→.write
inference); the engine additionally enforces record scope and the finer
``exception.resolve`` / ``exception.compliance`` for blocker / compliance resolution.
Tax domain only; other domains are rejected by the services.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import exception_engine as ee
from app.services import exception_reporting as er
from app.services import exception_work as ew

router = APIRouter(tags=["exceptions"])
templates = Jinja2Templates(directory="app/templates")


# --- request models ----------------------------------------------------------

class CreateException(BaseModel):
    code: str
    title: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    dedupe_key: Optional[str] = None
    source: str = "manual"
    tax_engagement_return_id: Optional[int] = None
    person_id: Optional[int] = None
    household_id: Optional[int] = None


class ActionBody(BaseModel):
    reason: Optional[str] = None
    expected_status: Optional[str] = None


class CommentBody(BaseModel):
    body: str


class ResolveBody(BaseModel):
    resolution_code: str
    notes: Optional[str] = None
    expected_status: Optional[str] = None


class AssignBody(BaseModel):
    assignment_role: str = "primary"
    user_id: Optional[int] = None
    team_id: Optional[int] = None
    reason: Optional[str] = None


class ReassignBody(BaseModel):
    user_id: Optional[int] = None
    team_id: Optional[int] = None
    reason: Optional[str] = None


# --- error translation -------------------------------------------------------

def _run(fn):
    """Call a service and translate its domain errors to HTTP responses."""
    try:
        return fn()
    except ee.ExceptionNotFoundError:
        raise HTTPException(404, "Exception not found")
    except (ee.ExceptionAuthorizationError, ew.ExceptionAssignmentAuthError) as exc:
        if "capability" in str(exc).lower():
            raise HTTPException(403, str(exc))
        raise HTTPException(404, "Exception not found")  # out-of-scope → hide existence
    except (ee.StaleActionError, ee.InvalidTransitionError) as exc:
        raise HTTPException(409, str(exc))
    except ee.UnsupportedDomainError as exc:
        raise HTTPException(400, str(exc))
    except (ee.ExceptionEngineError, ew.ExceptionAssignmentError) as exc:
        raise HTTPException(400, str(exc))


def _filtered(principal, *, domain, status, severity, category, owner_user_id,
              return_id, person_id, sla_state, escalation_level):
    rows = ee.list_exceptions(principal, domain=domain, status=status or None,
                              severity=severity or None, category=category or None,
                              owner_user_id=int(owner_user_id) if owner_user_id else None)
    if return_id:
        rows = [r for r in rows if str(r["tax_engagement_return_id"]) == str(return_id)]
    if person_id:
        rows = [r for r in rows if str(r["person_id"]) == str(person_id)]
    if sla_state:
        rows = [r for r in rows if r.get("sla_state") == sla_state]
    if escalation_level not in (None, ""):
        rows = [r for r in rows if str(r["escalation_level"]) == str(escalation_level)]
    return rows


# --- JSON API (/api/v1/exceptions) -------------------------------------------

@router.get("/api/v1/exceptions")
def api_list(principal: Principal = Depends(require_capability("exception.read")),
             domain: str = "tax", status: str = "", severity: str = "", category: str = "",
             owner_user_id: str = "", return_id: str = "", person_id: str = "",
             sla_state: str = "", escalation_level: str = ""):
    return {"results": _run(lambda: _filtered(
        principal, domain=domain, status=status, severity=severity, category=category,
        owner_user_id=owner_user_id, return_id=return_id, person_id=person_id,
        sla_state=sla_state, escalation_level=escalation_level))}


@router.get("/api/v1/exceptions/metrics")
def api_metrics(principal: Principal = Depends(require_capability("exception.read"))):
    return _run(lambda: ee.metrics(principal))


@router.get("/api/v1/exceptions/report")
def api_report(audience: str = "", trend_days: int = 30,
               principal: Principal = Depends(require_capability("exception.read"))):
    # Registered before /{exception_id} so "report" is not captured as an id.
    return _run(lambda: er.exception_report(principal, audience=audience or None,
                                            trend_days=max(1, min(trend_days, 120))))


@router.get("/api/v1/exceptions/{exception_id}")
def api_detail(exception_id: int, principal: Principal = Depends(require_capability("exception.read"))):
    return _run(lambda: ee.get_exception(exception_id, principal=principal, with_events=True))


@router.get("/api/v1/exceptions/{exception_id}/events")
def api_events(exception_id: int, principal: Principal = Depends(require_capability("exception.read"))):
    return {"events": _run(lambda: ee.event_history(exception_id, principal=principal))}


@router.post("/api/v1/exceptions", status_code=201)
def api_create(payload: CreateException, principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.raise_exception(
        code=payload.code, principal=principal, actor_user_id=principal.user_id,
        source=payload.source or "manual", title=payload.title, description=payload.description,
        severity=payload.severity, dedupe_key=payload.dedupe_key,
        tax_engagement_return_id=payload.tax_engagement_return_id,
        person_id=payload.person_id, household_id=payload.household_id))


@router.post("/api/v1/exceptions/{exception_id}/acknowledge")
def api_acknowledge(exception_id: int, body: ActionBody = ActionBody(),
                    principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.acknowledge(exception_id, principal=principal,
                actor_user_id=principal.user_id, expected_status=body.expected_status))


@router.post("/api/v1/exceptions/{exception_id}/start")
def api_start(exception_id: int, body: ActionBody = ActionBody(),
              principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.begin_work(exception_id, principal=principal,
                actor_user_id=principal.user_id, expected_status=body.expected_status))


@router.post("/api/v1/exceptions/{exception_id}/waiting")
def api_waiting(exception_id: int, body: ActionBody = ActionBody(),
                principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.place_waiting(exception_id, principal=principal,
                actor_user_id=principal.user_id, reason=body.reason, expected_status=body.expected_status))


@router.post("/api/v1/exceptions/{exception_id}/escalate")
def api_escalate(exception_id: int, body: ActionBody = ActionBody(),
                 principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.escalate(exception_id, principal=principal,
                actor_user_id=principal.user_id, reason=body.reason))


@router.post("/api/v1/exceptions/{exception_id}/comment")
def api_comment(exception_id: int, body: CommentBody,
                principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.comment(exception_id, body.body, principal=principal,
                actor_user_id=principal.user_id))


@router.post("/api/v1/exceptions/{exception_id}/resolve")
def api_resolve(exception_id: int, body: ResolveBody,
                principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.resolve(exception_id, body.resolution_code, principal=principal,
                actor_user_id=principal.user_id, notes=body.notes, expected_status=body.expected_status))


@router.post("/api/v1/exceptions/{exception_id}/cancel")
def api_cancel(exception_id: int, body: ActionBody = ActionBody(),
               principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.cancel(exception_id, principal=principal,
                actor_user_id=principal.user_id, reason=body.reason, expected_status=body.expected_status))


@router.post("/api/v1/exceptions/{exception_id}/reopen")
def api_reopen(exception_id: int, body: ActionBody = ActionBody(),
               principal: Principal = Depends(require_capability("exception.write"))):
    return _run(lambda: ee.reopen(exception_id, principal=principal,
                actor_user_id=principal.user_id, reason=body.reason, expected_status=body.expected_status))


@router.post("/api/v1/exceptions/{exception_id}/assign")
def api_assign(exception_id: int, body: AssignBody,
               principal: Principal = Depends(require_capability("exception.write"))):
    assignment_id = _run(lambda: ew.assign_exception(
        exception_id, principal=principal, assignment_role=body.assignment_role,
        user_id=body.user_id, team_id=body.team_id, actor_user_id=principal.user_id, reason=body.reason))
    return {"assignment_id": assignment_id}


@router.post("/api/v1/exceptions/assignments/{assignment_id}/reassign")
def api_reassign(assignment_id: int, body: ReassignBody,
                 principal: Principal = Depends(require_capability("exception.write"))):
    new_id = _run(lambda: ew.reassign_exception(
        assignment_id, principal=principal, user_id=body.user_id, team_id=body.team_id,
        actor_user_id=principal.user_id, reason=body.reason))
    return {"assignment_id": new_id}


@router.post("/api/v1/exceptions/assignments/{assignment_id}/remove")
def api_remove(assignment_id: int, body: ActionBody = ActionBody(),
               principal: Principal = Depends(require_capability("exception.write"))):
    _run(lambda: ew.remove_exception_assignment(
        assignment_id, principal=principal, actor_user_id=principal.user_id, reason=body.reason))
    return {"status": "removed"}


# --- HTML console (/exceptions) ----------------------------------------------

@router.get("/exceptions", response_class=HTMLResponse)
def console(request: Request, principal: Principal = Depends(require_capability("exception.read")),
            domain: str = "tax", status: str = "", severity: str = "", category: str = "",
            owner_user_id: str = "", return_id: str = "", person_id: str = "",
            sla_state: str = "", escalation_level: str = ""):
    filters = {"domain": domain, "status": status, "severity": severity, "category": category,
               "owner_user_id": owner_user_id, "return_id": return_id, "person_id": person_id,
               "sla_state": sla_state, "escalation_level": escalation_level}
    if domain and domain != "tax":
        return templates.TemplateResponse(request=request, name="exceptions/console.html", status_code=400,
            context={"results": [], "metrics": None, "filters": filters, "principal": principal,
                     "invalid_filter": f"Domain '{domain}' is not available in Sprint 5.5 (tax only)."})
    try:
        results = _filtered(principal, domain=domain or "tax", status=status, severity=severity,
                            category=category, owner_user_id=owner_user_id, return_id=return_id,
                            person_id=person_id, sla_state=sla_state, escalation_level=escalation_level)
        metrics = ee.metrics(principal)
    except ee.UnsupportedDomainError as exc:
        return templates.TemplateResponse(request=request, name="exceptions/console.html", status_code=400,
            context={"results": [], "metrics": None, "filters": filters, "principal": principal,
                     "invalid_filter": str(exc)})
    return templates.TemplateResponse(request=request, name="exceptions/console.html",
        context={"results": results, "metrics": metrics, "filters": filters, "principal": principal})


@router.get("/exceptions/reporting", response_class=HTMLResponse)
def reporting(request: Request, audience: str = "", trend_days: int = 30,
              principal: Principal = Depends(require_capability("exception.read"))):
    # Registered before /exceptions/{exception_id} so "reporting" is not an id.
    try:
        report = er.exception_report(principal, audience=audience or None,
                                     trend_days=max(1, min(trend_days, 120)))
    except ee.UnsupportedDomainError as exc:
        raise HTTPException(400, str(exc))
    return templates.TemplateResponse(request=request, name="exceptions/reporting.html",
        context={"report": report, "audiences": sorted(er.AUDIENCES), "principal": principal})


@router.get("/exceptions/{exception_id}", response_class=HTMLResponse)
def console_detail(exception_id: int, request: Request,
                   principal: Principal = Depends(require_capability("exception.read"))):
    try:
        data = ee.get_exception(exception_id, principal=principal, with_events=True)
    except ee.ExceptionNotFoundError:
        raise HTTPException(404, "Exception not found")
    except ee.ExceptionAuthorizationError:
        raise HTTPException(404, "Exception not found")  # out-of-scope → hide existence
    return templates.TemplateResponse(request=request, name="exceptions/detail.html",
        context={"exception": data, "principal": principal})


# --- HTML actions (form POST → redirect to detail) ---------------------------

async def _form(request):
    return await request.form()


def _redirect(exception_id):
    return RedirectResponse(f"/exceptions/{exception_id}", 303)


@router.post("/exceptions/{exception_id}/acknowledge")
async def ui_acknowledge(exception_id: int, request: Request,
                         principal: Principal = Depends(require_capability("exception.write"))):
    _run(lambda: ee.acknowledge(exception_id, principal=principal, actor_user_id=principal.user_id))
    return _redirect(exception_id)


@router.post("/exceptions/{exception_id}/start")
async def ui_start(exception_id: int, request: Request,
                   principal: Principal = Depends(require_capability("exception.write"))):
    _run(lambda: ee.begin_work(exception_id, principal=principal, actor_user_id=principal.user_id))
    return _redirect(exception_id)


@router.post("/exceptions/{exception_id}/waiting")
async def ui_waiting(exception_id: int, request: Request,
                     principal: Principal = Depends(require_capability("exception.write"))):
    form = await _form(request)
    _run(lambda: ee.place_waiting(exception_id, principal=principal, actor_user_id=principal.user_id,
                                  reason=str(form.get("reason") or "") or None))
    return _redirect(exception_id)


@router.post("/exceptions/{exception_id}/escalate")
async def ui_escalate(exception_id: int, request: Request,
                      principal: Principal = Depends(require_capability("exception.write"))):
    form = await _form(request)
    _run(lambda: ee.escalate(exception_id, principal=principal, actor_user_id=principal.user_id,
                             reason=str(form.get("reason") or "") or None))
    return _redirect(exception_id)


@router.post("/exceptions/{exception_id}/comment")
async def ui_comment(exception_id: int, request: Request,
                     principal: Principal = Depends(require_capability("exception.write"))):
    form = await _form(request)
    _run(lambda: ee.comment(exception_id, str(form.get("body") or ""), principal=principal,
                            actor_user_id=principal.user_id))
    return _redirect(exception_id)


@router.post("/exceptions/{exception_id}/resolve")
async def ui_resolve(exception_id: int, request: Request,
                     principal: Principal = Depends(require_capability("exception.write"))):
    form = await _form(request)
    _run(lambda: ee.resolve(exception_id, str(form.get("resolution_code") or "resolved"),
                            principal=principal, actor_user_id=principal.user_id,
                            notes=str(form.get("notes") or "") or None))
    return _redirect(exception_id)


@router.post("/exceptions/{exception_id}/cancel")
async def ui_cancel(exception_id: int, request: Request,
                    principal: Principal = Depends(require_capability("exception.write"))):
    form = await _form(request)
    _run(lambda: ee.cancel(exception_id, principal=principal, actor_user_id=principal.user_id,
                           reason=str(form.get("reason") or "") or None))
    return _redirect(exception_id)


@router.post("/exceptions/{exception_id}/reopen")
async def ui_reopen(exception_id: int, request: Request,
                    principal: Principal = Depends(require_capability("exception.write"))):
    _run(lambda: ee.reopen(exception_id, principal=principal, actor_user_id=principal.user_id))
    return _redirect(exception_id)


@router.post("/exceptions/{exception_id}/assign")
async def ui_assign(exception_id: int, request: Request,
                    principal: Principal = Depends(require_capability("exception.write"))):
    form = await _form(request)
    _run(lambda: ew.assign_exception(
        exception_id, principal=principal, assignment_role=str(form.get("assignment_role") or "primary"),
        user_id=int(form["user_id"]) if form.get("user_id") else None,
        team_id=int(form["team_id"]) if form.get("team_id") else None,
        actor_user_id=principal.user_id, reason=str(form.get("reason") or "") or None))
    return _redirect(exception_id)
