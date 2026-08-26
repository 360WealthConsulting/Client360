"""Billing & Invoicing routes — staff management + client (portal) billing area.

Staff routes: capability-gated (billing.read to view, billing.write to mutate) AND record-scoped on the
bill-to subject (the service re-checks scope and raises PermissionError → 403). Post/Redirect/Get.

Client routes: under /portal/billing, so the auth middleware enforces the ``billing`` / ``invoice_view``
features (portal_gate) before the route runs; the service additionally scopes every read to the client's
own person/household/organization (per-organization: ABC LLC invoices never appear under XYZ LLC).
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.portal.service import PortalPrincipal
from app.routes.portal import current_portal
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.billing import constants as k
from app.services.billing import service as b

router = APIRouter(tags=["billing"])
templates = Jinja2Templates(directory="app/templates")


def _rid(request):
    return getattr(request.state, "request_id", "billing")


def _dollars_to_cents(value) -> int:
    v = (value or "").strip().replace("$", "").replace(",", "")
    if not v:
        raise b.BillingError("An amount is required")
    try:
        return int(round(float(v) * 100))
    except ValueError as exc:
        raise b.BillingError("Invalid amount") from exc


def _valid_subject(subject_type):
    if subject_type not in k.SUBJECT_TYPES:
        raise HTTPException(404, "Unknown subject type")


# --- staff ------------------------------------------------------------------

@router.get("/billing/{subject_type}/{subject_id}", response_class=HTMLResponse)
def billing_panel(subject_type: str, subject_id: int, request: Request,
                  principal: Principal = Depends(require_capability("billing.read"))):
    _valid_subject(subject_type)
    if not b.in_scope(principal, subject_type, subject_id, write=False):
        raise HTTPException(403, f"{subject_type} is outside your record scope")
    return templates.TemplateResponse(request=request, name="billing/staff_panel.html", context={
        "principal": principal, "subject_type": subject_type, "subject_id": subject_id, "money": k.money,
        "summary": b.subject_billing_summary(subject_type, subject_id),
        "agreements": b.list_agreements(subject_type, subject_id),
        "invoices": b.list_invoices(subject_type, subject_id),
        "payments": b.payment_history(subject_type, subject_id),
        "signal": b.billing_active_signal(subject_type, subject_id),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


@router.get("/billing/invoices/{invoice_id}", response_class=HTMLResponse)
def staff_invoice_detail(invoice_id: int, request: Request,
                         principal: Principal = Depends(require_capability("billing.read"))):
    inv = b.load_invoice(invoice_id)
    if inv is None or not b.in_scope(principal, inv["bill_to_type"], inv["bill_to_id"], write=False):
        raise HTTPException(404, "Invoice not found")           # scope denial hides existence
    return templates.TemplateResponse(request=request, name="billing/staff_invoice.html", context={
        "principal": principal, "invoice": b.invoice_detail(invoice_id), "money": k.money,
        "line_kinds": k.LINE_KINDS, "notice": request.query_params.get("notice"),
        "error": request.query_params.get("error")})


def _panel_redirect(subject_type, subject_id, *, notice=None, error=None):
    q = ("?notice=" + quote(notice)) if notice else (("?error=" + quote(error)) if error else "")
    return RedirectResponse(f"/billing/{subject_type}/{subject_id}{q}", status_code=303)


def _invoice_redirect(invoice_id, *, notice=None, error=None):
    q = ("?notice=" + quote(notice)) if notice else (("?error=" + quote(error)) if error else "")
    return RedirectResponse(f"/billing/invoices/{invoice_id}{q}", status_code=303)


def _subject_of_invoice(invoice_id):
    inv = b.load_invoice(invoice_id)
    if inv is None:
        raise HTTPException(404, "Invoice not found")
    return inv["bill_to_type"], inv["bill_to_id"]


@router.post("/billing/{subject_type}/{subject_id}/agreements")
def create_agreement(subject_type: str, subject_id: int, request: Request, title: str = Form(...),
                     service_line_code: str | None = Form(None), amount: str | None = Form(None),
                     principal: Principal = Depends(require_capability("billing.write"))):
    _valid_subject(subject_type)
    try:
        cents = _dollars_to_cents(amount) if (amount or "").strip() else None
        b.create_agreement(principal, bill_to_type=subject_type, bill_to_id=subject_id, title=title,
                           service_line_code=(service_line_code or None), default_amount_cents=cents,
                           request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except b.BillingError as exc:
        return _panel_redirect(subject_type, subject_id, error=str(exc))
    return _panel_redirect(subject_type, subject_id, notice="Service agreement created.")


@router.post("/billing/agreements/{agreement_id}/status")
def set_agreement_status(agreement_id: int, request: Request, status: str = Form(...),
                         principal: Principal = Depends(require_capability("billing.write"))):
    ag = b.load_agreement(agreement_id)
    if ag is None:
        raise HTTPException(404, "Agreement not found")
    try:
        b.set_agreement_status(principal, agreement_id, status, request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except b.BillingError as exc:
        return _panel_redirect(ag["bill_to_type"], ag["bill_to_id"], error=str(exc))
    return _panel_redirect(ag["bill_to_type"], ag["bill_to_id"], notice="Agreement updated.")


@router.post("/billing/agreements/{agreement_id}/schedules")
def create_schedule(agreement_id: int, request: Request, frequency: str = Form(...), amount: str = Form(...),
                    principal: Principal = Depends(require_capability("billing.write"))):
    ag = b.load_agreement(agreement_id)
    if ag is None:
        raise HTTPException(404, "Agreement not found")
    try:
        b.create_schedule(principal, agreement_id, frequency=frequency,
                          amount_cents=_dollars_to_cents(amount), request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except b.BillingError as exc:
        return _panel_redirect(ag["bill_to_type"], ag["bill_to_id"], error=str(exc))
    return _panel_redirect(ag["bill_to_type"], ag["bill_to_id"], notice="Billing schedule created.")


@router.post("/billing/{subject_type}/{subject_id}/invoices")
def create_invoice(subject_type: str, subject_id: int, request: Request,
                   agreement_id: int | None = Form(None),
                   principal: Principal = Depends(require_capability("billing.write"))):
    _valid_subject(subject_type)
    try:
        invoice_id = b.create_draft_invoice(principal, bill_to_type=subject_type, bill_to_id=subject_id,
                                           agreement_id=agreement_id, request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return _invoice_redirect(invoice_id, notice="Draft invoice created — add line items, then issue.")


@router.post("/billing/invoices/{invoice_id}/line-items")
def add_line_item(invoice_id: int, request: Request, description: str = Form(...), amount: str = Form(...),
                  quantity: int = Form(1), kind: str = Form("fee"),
                  principal: Principal = Depends(require_capability("billing.write"))):
    _subject_of_invoice(invoice_id)
    try:
        b.add_line_item(principal, invoice_id, description=description,
                        unit_amount_cents=_dollars_to_cents(amount), quantity=quantity, kind=kind,
                        request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except b.BillingError as exc:
        return _invoice_redirect(invoice_id, error=str(exc))
    return _invoice_redirect(invoice_id, notice="Line item added.")


@router.post("/billing/invoices/{invoice_id}/line-items/{line_id}/remove")
def remove_line_item(invoice_id: int, line_id: int, request: Request,
                     principal: Principal = Depends(require_capability("billing.write"))):
    _subject_of_invoice(invoice_id)
    try:
        b.remove_line_item(principal, line_id, request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except b.BillingError as exc:
        return _invoice_redirect(invoice_id, error=str(exc))
    return _invoice_redirect(invoice_id, notice="Line item removed.")


@router.post("/billing/invoices/{invoice_id}/issue")
def issue_invoice(invoice_id: int, request: Request, due_date: str | None = Form(None),
                  principal: Principal = Depends(require_capability("billing.write"))):
    _subject_of_invoice(invoice_id)
    from datetime import date as _date
    due = _date.fromisoformat(due_date) if (due_date or "").strip() else None
    try:
        b.issue_invoice(principal, invoice_id, due_date=due, request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except b.BillingError as exc:
        return _invoice_redirect(invoice_id, error=str(exc))
    return _invoice_redirect(invoice_id, notice="Invoice issued.")


@router.post("/billing/invoices/{invoice_id}/void")
def void_invoice(invoice_id: int, request: Request,
                 principal: Principal = Depends(require_capability("billing.write"))):
    _subject_of_invoice(invoice_id)
    try:
        b.void_invoice(principal, invoice_id, request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return _invoice_redirect(invoice_id, notice="Invoice voided.")


@router.post("/billing/invoices/{invoice_id}/payments")
def record_payment(invoice_id: int, request: Request, amount: str = Form(...), method: str = Form("manual"),
                   external_ref: str | None = Form(None),
                   principal: Principal = Depends(require_capability("billing.write"))):
    _subject_of_invoice(invoice_id)
    try:
        b.record_payment(principal, invoice_id, amount_cents=_dollars_to_cents(amount), method=method,
                         external_ref=(external_ref or None), request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except b.BillingError as exc:
        return _invoice_redirect(invoice_id, error=str(exc))
    return _invoice_redirect(invoice_id, notice="Payment recorded.")


@router.post("/billing/{subject_type}/{subject_id}/generate")
def generate_invoices(subject_type: str, subject_id: int, request: Request,
                      principal: Principal = Depends(require_capability("billing.write"))):
    _valid_subject(subject_type)
    try:
        created = b.generate_due_invoices(principal, subject_type, subject_id, request_id=_rid(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return _panel_redirect(subject_type, subject_id,
                           notice=f"Generated {len(created)} invoice(s) from due schedules.")


# --- client (portal; feature-gated by the middleware) -----------------------

@router.get("/portal/billing", response_class=HTMLResponse)
def portal_billing(request: Request, principal: PortalPrincipal = Depends(current_portal)):
    return templates.TemplateResponse(request=request, name="portal/billing.html", context={
        "principal": principal, "money": k.money, "invoices": b.client_invoices(principal),
        "agreements": b.client_agreements(principal),
        "payments": _client_payment_history(principal),
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


def _client_payment_history(principal):
    """Client payment history. The Core ``billing`` feature is enforced here as well as by the
    middleware, and every row is projected — ``payment_history`` returns raw ``payments`` rows
    including ``external_ref`` (the processor reference), ``metadata`` and ``recorded_by_user_id``."""
    from app.services.features.service import client_can
    if not client_can(principal, "billing"):
        return []
    out = []
    for t, i in b.client_billing_subjects(principal):
        out.extend(b._client_payment_view(p) for p in b.payment_history(t, i))
    out.sort(key=lambda p: p["received_on"] or __import__("datetime").date.min, reverse=True)
    return out[:50]


@router.get("/portal/billing/invoices/{invoice_id}", response_class=HTMLResponse)
def portal_invoice(invoice_id: int, request: Request,
                   principal: PortalPrincipal = Depends(current_portal)):
    detail = b.client_invoice_detail(principal, invoice_id)
    if detail is None:
        raise HTTPException(404, "Invoice not found")           # out-of-scope / draft never disclosed
    return templates.TemplateResponse(request=request, name="portal/billing_invoice.html", context={
        "principal": principal, "invoice": detail, "money": k.money})
