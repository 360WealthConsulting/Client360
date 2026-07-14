from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.portal.service import PortalPrincipal
from app.routes.portal import current_portal
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.tax_intake import (accept_letter, intake_detail, launch_intake,
    portal_intakes, process_reminders, save_organizer, save_questionnaire,
    staff_dashboard, sync_documents, template_catalog)
from app.services.tax_domain import list_engagements

router=APIRouter(tags=["tax-intake"]); templates=Jinja2Templates(directory="app/templates")

class ResponseSave(BaseModel):
    responses: dict = Field(default_factory=dict); complete: bool = False
class Acceptance(BaseModel): metadata: dict = Field(default_factory=dict)

def _authorize_return(principal, return_id):
    if return_id not in {row["return_id"] for row in list_engagements(principal)}:
        raise HTTPException(404,"Tax intake not found")

@router.get("/tax/intake")
def intake_dashboard(request:Request, principal:Principal=Depends(require_capability("tax.intake.read"))):
    return templates.TemplateResponse(request=request,name="tax/intake_dashboard.html",context={"data":staff_dashboard(principal),"principal":principal})

@router.get("/api/v1/tax/intake")
def api_dashboard(principal:Principal=Depends(require_capability("tax.intake.read"))): return staff_dashboard(principal)

@router.get("/api/v1/tax/intake/templates")
def api_templates(principal:Principal=Depends(require_capability("tax.intake.read"))): return template_catalog()

@router.get("/api/v1/tax/intake/{return_id}")
def api_detail(return_id:int, principal:Principal=Depends(require_capability("tax.intake.read"))):
    allowed={r["return_id"] for r in staff_dashboard(principal)["items"]}
    if return_id not in allowed: raise HTTPException(404,"Tax intake not found")
    return intake_detail(return_id)

@router.post("/api/v1/tax/intake/{return_id}/launch",status_code=201)
def api_launch(return_id:int, request:Request, principal:Principal=Depends(require_capability("tax.intake.write"))):
    _authorize_return(principal,return_id)
    try: return launch_intake(return_id,actor_user_id=principal.user_id,request_id=request.state.request_id)
    except ValueError as exc: raise HTTPException(400,str(exc))

@router.put("/api/v1/tax/intake/{return_id}/organizer")
def api_organizer(return_id:int,payload:ResponseSave,request:Request,principal:Principal=Depends(require_capability("tax.intake.write"))):
    _authorize_return(principal,return_id)
    try: return save_organizer(return_id,payload.responses,actor_user_id=principal.user_id,complete=payload.complete,request_id=request.state.request_id)
    except (ValueError,PermissionError) as exc: raise HTTPException(400,str(exc))

@router.put("/api/v1/tax/intake/{return_id}/questionnaire")
def api_questionnaire(return_id:int,payload:ResponseSave,request:Request,principal:Principal=Depends(require_capability("tax.intake.write"))):
    _authorize_return(principal,return_id)
    try: return save_questionnaire(return_id,payload.responses,actor_user_id=principal.user_id,complete=payload.complete,request_id=request.state.request_id)
    except (ValueError,PermissionError) as exc: raise HTTPException(400,str(exc))

@router.post("/api/v1/tax/intake/{return_id}/documents/sync")
def api_sync(return_id:int,principal:Principal=Depends(require_capability("tax.intake.write"))):
    _authorize_return(principal,return_id); return sync_documents(return_id)

@router.post("/api/v1/tax/intake/reminders")
def api_reminders(principal:Principal=Depends(require_capability("tax.intake.write"))): return {"sent":process_reminders()}

@router.get("/portal/tax-intake")
def portal_page(request:Request,principal:PortalPrincipal=Depends(current_portal)):
    return templates.TemplateResponse(request=request,name="portal/tax_intake.html",context={"intakes":portal_intakes(principal),"principal":principal})

@router.get("/api/v1/portal/tax/intake")
def portal_api(principal:PortalPrincipal=Depends(current_portal)): return {"intakes":portal_intakes(principal)}

@router.post("/api/v1/portal/tax/intake/{return_id}/letter/accept")
def portal_accept(return_id:int,payload:Acceptance,request:Request,principal:PortalPrincipal=Depends(current_portal)):
    try: return {"id":accept_letter(return_id,portal_principal=principal,metadata=payload.metadata,request_id=request.state.request_id)}
    except (ValueError,PermissionError) as exc: raise HTTPException(403,str(exc))

@router.put("/api/v1/portal/tax/intake/{return_id}/organizer")
def portal_organizer(return_id:int,payload:ResponseSave,request:Request,principal:PortalPrincipal=Depends(current_portal)):
    try: return save_organizer(return_id,payload.responses,portal_principal=principal,complete=payload.complete,request_id=request.state.request_id)
    except (ValueError,PermissionError) as exc: raise HTTPException(403,str(exc))

@router.put("/api/v1/portal/tax/intake/{return_id}/questionnaire")
def portal_questionnaire(return_id:int,payload:ResponseSave,request:Request,principal:PortalPrincipal=Depends(current_portal)):
    try: return save_questionnaire(return_id,payload.responses,portal_principal=principal,complete=payload.complete,request_id=request.state.request_id)
    except (ValueError,PermissionError) as exc: raise HTTPException(403,str(exc))

@router.post("/api/v1/portal/tax/intake/{return_id}/documents/sync")
def portal_sync(return_id:int,principal:PortalPrincipal=Depends(current_portal)):
    detail=next((i for i in portal_intakes(principal) if i["context"]["return_id"]==return_id),None)
    if not detail: raise HTTPException(403,"Tax intake outside portal scope")
    return sync_documents(return_id)
