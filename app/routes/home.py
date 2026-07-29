"""Staff Home dashboard — the first useful screen after an employee signs in.

`GET /home` renders the role-aware landing; `GET /api/home/summary` returns the same data as JSON.
Both are gated by ``current_principal`` only (any authenticated staff may land here — a portal session
has no staff principal, so it is redirected to /auth/login by the auth middleware). Every panel
self-suppresses on capability + record scope inside ``home_summary`` — unauthorized users receive
empty panels, never fetched-then-hidden restricted data.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import current_principal
from app.security.models import Principal
from app.services.home import home_summary

router = APIRouter(tags=["home"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/home", response_class=HTMLResponse)
def staff_home(request: Request, principal: Principal = Depends(current_principal)):
    return templates.TemplateResponse(request=request, name="home/index.html", context={
        "principal": principal, "home": home_summary(principal)})


@router.get("/api/home/summary")
def api_home_summary(principal: Principal = Depends(current_principal)):
    """The authenticated user's authorized dashboard data (counts + panels), JSON-safe."""
    return JSONResponse(_json(home_summary(principal)))


def _json(value):
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
