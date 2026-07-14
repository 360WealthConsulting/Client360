from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.tax_domain import create_engagement, dashboard, override_deadline, reference_data

router = APIRouter(tags=["tax-domain"])
templates = Jinja2Templates(directory="app/templates")

class EngagementCreate(BaseModel):
    tax_year: int; return_type: str
    person_id: Optional[int] = None; household_id: Optional[int] = None
    relationship_entity_id: Optional[int] = None; jurisdiction: str = "US"
    filing_status: str = "na"; firm_code: str = "360-tax"; office_code: str = "primary"
    priority: str = "normal"; assignee_user_id: Optional[int] = None; metadata: dict = Field(default_factory=dict)

class DeadlineOverride(BaseModel):
    due_date: date; reason: str

@router.get("/tax")
def tax_dashboard(request: Request, tax_year: Optional[int] = None, office_id: Optional[int] = None,
                  status: Optional[str] = None, principal: Principal = Depends(require_capability("tax.read"))):
    return templates.TemplateResponse(request=request, name="tax/dashboard.html", context={"tax": dashboard(principal, tax_year=tax_year, office_id=office_id, status=status), "reference": reference_data(), "principal": principal})

@router.get("/api/v1/tax/reference-data")
def api_reference(principal: Principal = Depends(require_capability("tax.read"))): return reference_data()

@router.get("/api/v1/tax/firms")
def api_firms(principal: Principal = Depends(require_capability("tax.read"))): return {"firms": reference_data()["firms"]}

@router.get("/api/v1/tax/offices")
def api_offices(principal: Principal = Depends(require_capability("tax.read"))): return {"offices": reference_data()["offices"]}

@router.get("/api/v1/tax/tax-years")
def api_years(principal: Principal = Depends(require_capability("tax.read"))): return {"tax_years": reference_data()["tax_years"]}

@router.get("/api/v1/tax/jurisdictions")
def api_jurisdictions(principal: Principal = Depends(require_capability("tax.read"))): return {"jurisdictions": reference_data()["jurisdictions"]}

@router.get("/api/v1/tax/return-types")
def api_return_types(principal: Principal = Depends(require_capability("tax.read"))): return {"return_types": reference_data()["return_types"]}

@router.get("/api/v1/tax/filing-statuses")
def api_filing_statuses(principal: Principal = Depends(require_capability("tax.read"))): return {"filing_statuses": reference_data()["filing_statuses"]}

@router.get("/api/v1/tax/dashboard")
def api_dashboard(tax_year: Optional[int] = None, office_id: Optional[int] = None, status: Optional[str] = None,
                  principal: Principal = Depends(require_capability("tax.read"))):
    return dashboard(principal, tax_year=tax_year, office_id=office_id, status=status)

@router.get("/api/v1/tax/engagements")
def api_engagements(tax_year: Optional[int] = None, office_id: Optional[int] = None, status: Optional[str] = None,
                    principal: Principal = Depends(require_capability("tax.read"))):
    return {"engagements": dashboard(principal, tax_year=tax_year, office_id=office_id, status=status)["items"]}

@router.post("/api/v1/tax/engagements", status_code=201)
def api_create(payload: EngagementCreate, request: Request, principal: Principal = Depends(require_capability("tax.write"))):
    if not any((payload.person_id, payload.relationship_entity_id)):
        raise HTTPException(400, "A person or relationship entity is required")
    try: return create_engagement(payload.dict(), actor_user_id=principal.user_id, request_id=request.state.request_id)
    except ValueError as exc: raise HTTPException(400, str(exc))

@router.patch("/api/v1/tax/deadlines/{deadline_id}")
def api_override_deadline(deadline_id: int, payload: DeadlineOverride, request: Request,
                          principal: Principal = Depends(require_capability("tax.deadline.manage"))):
    try: override_deadline(deadline_id, payload.due_date, payload.reason, actor_user_id=principal.user_id, request_id=request.state.request_id)
    except ValueError as exc: raise HTTPException(400, str(exc))
    return {"id": deadline_id, "due_date": payload.due_date}
