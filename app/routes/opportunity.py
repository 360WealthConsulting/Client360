"""Opportunity & Pipeline routes (Phase D.13).

Server-side capability gating: view / edit / delete / assign / close / report / forecast.
``/opportunities`` is outside the middleware RECORD_PATH, so the service enforces record/book
scope (an opportunity is visible to its primary/supporting/creating advisor or to a principal
whose book contains the target client). Sensitive revenue forecasts are gated by
``opportunity.forecast`` and computed server-side. No opportunity may enumerate another
advisor's pipeline.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.opportunity import reporting
from app.services.opportunity import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request: Request) -> dict[str, list[str]]:
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


@router.get("", response_class=HTMLResponse)
def board(
    request: Request, stage_id: int | None = None, status: str | None = None,
    advisor_id: int | None = None, source: str | None = None, q: str | None = None,
    page: int = 1,
    principal: Principal = Depends(require_capability("opportunity.view")),
):
    result = svc.list_opportunities(principal, stage_id=stage_id, status=status,
                                    advisor_id=advisor_id, source=source, search=q, page=page)
    pipelines = svc.list_pipelines()
    stages = svc.list_stages(pipelines[0]["id"]) if pipelines else []
    return templates.TemplateResponse(request=request, name="opportunity/board.html", context={
        "principal": principal, "result": result, "stages": stages,
        "filters": {"stage_id": stage_id or "", "status": status or "", "source": source or "",
                    "q": q or ""},
        "can_edit": principal.can("opportunity.edit"),
        "can_report": principal.can("opportunity.report")})


@router.get("/reports", response_class=HTMLResponse)
def reports(
    request: Request,
    principal: Principal = Depends(require_capability("opportunity.report")),
):
    report = reporting.pipeline_report(principal, today=date.today())
    forecast = reporting.forecast_report(principal) if principal.can("opportunity.forecast") else None
    return templates.TemplateResponse(request=request, name="opportunity/reports.html", context={
        "principal": principal, "report": report, "forecast": forecast})


@router.get("/{opportunity_id}", response_class=HTMLResponse)
def detail(
    request: Request, opportunity_id: int,
    principal: Principal = Depends(require_capability("opportunity.view")),
):
    opp = svc.get_opportunity(principal, opportunity_id)
    if opp is None:
        raise HTTPException(404, "Not found")
    stages = svc.list_stages(opp["pipeline_id"])
    return templates.TemplateResponse(request=request, name="opportunity/detail.html", context={
        "principal": principal, "o": opp, "stages": stages,
        "can_edit": principal.can("opportunity.edit"),
        "can_assign": principal.can("opportunity.assign"),
        "can_close": principal.can("opportunity.close"),
        "can_delete": principal.can("opportunity.delete")})


@router.post("")
async def create(
    request: Request,
    principal: Principal = Depends(require_capability("opportunity.edit")),
):
    form = await _form(request)
    try:
        opp = svc.create_opportunity(
            principal, title=_one(form, "title"), actor_user_id=principal.user_id,
            stage_code=_one(form, "stage_code") or None, person_id=_int(form, "person_id"),
            household_id=_int(form, "household_id"), organization_id=_int(form, "organization_id"),
            primary_advisor_id=_int(form, "primary_advisor_id"),
            primary_service_line=_one(form, "primary_service_line") or None,
            source=_one(form, "source") or None,
            expected_revenue=_one(form, "expected_revenue") or None,
            expected_close_date=_one(form, "expected_close_date") or None,
            referral_source_person_id=_int(form, "referral_source_person_id"),
            referral_source_text=_one(form, "referral_source_text") or None)
    except svc.OpportunityError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/opportunities/{opp['id']}", status_code=303)


@router.post("/{opportunity_id}")
async def update(
    request: Request, opportunity_id: int,
    principal: Principal = Depends(require_capability("opportunity.edit")),
):
    form = await _form(request)
    fields = {}
    for key in ("title", "primary_service_line", "source", "next_action", "notes",
                "referral_source_text", "originating_campaign"):
        if _one(form, key):
            fields[key] = _one(form, key)
    for key in ("expected_revenue", "probability", "expected_close_date", "next_action_date"):
        if _one(form, key):
            fields[key] = _one(form, key)
    try:
        svc.update_opportunity(principal, opportunity_id, actor_user_id=principal.user_id, fields=fields)
    except svc.OpportunityNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.OpportunityError as exc:
        return RedirectResponse(url=f"/opportunities/{opportunity_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/opportunities/{opportunity_id}", status_code=303)


@router.post("/{opportunity_id}/stage")
async def change_stage(
    request: Request, opportunity_id: int,
    principal: Principal = Depends(require_capability("opportunity.edit")),
):
    form = await _form(request)
    try:
        svc.change_stage(principal, opportunity_id, new_stage_id=_int(form, "stage_id"),
                         actor_user_id=principal.user_id, note=_one(form, "note") or None)
    except svc.OpportunityNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.OpportunityError as exc:
        return RedirectResponse(url=f"/opportunities/{opportunity_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/opportunities/{opportunity_id}", status_code=303)


@router.post("/{opportunity_id}/assign")
async def assign(
    request: Request, opportunity_id: int,
    principal: Principal = Depends(require_capability("opportunity.assign")),
):
    form = await _form(request)
    try:
        svc.assign_advisor(principal, opportunity_id, primary_advisor_id=_int(form, "primary_advisor_id"),
                           supporting_advisor_id=(_int(form, "supporting_advisor_id")
                                                  if "supporting_advisor_id" in form else "__keep__"),
                           actor_user_id=principal.user_id)
    except svc.OpportunityNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.OpportunityError as exc:
        return RedirectResponse(url=f"/opportunities/{opportunity_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/opportunities/{opportunity_id}", status_code=303)


@router.post("/{opportunity_id}/close")
async def close(
    request: Request, opportunity_id: int,
    principal: Principal = Depends(require_capability("opportunity.close")),
):
    form = await _form(request)
    try:
        svc.close_opportunity(principal, opportunity_id, outcome=_one(form, "outcome"),
                              actor_user_id=principal.user_id, reason=_one(form, "reason") or None)
    except svc.OpportunityNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.OpportunityError as exc:
        return RedirectResponse(url=f"/opportunities/{opportunity_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/opportunities/{opportunity_id}", status_code=303)


@router.post("/{opportunity_id}/activities")
async def log_activity(
    request: Request, opportunity_id: int,
    principal: Principal = Depends(require_capability("opportunity.edit")),
):
    form = await _form(request)
    try:
        svc.log_activity(principal, opportunity_id, activity_type=_one(form, "activity_type") or "note",
                         actor_user_id=principal.user_id, subject=_one(form, "subject") or None,
                         body=_one(form, "body") or None,
                         timeline_event_id=_int(form, "timeline_event_id"))
    except svc.OpportunityNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.OpportunityError as exc:
        return RedirectResponse(url=f"/opportunities/{opportunity_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/opportunities/{opportunity_id}", status_code=303)


@router.post("/{opportunity_id}/delete")
async def delete(
    request: Request, opportunity_id: int,
    principal: Principal = Depends(require_capability("opportunity.delete")),
):
    try:
        svc.delete_opportunity(principal, opportunity_id)
    except svc.OpportunityNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.OpportunityError as exc:
        return RedirectResponse(url=f"/opportunities/{opportunity_id}?error={exc}", status_code=303)
    return RedirectResponse(url="/opportunities", status_code=303)
