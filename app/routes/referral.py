"""Referral Source routes (Phase D.14). Server-side capability gating + book scope in-service."""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.referral import reporting
from app.services.referral import service as svc
from app.templating import install_filters

router = APIRouter(prefix="/referral-sources", tags=["referral-sources"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


@router.get("", response_class=HTMLResponse)
def board(request: Request, status: str | None = None, source_type: str | None = None,
          q: str | None = None, page: int = 1,
          principal: Principal = Depends(require_capability("referral.view"))):
    result = svc.list_referral_sources(principal, status=status, source_type=source_type,
                                       search=q, page=page)
    return templates.TemplateResponse(request=request, name="referral/board.html", context={
        "principal": principal, "result": result,
        "filters": {"status": status or "", "source_type": source_type or "", "q": q or ""},
        "can_edit": principal.can("referral.edit"), "can_report": principal.can("referral.report")})


@router.get("/reports", response_class=HTMLResponse)
def reports(request: Request, principal: Principal = Depends(require_capability("referral.report"))):
    return templates.TemplateResponse(request=request, name="referral/reports.html", context={
        "principal": principal, "report": reporting.referral_report(principal)})


@router.get("/{referral_source_id}", response_class=HTMLResponse)
def detail(request: Request, referral_source_id: int,
           principal: Principal = Depends(require_capability("referral.view"))):
    s = svc.get_referral_source(principal, referral_source_id)
    if s is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="referral/detail.html", context={
        "principal": principal, "s": s, "can_edit": principal.can("referral.edit"),
        "can_delete": principal.can("referral.delete")})


@router.post("")
async def create(request: Request, principal: Principal = Depends(require_capability("referral.edit"))):
    form = await _form(request)
    try:
        s = svc.create_referral_source(
            principal, name=_one(form, "name"), source_type=_one(form, "source_type") or "other",
            actor_user_id=principal.user_id, relationship_type=_one(form, "relationship_type") or None,
            person_id=_int(form, "person_id"), organization_id=_int(form, "organization_id"),
            email=_one(form, "email") or None, phone=_one(form, "phone") or None,
            primary_advisor_id=_int(form, "primary_advisor_id"), notes=_one(form, "notes") or None)
    except svc.ReferralError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/referral-sources/{s['id']}", status_code=303)


@router.post("/{referral_source_id}")
async def update(request: Request, referral_source_id: int,
                 principal: Principal = Depends(require_capability("referral.edit"))):
    form = await _form(request)
    fields = {k: _one(form, k) for k in ("name", "source_type", "relationship_type", "email",
                                         "phone", "notes") if _one(form, k)}
    try:
        svc.update_referral_source(principal, referral_source_id, actor_user_id=principal.user_id,
                                   fields=fields)
    except svc.ReferralNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.ReferralError as exc:
        return RedirectResponse(url=f"/referral-sources/{referral_source_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/referral-sources/{referral_source_id}", status_code=303)


@router.post("/{referral_source_id}/status")
async def status(request: Request, referral_source_id: int,
                 principal: Principal = Depends(require_capability("referral.edit"))):
    form = await _form(request)
    try:
        svc.set_status(principal, referral_source_id, new_status=_one(form, "status"),
                       actor_user_id=principal.user_id, note=_one(form, "note") or None)
    except svc.ReferralNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.ReferralError as exc:
        return RedirectResponse(url=f"/referral-sources/{referral_source_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/referral-sources/{referral_source_id}", status_code=303)


@router.post("/{referral_source_id}/delete")
async def delete(request: Request, referral_source_id: int,
                 principal: Principal = Depends(require_capability("referral.delete"))):
    try:
        svc.delete_referral_source(principal, referral_source_id)
    except svc.ReferralNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return RedirectResponse(url="/referral-sources", status_code=303)
