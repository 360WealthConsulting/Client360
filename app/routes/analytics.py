"""Enterprise Analytics routes (Phase D.15).

Read-model surfaces. ``/analytics`` is outside the middleware RULES, so every endpoint enforces
its capability in-route (analytics.view / executive / export / manage_targets / manage_dashboards).
Executive/firm-wide metrics are withheld server-side without analytics.executive.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.analytics import dashboards, intelligence, metrics, service, targets, trends
from app.templating import install_filters

router = APIRouter(prefix="/analytics", tags=["analytics"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


@router.get("", response_class=HTMLResponse)
def overview(request: Request, principal: Principal = Depends(require_capability("analytics.view"))):
    default_code = "executive_summary" if principal.can("analytics.executive") else "advisor"
    dashboard = dashboards.compose_predefined(principal, default_code)
    return templates.TemplateResponse(request=request, name="analytics/overview.html", context={
        "principal": principal, "dashboard": dashboard,
        "dashboards": dashboards.list_predefined(principal) + dashboards.list_custom(principal),
        "intel": intelligence.firm_intelligence(principal),
        "can_export": principal.can("analytics.export")})


@router.get("/export")
def export(request: Request, keys: str | None = None,
           principal: Principal = Depends(require_capability("analytics.export"))):
    metric_keys = [k for k in (keys or "").split(",") if k] or None
    return JSONResponse(service.export_metrics(principal, metric_keys))


@router.get("/dashboards/{code}", response_class=HTMLResponse)
def dashboard(request: Request, code: str,
              principal: Principal = Depends(require_capability("analytics.view"))):
    try:
        if code in dashboards.PREDEFINED:
            d = dashboards.compose_predefined(principal, code)
        else:
            d = dashboards.compose_custom(principal, code)
    except dashboards.DashboardError as exc:
        raise HTTPException(404, str(exc)) from exc
    return templates.TemplateResponse(request=request, name="analytics/dashboard.html", context={
        "principal": principal, "d": d})


@router.get("/metrics/{metric_key}", response_class=HTMLResponse)
def metric_detail(request: Request, metric_key: str,
                  principal: Principal = Depends(require_capability("analytics.view"))):
    metric = metrics.compute_metric(principal, metric_key)
    if metric.get("error"):
        raise HTTPException(404, "Unknown metric")
    trend = trends.metric_trend(metric_key)
    return templates.TemplateResponse(request=request, name="analytics/metric.html", context={
        "principal": principal, "metric": metric, "trend": trend,
        "variance": targets.variance(principal, metric_key)})


@router.post("/targets")
async def manage_target(request: Request,
                        principal: Principal = Depends(require_capability("analytics.manage_targets"))):
    form = await _form(request)

    def _num(k):
        v = _one(form, k)
        return float(v) if v else None

    try:
        targets.set_target(principal, metric_key=_one(form, "metric_key"),
                           actor_user_id=principal.user_id,
                           dimension_key=_one(form, "dimension_key") or None,
                           period=_one(form, "period") or "all", target_value=_num("target_value"),
                           threshold_warning=_num("threshold_warning"),
                           threshold_critical=_num("threshold_critical"),
                           direction=_one(form, "direction") or "higher_is_better")
    except targets.TargetError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/analytics", status_code=303)


@router.post("/dashboards")
async def create_dashboard(request: Request,
                           principal: Principal = Depends(require_capability("analytics.manage_dashboards"))):
    form = await _form(request)
    try:
        d = dashboards.create_dashboard(principal, code=_one(form, "code"), name=_one(form, "name"),
                                        actor_user_id=principal.user_id,
                                        description=_one(form, "description") or None,
                                        executive_only=(_one(form, "executive_only") == "1"))
    except dashboards.DashboardError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/analytics/dashboards/{d['code']}", status_code=303)


@router.post("/snapshots")
async def capture_snapshot(request: Request,
                           principal: Principal = Depends(require_capability("analytics.manage_dashboards"))):
    form = await _form(request)
    metric_key = _one(form, "metric_key")
    try:
        if metric_key:
            service.capture_snapshot(principal, metric_key=metric_key, actor_user_id=principal.user_id,
                                     period_key=_one(form, "period_key") or None)
        else:
            service.capture_all(principal, actor_user_id=principal.user_id,
                                period_key=_one(form, "period_key") or None)
    except service.SnapshotError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/analytics", status_code=303)
