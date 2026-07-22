"""Campaign routes (Phase D.14). Server-side capability gating; sensitive budget/ROI fields
require campaign.manage_budget / campaign.manage_roi (enforced in the service)."""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.campaign import reporting
from app.services.campaign import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


@router.get("", response_class=HTMLResponse)
def board(request: Request, status: str | None = None, q: str | None = None, page: int = 1,
          principal: Principal = Depends(require_capability("campaign.view"))):
    result = svc.list_campaigns(principal, status=status, search=q, page=page)
    return templates.TemplateResponse(request=request, name="campaign/board.html", context={
        "principal": principal, "result": result, "filters": {"status": status or "", "q": q or ""},
        "can_edit": principal.can("campaign.edit"), "can_report": principal.can("campaign.report")})


@router.get("/reports", response_class=HTMLResponse)
def reports(request: Request, principal: Principal = Depends(require_capability("campaign.report"))):
    return templates.TemplateResponse(request=request, name="campaign/reports.html", context={
        "principal": principal, "report": reporting.campaign_report(principal)})


@router.get("/{campaign_id}", response_class=HTMLResponse)
def detail(request: Request, campaign_id: int,
           principal: Principal = Depends(require_capability("campaign.view"))):
    c = svc.get_campaign(principal, campaign_id)
    if c is None:
        raise HTTPException(404, "Not found")
    perf = reporting.campaign_performance(principal, c) if principal.can("campaign.report") else None
    return templates.TemplateResponse(request=request, name="campaign/detail.html", context={
        "principal": principal, "c": c, "perf": perf,
        "can_edit": principal.can("campaign.edit"), "can_archive": principal.can("campaign.archive"),
        "can_delete": principal.can("campaign.delete"),
        "can_budget": principal.can("campaign.manage_budget"),
        "can_roi": principal.can("campaign.manage_roi")})


@router.post("")
async def create(request: Request, principal: Principal = Depends(require_capability("campaign.edit"))):
    form = await _form(request)
    try:
        c = svc.create_campaign(principal, name=_one(form, "name"), actor_user_id=principal.user_id,
                                campaign_type=_one(form, "campaign_type") or None,
                                marketing_channel=_one(form, "marketing_channel") or None,
                                objective=_one(form, "objective") or None,
                                start_date=_one(form, "start_date") or None,
                                end_date=_one(form, "end_date") or None)
    except svc.CampaignPermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except svc.CampaignError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/campaigns/{c['id']}", status_code=303)


@router.post("/{campaign_id}")
async def update(request: Request, campaign_id: int,
                 principal: Principal = Depends(require_capability("campaign.edit"))):
    form = await _form(request)
    fields = {}
    for key in ("name", "campaign_type", "marketing_channel", "objective", "target_audience",
                "notes", "start_date", "end_date", "budget", "actual_cost", "expected_roi",
                "actual_roi"):
        if _one(form, key):
            fields[key] = _one(form, key)
    try:
        svc.update_campaign(principal, campaign_id, actor_user_id=principal.user_id, fields=fields)
    except svc.CampaignPermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except svc.CampaignNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/status")
async def status(request: Request, campaign_id: int,
                 principal: Principal = Depends(require_capability("campaign.edit"))):
    form = await _form(request)
    try:
        svc.set_status(principal, campaign_id, new_status=_one(form, "status"),
                       actor_user_id=principal.user_id, note=_one(form, "note") or None)
    except svc.CampaignPermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except svc.CampaignNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.CampaignError as exc:
        return RedirectResponse(url=f"/campaigns/{campaign_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/activities")
async def log_activity(request: Request, campaign_id: int,
                       principal: Principal = Depends(require_capability("campaign.edit"))):
    form = await _form(request)
    tid = _one(form, "timeline_event_id")
    try:
        svc.log_activity(principal, campaign_id, activity_type=_one(form, "activity_type") or "note",
                         actor_user_id=principal.user_id, subject=_one(form, "subject") or None,
                         body=_one(form, "body") or None, timeline_event_id=int(tid) if tid else None)
    except svc.CampaignNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.CampaignError as exc:
        return RedirectResponse(url=f"/campaigns/{campaign_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/{campaign_id}/delete")
async def delete(request: Request, campaign_id: int,
                 principal: Principal = Depends(require_capability("campaign.delete"))):
    try:
        svc.delete_campaign(principal, campaign_id)
    except svc.CampaignNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return RedirectResponse(url="/campaigns", status_code=303)
