"""Staff-facing Access & Features surface + administrator-only firm feature controls.

Two capability-gated surfaces over the Client Feature & Access Control framework:

  * Per-client (``/client/access/{subject_type}/{subject_id}``) — status, product entitlements, and
    feature overrides for one household / organization / person. Gated on ``client.read``/``client.write``
    PLUS record scope (a staff member may only manage subjects they can already service), exactly like
    the existing client-portal admin surface. Lives under the existing client-management experience.

  * Firm-wide (``/admin/feature-controls``) — the administrator control for global feature states
    (enabled / disabled / beta / internal_only). Gated on ``configuration.view``/``configuration.admin``.

All mutations delegate to the audited service. UI never enforces access — it only reflects the
server-side decision.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.authorization import organization_in_scope, record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.features import catalog
from app.services.features import service as feat

router = APIRouter(tags=["client-access"])
templates = Jinja2Templates(directory="app/templates")


def _valid_subject(subject_type: str):
    if subject_type not in catalog.SUBJECT_TYPES:
        raise HTTPException(404, "Unknown subject type")


def _in_scope(principal: Principal, subject_type: str, subject_id: int, *, write: bool) -> bool:
    """Record-scope gate that dispatches by subject kind (person/household vs organization)."""
    if subject_type == "organization":
        return organization_in_scope(principal, subject_id, write=write)
    return record_in_scope(principal, subject_type, subject_id, write=write)


def _rid(request: Request):
    return getattr(request.state, "request_id", "client-access")


def _panel(request: Request, principal, subject_type, subject_id):
    status = feat.get_status(subject_type, subject_id)
    return templates.TemplateResponse(request=request, name="client360/access.html", context={
        "principal": principal, "subject_type": subject_type, "subject_id": subject_id,
        "status": status, "entitlements": sorted(feat.entitlements(subject_type, subject_id)),
        "features": feat.feature_report(subject_type, subject_id, actor="client"),
        "access_summary": feat.subject_access_summary(subject_type, subject_id),
        "products": catalog.PRODUCTS, "statuses": catalog.CLIENT_STATUSES,
        "dispositions": catalog.CLIENT_DISPOSITIONS, "override_states": catalog.OVERRIDE_STATES,
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


# --- per-client staff surface ------------------------------------------------

@router.get("/client/access/{subject_type}/{subject_id}", response_class=HTMLResponse)
def access_panel(subject_type: str, subject_id: int, request: Request,
                 principal: Principal = Depends(require_capability("client.read"))):
    _valid_subject(subject_type)
    if not _in_scope(principal, subject_type, subject_id, write=False):
        raise HTTPException(403, f"{subject_type} is outside your record scope")
    return _panel(request, principal, subject_type, subject_id)


def _guard_write(principal, subject_type, subject_id):
    _valid_subject(subject_type)
    if not _in_scope(principal, subject_type, subject_id, write=True):
        raise HTTPException(403, f"{subject_type} is outside your record scope")


def _back(subject_type, subject_id, *, notice=None, error=None):
    q = ("?notice=" + quote(notice)) if notice else (("?error=" + quote(error)) if error else "")
    return RedirectResponse(f"/client/access/{subject_type}/{subject_id}{q}", status_code=303)


@router.post("/client/access/{subject_type}/{subject_id}/status")
def set_client_status(subject_type: str, subject_id: int, request: Request,
                      status: str = Form(...), disposition: str | None = Form(None),
                      principal: Principal = Depends(require_capability("client.write"))):
    _guard_write(principal, subject_type, subject_id)
    try:
        feat.set_status(subject_type, subject_id, status, disposition or None,
                        actor_user_id=principal.user_id, request_id=_rid(request))
    except ValueError as exc:
        return _back(subject_type, subject_id, error=str(exc))
    return _back(subject_type, subject_id, notice="Client status updated.")


@router.post("/client/access/{subject_type}/{subject_id}/entitlement")
def set_client_entitlement(subject_type: str, subject_id: int, request: Request,
                           product: str = Form(...), action: str = Form(...),
                           principal: Principal = Depends(require_capability("client.write"))):
    _guard_write(principal, subject_type, subject_id)
    try:
        if action == "grant":
            feat.grant_entitlement(subject_type, subject_id, product,
                                   actor_user_id=principal.user_id, request_id=_rid(request))
        elif action == "revoke":
            feat.revoke_entitlement(subject_type, subject_id, product,
                                    actor_user_id=principal.user_id, request_id=_rid(request))
        else:
            return _back(subject_type, subject_id, error="Unknown action")
    except ValueError as exc:
        return _back(subject_type, subject_id, error=str(exc))
    return _back(subject_type, subject_id, notice=f"{product.title()} entitlement {action}ed.")


@router.post("/client/access/{subject_type}/{subject_id}/feature")
def set_client_feature_override(subject_type: str, subject_id: int, request: Request,
                               feature_key: str = Form(...), state: str = Form(...),
                               principal: Principal = Depends(require_capability("client.write"))):
    _guard_write(principal, subject_type, subject_id)
    try:
        feat.set_override(subject_type, subject_id, feature_key, state,
                          actor_user_id=principal.user_id, request_id=_rid(request))
    except ValueError as exc:
        return _back(subject_type, subject_id, error=str(exc))
    return _back(subject_type, subject_id, notice=f"{feature_key} set to {state}.")


# --- firm-wide administrator surface -----------------------------------------

@router.get("/admin/feature-controls", response_class=HTMLResponse)
def feature_controls(request: Request,
                     principal: Principal = Depends(require_capability("configuration.view"))):
    rows = []
    for f in catalog.FEATURES.values():
        rows.append({"feature": f.key, "label": f.label, "product": f.product,
                     "state": feat.firm_state(f.key), "default": f.default_firm_state})
    return templates.TemplateResponse(request=request, name="admin/feature_controls.html", context={
        "principal": principal, "features": rows, "states": catalog.FIRM_STATES,
        "notice": request.query_params.get("notice"), "error": request.query_params.get("error")})


@router.post("/admin/feature-controls")
def set_feature_control(request: Request, feature_key: str = Form(...), state: str = Form(...),
                        principal: Principal = Depends(require_capability("configuration.admin"))):
    try:
        feat.set_firm_state(feature_key, state, actor_user_id=principal.user_id, request_id=_rid(request))
    except ValueError as exc:
        return RedirectResponse("/admin/feature-controls?error=" + quote(str(exc)), status_code=303)
    return RedirectResponse("/admin/feature-controls?notice=" + quote(f"{feature_key} → {state}"),
                            status_code=303)
