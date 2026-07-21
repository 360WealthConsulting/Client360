"""Business Owner Planning Workspace routes (Phase D.12).

Read-first composition workspace. Gated server-side by ``business_owner.read`` /
``business_owner.planning_update``. ``/business-owner/*`` is outside the
``^/(people|households)`` middleware RECORD_PATH, so the service enforces person record
scope (scope-first) and, for a business, a validated ownership/organization relationship
(blocks URL enumeration). The workspace never bypasses tax / benefits / insurance /
advisor_work / timeline / compliance / annual_review permissions — sensitive sections are
omitted or redacted in the service. The only mutation is the D.12-owned planning profile.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import business_owner as svc
from app.templating import install_filters

router = APIRouter(prefix="/business-owner", tags=["business-owner"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request: Request) -> dict[str, list[str]]:
    return parse_qs((await request.body()).decode("utf-8"))


@router.get("/{person_id}", response_class=HTMLResponse)
def workspace(
    request: Request, person_id: int,
    principal: Principal = Depends(require_capability("business_owner.read")),
):
    ws = svc.compose_person_workspace(principal, person_id)
    if ws is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(
        request=request, name="business_owner/workspace.html",
        context={"request": request, "principal": principal, "ws": ws})


@router.get("/{person_id}/business/{business_id}", response_class=HTMLResponse)
def business_detail(
    request: Request, person_id: int, business_id: int,
    principal: Principal = Depends(require_capability("business_owner.read")),
):
    detail = svc.compose_business_detail(principal, person_id, business_id)
    if detail is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(
        request=request, name="business_owner/business.html",
        context={"request": request, "principal": principal, "d": detail,
                 "can_plan": principal.can("business_owner.planning_update"),
                 "status_vocab": svc.PLANNING_STATUS_VOCAB, "source_vocab": svc.SOURCE_VOCAB})


@router.post("/{person_id}/business/{business_id}/planning")
async def update_planning(
    request: Request, person_id: int, business_id: int,
    principal: Principal = Depends(require_capability("business_owner.planning_update")),
):
    form = await _form(request)

    def one(key):
        return (form.get(key, [""])[0]).strip()

    fields = {f: one(f) for f in svc._STATUS_FIELDS if one(f)}
    if one("source_type"):
        fields["source_type"] = one("source_type")
    if one("notes"):
        fields["notes"] = one("notes")
    for f in ("successor_person_id", "emergency_contact_person_id", "valuation_amount",
              "valuation_as_of", "buy_sell_reviewed_at"):
        if one(f):
            fields[f] = one(f)
    try:
        svc.upsert_planning_profile(principal, person_id=person_id, business_id=business_id,
                                    fields=fields)
    except svc.BusinessNotInScopeError as exc:
        raise HTTPException(404, "Not found") from exc
    except svc.PlanningValidationError as exc:
        return RedirectResponse(
            url=f"/business-owner/{person_id}/business/{business_id}?error={exc}",
            status_code=303)
    return RedirectResponse(
        url=f"/business-owner/{person_id}/business/{business_id}", status_code=303)
