from typing import Optional
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel,Field

from app.portal.service import PortalPrincipal
from app.routes.portal import current_portal
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.tax_domain import list_engagements
from app.services.tax_return_lifecycle import (client_decision,decide_review,portal_returns,
    production_dashboard,record_filing,request_review,resolve_correction,return_detail,
    sync_workflow,transition_return)

router=APIRouter(tags=["tax-return-production"]); templates=Jinja2Templates(directory="app/templates")
class Transition(BaseModel): status:str; reason:Optional[str]=None
class ReviewRequest(BaseModel): review_type:str; reviewer_user_id:Optional[int]=None; reviewer_team_id:Optional[int]=None
class ReviewDecision(BaseModel): decision:str; notes:Optional[str]=None; corrections:list[str]=Field(default_factory=list)
class FilingUpdate(BaseModel): filing_status:str; provider_key:str="manual"; external_id:Optional[str]=None; submission_id:Optional[str]=None; reason_code:Optional[str]=None; message:Optional[str]=None; idempotency_key:Optional[str]=None; metadata:dict=Field(default_factory=dict)
class ClientDecision(BaseModel): approval_type:str; decision:str; notes:Optional[str]=None

def _authorized(principal,return_id):
    if return_id not in {r["return_id"] for r in list_engagements(principal)}: raise HTTPException(404,"Tax return not found")

@router.get("/tax/returns")
def production(request:Request,principal:Principal=Depends(require_capability("tax.read"))): return templates.TemplateResponse(request=request,name="tax/return_dashboard.html",context={"data":production_dashboard(principal),"view":"Production","principal":principal})
@router.get("/tax/returns/reviews")
def reviews(request:Request,principal:Principal=Depends(require_capability("tax.review"))): return templates.TemplateResponse(request=request,name="tax/return_dashboard.html",context={"data":production_dashboard(principal),"view":"Review","principal":principal})
@router.get("/tax/returns/filing")
def filing(request:Request,principal:Principal=Depends(require_capability("tax.read"))): return templates.TemplateResponse(request=request,name="tax/return_dashboard.html",context={"data":production_dashboard(principal),"view":"Filing","principal":principal})
@router.get("/tax/returns/metrics")
def metrics(request:Request,principal:Principal=Depends(require_capability("tax.read"))): return templates.TemplateResponse(request=request,name="tax/return_dashboard.html",context={"data":production_dashboard(principal),"view":"Metrics","principal":principal})

@router.get("/api/v1/tax/returns")
def api_returns(principal:Principal=Depends(require_capability("tax.read"))): return production_dashboard(principal)
@router.get("/api/v1/tax/returns/metrics")
def api_metrics(principal:Principal=Depends(require_capability("tax.read"))): return production_dashboard(principal)
@router.get("/api/v1/tax/returns/{return_id}")
def api_detail(return_id:int,principal:Principal=Depends(require_capability("tax.read"))): _authorized(principal,return_id); return return_detail(return_id)
@router.post("/api/v1/tax/returns/{return_id}/lifecycle")
def api_transition(return_id:int,payload:Transition,request:Request,principal:Principal=Depends(require_capability("tax.write"))):
    _authorized(principal,return_id)
    try:return {"status":transition_return(return_id,payload.status,actor_user_id=principal.user_id,reason=payload.reason,request_id=request.state.request_id)}
    except ValueError as exc:raise HTTPException(400,str(exc))
@router.post("/api/v1/tax/returns/{return_id}/workflow-sync")
def api_workflow(return_id:int,principal:Principal=Depends(require_capability("tax.write"))): _authorized(principal,return_id); return {"status":sync_workflow(return_id,actor_user_id=principal.user_id)}
@router.post("/api/v1/tax/returns/{return_id}/reviews",status_code=201)
def api_review_request(return_id:int,payload:ReviewRequest,principal:Principal=Depends(require_capability("tax.review"))):
    _authorized(principal,return_id)
    try:return {"id":request_review(return_id,payload.review_type,requested_by_user_id=principal.user_id,reviewer_user_id=payload.reviewer_user_id,reviewer_team_id=payload.reviewer_team_id)}
    except ValueError as exc:raise HTTPException(400,str(exc))
@router.post("/api/v1/tax/returns/reviews/{review_id}/decision")
def api_review_decision(review_id:int,payload:ReviewDecision,request:Request,principal:Principal=Depends(require_capability("tax.review"))):
    try:return {"status":decide_review(review_id,payload.decision,reviewer_user_id=principal.user_id,notes=payload.notes,corrections=payload.corrections,request_id=request.state.request_id)}
    except (ValueError,PermissionError) as exc:raise HTTPException(400,str(exc))
@router.post("/api/v1/tax/returns/review-corrections/{correction_id}/resolve",status_code=204)
def api_resolve(correction_id:int,principal:Principal=Depends(require_capability("tax.write"))):
    try:resolve_correction(correction_id,actor_user_id=principal.user_id)
    except ValueError as exc:raise HTTPException(404,str(exc))
@router.post("/api/v1/tax/returns/{return_id}/filing")
def api_filing(return_id:int,payload:FilingUpdate,request:Request,principal:Principal=Depends(require_capability("tax.write"))):
    _authorized(principal,return_id)
    try:return {"filing_status":record_filing(return_id,**payload.dict(),actor_user_id=principal.user_id,request_id=request.state.request_id)}
    except ValueError as exc:raise HTTPException(400,str(exc))

@router.get("/portal/tax-returns")
def portal_page(request:Request,principal:PortalPrincipal=Depends(current_portal)): return templates.TemplateResponse(request=request,name="portal/tax_returns.html",context={"returns":portal_returns(principal),"principal":principal})
@router.get("/api/v1/portal/tax/returns")
def portal_api(principal:PortalPrincipal=Depends(current_portal)): return {"returns":portal_returns(principal)}
@router.post("/api/v1/portal/tax/returns/{return_id}/decision")
def portal_decide(return_id:int,payload:ClientDecision,request:Request,principal:PortalPrincipal=Depends(current_portal)):
    try:return {"status":client_decision(return_id,payload.approval_type,payload.decision,portal_principal=principal,notes=payload.notes,request_id=request.state.request_id)}
    except (ValueError,PermissionError) as exc:raise HTTPException(403,str(exc))
