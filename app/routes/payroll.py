"""Payroll Hub — staff console + client-facing portal view (360Plus / Client360, foundation).

Thin HTTP layer over :mod:`app.services.payroll` — no business logic here. Staff routes live under
``/business/{organization_id}/payroll`` and are capability-gated (``payroll.read`` / ``payroll.write``);
the service re-checks record scope (defense in depth). The client-facing view lives under
``/portal/business/{organization_id}/payroll`` and is gated by the per-client Payroll feature
(``client_can``) plus portal scope — a completely separate access path from staff.

Information + workflow only: no payroll submission, direct deposit, ACH, tax payment, or money movement.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, relationship_entities
from app.portal.service import PortalPrincipal
from app.routes.portal import current_portal
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import payroll as pay

router = APIRouter(tags=["payroll"])
templates = Jinja2Templates(directory="app/templates")


# --- helpers -----------------------------------------------------------------

def _run(fn):
    """Call a service function and translate its errors to HTTP responses."""
    try:
        return fn()
    except pay.PayrollNotFound:
        raise HTTPException(404, "Not found") from None
    except pay.PayrollUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except PermissionError as exc:
        raise HTTPException(403 if "capability" in str(exc).lower() else 404, str(exc)) from None
    except pay.PayrollError as exc:
        raise HTTPException(400, str(exc)) from None


def _org_name(organization_id):
    with engine.connect() as c:
        return c.scalar(select(relationship_entities.c.name).where(
            relationship_entities.c.id == organization_id)) or f"Business #{organization_id}"


def _cents(value):
    """Form dollars -> integer cents. Blank/None -> None."""
    if value is None or str(value).strip() == "":
        return None
    return round(float(value) * 100)


def _dash(organization_id):
    return RedirectResponse(f"/business/{organization_id}/payroll", status_code=303)


# --- staff console -----------------------------------------------------------

@router.get("/business/{organization_id}/payroll", response_class=HTMLResponse)
def payroll_dashboard(organization_id: int, request: Request,
                      principal: Principal = Depends(require_capability("payroll.read"))):
    summary = _run(lambda: pay.payroll_summary(organization_id, principal=principal))
    ctx = {
        "request": request,
        "organization_id": organization_id,
        "org_name": _org_name(organization_id),
        "summary": summary,
        "employees": _run(lambda: pay.list_employees(organization_id, principal=principal)),
        "runs": _run(lambda: pay.list_runs(organization_id, principal=principal)),
        "documents": _run(lambda: pay.list_documents(organization_id, principal=principal)),
        "issues": _run(lambda: pay.list_issues(organization_id, principal=principal)),
        "providers": pay.list_providers(),
        "issue_types": sorted(pay.ISSUE_TYPES),
        "pay_frequencies": sorted(pay.PAY_FREQUENCIES),
        "doc_categories": sorted(pay.DOC_CATEGORIES),
    }
    return templates.TemplateResponse("payroll/dashboard.html", ctx)


@router.post("/business/{organization_id}/payroll/accounts")
def create_account(organization_id: int, provider_code: str = Form(""),
                   external_account_id: str = Form(""), pay_frequency: str = Form(""),
                   next_payroll_date: str = Form(""), notes: str = Form(""),
                   principal: Principal = Depends(require_capability("payroll.write"))):
    _run(lambda: pay.create_account(
        principal, organization_id=organization_id, provider_code=provider_code or None,
        external_account_id=external_account_id or None, pay_frequency=pay_frequency or None,
        next_payroll_date=next_payroll_date or None, notes=notes or None))
    return _dash(organization_id)


@router.post("/business/{organization_id}/payroll/employees")
def add_employee(organization_id: int, payroll_account_id: int = Form(...), full_name: str = Form(...),
                 employment_status: str = Form("active"), hire_date: str = Form(""),
                 compensation_type: str = Form(""), compensation_amount: str = Form(""),
                 compensation_period: str = Form("annual"), provider_employee_id: str = Form(""),
                 retirement_plan_participant: bool = Form(False),
                 principal: Principal = Depends(require_capability("payroll.write"))):
    _run(lambda: pay.add_employee(
        principal, payroll_account_id=payroll_account_id, full_name=full_name,
        employment_status=employment_status, hire_date=hire_date or None,
        compensation_type=compensation_type or None, compensation_amount_cents=_cents(compensation_amount),
        compensation_period=compensation_period, provider_employee_id=provider_employee_id or None,
        retirement_plan_participant=retirement_plan_participant))
    return _dash(organization_id)


@router.post("/business/{organization_id}/payroll/runs")
def record_run(organization_id: int, payroll_account_id: int = Form(...), period_start: str = Form(""),
               period_end: str = Form(""), pay_date: str = Form(""), status: str = Form("scheduled"),
               gross: str = Form(""), employee_taxes: str = Form(""), employer_taxes: str = Form(""),
               deductions: str = Form(""), retirement_contributions: str = Form(""),
               benefits: str = Form(""), net: str = Form(""),
               principal: Principal = Depends(require_capability("payroll.write"))):
    _run(lambda: pay.record_run(
        principal, payroll_account_id=payroll_account_id, period_start=period_start or None,
        period_end=period_end or None, pay_date=pay_date or None, status=status,
        gross_cents=_cents(gross), employee_taxes_cents=_cents(employee_taxes),
        employer_taxes_cents=_cents(employer_taxes), deductions_cents=_cents(deductions),
        retirement_contributions_cents=_cents(retirement_contributions), benefits_cents=_cents(benefits),
        net_cents=_cents(net)))
    return _dash(organization_id)


@router.post("/business/{organization_id}/payroll/documents")
def link_document(organization_id: int, document_id: int = Form(...), category: str = Form(...),
                  payroll_account_id: str = Form(""), period_label: str = Form(""),
                  principal: Principal = Depends(require_capability("payroll.write"))):
    _run(lambda: pay.link_document(
        principal, organization_id=organization_id, document_id=document_id, category=category,
        payroll_account_id=int(payroll_account_id) if payroll_account_id else None,
        period_label=period_label or None))
    return _dash(organization_id)


@router.post("/business/{organization_id}/payroll/issues")
def open_issue(organization_id: int, issue_type: str = Form(...), title: str = Form(...),
               description: str = Form(""), severity: str = Form("medium"),
               payroll_account_id: str = Form(""), due_date: str = Form(""),
               principal: Principal = Depends(require_capability("payroll.write"))):
    _run(lambda: pay.open_issue(
        principal, organization_id=organization_id, issue_type=issue_type, title=title,
        description=description or None, severity=severity,
        payroll_account_id=int(payroll_account_id) if payroll_account_id else None,
        due_date=due_date or None))
    return _dash(organization_id)


@router.post("/business/{organization_id}/payroll/issues/{issue_id}/status")
def set_issue_status(organization_id: int, issue_id: int, status: str = Form(...),
                     principal: Principal = Depends(require_capability("payroll.write"))):
    _run(lambda: pay.set_issue_status(issue_id, status, principal=principal))
    return _dash(organization_id)


# --- client-facing portal view (separate access path) ------------------------

@router.get("/portal/business/{organization_id}/payroll", response_class=HTMLResponse)
def portal_payroll(organization_id: int, request: Request,
                   principal: PortalPrincipal = Depends(current_portal)):
    """Read-only client view. Authenticated by the standard portal dependency (401 without a portal
    session); then gated by portal scope (404 if the business is not the client's) and the per-client
    Payroll feature (403). NOT gated by staff capabilities. Shows only client-safe headline numbers."""
    from app.portal.service import portal_base_scope
    from app.services.features.service import client_can
    # Relationship scope only; the entity-scoped client_can below is the authorization.
    scope = portal_base_scope(principal.account_id)
    if organization_id not in set(scope.get("organization_ids", [])):
        raise HTTPException(404, "Not found")
    if not client_can(principal, pay.FEATURE_KEY, organization_id=organization_id):
        raise HTTPException(403, "This part of the portal isn't available right now. Please contact your advisor if you need it.")
    summary = _run(lambda: pay.portal_summary(organization_id))
    return templates.TemplateResponse("payroll/portal.html", {
        "request": request, "organization_id": organization_id,
        "org_name": _org_name(organization_id), "summary": summary})
