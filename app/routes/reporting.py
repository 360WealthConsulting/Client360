"""Enterprise Reporting routes (Phase D.21) — dashboards, reports, scorecards, BI.

New ``/reporting`` prefix. It matches no middleware RULE, so each endpoint enforces its
``reporting.*`` capability in-route; KPI values are composed from Analytics (which enforces the
principal's book scope and executive gating automatically). Report runs enforce record scope on
their optional client anchor. Sensitive audit history is gated by ``reporting.audit``; template /
export-profile management by ``reporting.templates``.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.reporting import common, render, schedules
from app.services.reporting import service as svc
from app.services.reporting import templates as tmpl
from app.templating import install_filters

router = APIRouter(prefix="/reporting", tags=["reporting"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


def _keys(form, key):
    return [v.strip() for v in form.get(key, []) if v.strip()] or \
           [k.strip() for k in _one(form, key).split(",") if k.strip()]


# --- overview + reads --------------------------------------------------------

@router.get("", response_class=HTMLResponse)
def overview(request: Request, category: str | None = None,
             principal: Principal = Depends(require_capability("reporting.view"))):
    return templates.TemplateResponse(request=request, name="reporting/overview.html", context={
        "principal": principal, "dashboards": svc.list_dashboards(principal, category=category),
        "metrics": svc.overview_metrics(principal), "scorecards": tmpl.list_scorecards(active_only=True),
        "predefined": render.predefined_scorecards(principal),
        "filters": {"category": category or ""},
        "can_manage": principal.can("reporting.manage"),
        "can_templates": principal.can("reporting.templates")})


@router.get("/dashboards")
def list_dashboards(request: Request, category: str | None = None,
                    principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"dashboards": [
        {"id": d["id"], "code": d["code"], "name": d["name"], "category": d["category"],
         "status": d["status"], "executive_only": d["executive_only"]}
        for d in svc.list_dashboards(principal, category=category)]})


@router.get("/dashboards/{dashboard_id}", response_class=HTMLResponse)
def dashboard_detail(request: Request, dashboard_id: int,
                     principal: Principal = Depends(require_capability("reporting.view"))):
    rendered = svc.render_dashboard(principal, dashboard_id)
    if rendered is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="reporting/dashboard.html", context={
        "principal": principal, "d": rendered["dashboard"], "widgets": rendered["widgets"],
        "can_manage": principal.can("reporting.manage")})


@router.get("/dashboards/{dashboard_id}/audit")
def dashboard_audit(request: Request, dashboard_id: int,
                    principal: Principal = Depends(require_capability("reporting.audit"))):
    if svc.get_dashboard(principal, dashboard_id) is None:
        raise HTTPException(404, "Not found")
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "occurred_at": e["occurred_at"].isoformat()}
        for e in common.audit_history(principal, entity_type="dashboard", entity_id=dashboard_id)]})


@router.get("/definitions")
def list_definitions(request: Request, report_type: str | None = None,
                     principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"definitions": [
        {"id": d["id"], "name": d["name"], "report_type": d["report_type"], "category": d["category"]}
        for d in svc.list_definitions(principal, report_type=report_type)]})


@router.get("/definitions/{definition_id}")
def compose_definition(request: Request, definition_id: int,
                       principal: Principal = Depends(require_capability("reporting.view"))):
    composed = svc.compose_definition(principal, definition_id)
    if composed is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(composed)


@router.get("/scorecards")
def list_scorecards(request: Request,
                    principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"scorecards": [
        {"code": s["code"], "name": s["name"], "category": s["category"],
         "executive_only": s["executive_only"]} for s in tmpl.list_scorecards(active_only=True)]})


@router.get("/scorecards/{code}")
def render_scorecard(request: Request, code: str,
                     principal: Principal = Depends(require_capability("reporting.view"))):
    result = render.render_scorecard_by_code(principal, code)
    if result is None:
        raise HTTPException(404, "Not found")
    return JSONResponse(result)


@router.get("/kpi-groups")
def list_kpi_groups(request: Request,
                    principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"kpi_groups": [
        {"code": g["code"], "name": g["name"], "metric_keys": g["metric_keys"]}
        for g in tmpl.list_kpi_groups()]})


@router.get("/templates")
def list_templates(request: Request,
                   principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"templates": [
        {"code": t["code"], "name": t["name"], "category": t["category"],
         "report_type": t["report_type"]} for t in tmpl.list_templates()]})


@router.get("/export-profiles")
def list_export_profiles(request: Request,
                         principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"export_profiles": [
        {"code": p["code"], "name": p["name"], "export_format": p["export_format"],
         "delivery": p["delivery"]} for p in tmpl.list_export_profiles()]})


@router.get("/schedules")
def list_schedules(request: Request,
                   principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"schedules": [
        {"id": s["id"], "name": s["name"], "frequency": s["frequency"], "active": s["active"]}
        for s in schedules.list_schedules()]})


@router.get("/saved-views")
def list_saved_views(request: Request,
                     principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"saved_views": [
        {"id": v["id"], "name": v["name"], "target_type": v["target_type"], "shared": v["shared"]}
        for v in tmpl.list_saved_views(principal)]})


@router.get("/reports")
def list_reports(request: Request, status: str | None = None, page: int = 1,
                 principal: Principal = Depends(require_capability("reporting.view"))):
    result = svc.list_reports(principal, status=status, page=page)
    return JSONResponse({"total": result["total"], "page": result["page"], "reports": [
        {"id": r["id"], "name": r["name"], "status": r["status"], "report_type": r["report_type"]}
        for r in result["rows"]]})


@router.get("/reports/{report_id}/audit")
def report_audit(request: Request, report_id: int,
                 principal: Principal = Depends(require_capability("reporting.audit"))):
    try:
        history = svc.report_audit(principal, report_id)
    except common.ReportingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    return JSONResponse({"history": [
        {"event_type": e["event_type"], "occurred_at": e["occurred_at"].isoformat()}
        for e in history]})


@router.get("/export")
def export(request: Request, metrics: str | None = None,
           principal: Principal = Depends(require_capability("reporting.view"))):
    keys = [k.strip() for k in metrics.split(",")] if metrics else None
    return JSONResponse(render.export_values(principal, keys))


@router.get("/metrics")
def available_metrics(request: Request,
                      principal: Principal = Depends(require_capability("reporting.view"))):
    return JSONResponse({"metrics": render.available_metrics(principal)})


# --- dashboards (manage) -----------------------------------------------------

@router.post("/dashboards")
async def create_dashboard(request: Request,
                           principal: Principal = Depends(require_capability("reporting.manage"))):
    form = await _form(request)
    try:
        d = svc.create_dashboard(principal, code=_one(form, "code"), name=_one(form, "name"),
                                 category=_one(form, "category") or "general",
                                 description=_one(form, "description") or None,
                                 executive_only=(_one(form, "executive_only") == "1"),
                                 actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/reporting/dashboards/{d['id']}", status_code=303)


@router.post("/dashboards/{dashboard_id}/status")
async def dashboard_status(request: Request, dashboard_id: int,
                           principal: Principal = Depends(require_capability("reporting.manage"))):
    form = await _form(request)
    try:
        svc.set_dashboard_status(principal, dashboard_id, _one(form, "status"),
                                 actor_user_id=principal.user_id)
    except common.ReportingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/reporting/dashboards/{dashboard_id}", status_code=303)


@router.post("/dashboards/{dashboard_id}/widgets")
async def add_widget(request: Request, dashboard_id: int,
                     principal: Principal = Depends(require_capability("reporting.manage"))):
    form = await _form(request)
    try:
        svc.add_widget(principal, dashboard_id, title=_one(form, "title"),
                       widget_type=_one(form, "widget_type") or "metric",
                       metric_key=_one(form, "metric_key") or None,
                       kpi_group_id=_int(form, "kpi_group_id"),
                       viz_type=_one(form, "viz_type") or "card",
                       sort_order=_int(form, "sort_order") or 0, actor_user_id=principal.user_id)
    except common.ReportingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/reporting/dashboards/{dashboard_id}", status_code=303)


# --- definitions + reports (manage) ------------------------------------------

@router.post("/definitions")
async def create_definition(request: Request,
                            principal: Principal = Depends(require_capability("reporting.manage"))):
    form = await _form(request)
    keys = _keys(form, "metric_keys")
    try:
        d = svc.create_definition(principal, name=_one(form, "name"),
                                  report_type=_one(form, "report_type") or "dashboard",
                                  category=_one(form, "category") or "general",
                                  definition={"metric_keys": keys} if keys else None,
                                  executive_only=(_one(form, "executive_only") == "1"),
                                  actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": d["id"], "name": d["name"]}, status_code=201)


@router.post("/reports")
async def create_report(request: Request,
                        principal: Principal = Depends(require_capability("reporting.manage"))):
    form = await _form(request)
    try:
        r = svc.create_report(principal, name=_one(form, "name"),
                              report_definition_id=_int(form, "report_definition_id"),
                              dashboard_id=_int(form, "dashboard_id"),
                              report_type=_one(form, "report_type") or "dashboard",
                              category=_one(form, "category") or "general",
                              person_id=_int(form, "person_id"),
                              household_id=_int(form, "household_id"),
                              export_profile_id=_int(form, "export_profile_id"),
                              actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": r["id"], "status": r["status"]}, status_code=201)


@router.post("/reports/{report_id}/generate")
async def generate_report(request: Request, report_id: int,
                          principal: Principal = Depends(require_capability("reporting.manage"))):
    form = await _form(request)
    try:
        r = svc.generate_report(principal, report_id,
                                capture_snapshots=(_one(form, "capture_snapshots") == "1"),
                                actor_user_id=principal.user_id)
    except common.ReportingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": r["id"], "status": r["status"]})


# --- templates / scorecards / kpi-groups / export-profiles / schedules -------

@router.post("/templates")
async def create_template(request: Request,
                          principal: Principal = Depends(require_capability("reporting.templates"))):
    form = await _form(request)
    try:
        tmpl.create_template(code=_one(form, "code"), name=_one(form, "name"),
                             category=_one(form, "category") or "general",
                             report_type=_one(form, "report_type") or "dashboard",
                             actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/reporting", status_code=303)


@router.post("/scorecards")
async def create_scorecard(request: Request,
                           principal: Principal = Depends(require_capability("reporting.templates"))):
    form = await _form(request)
    try:
        tmpl.create_scorecard(code=_one(form, "code"), name=_one(form, "name"),
                              metric_keys=_keys(form, "metric_keys"),
                              category=_one(form, "category") or "general",
                              executive_only=(_one(form, "executive_only") == "1"),
                              actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/reporting", status_code=303)


@router.post("/kpi-groups")
async def create_kpi_group(request: Request,
                           principal: Principal = Depends(require_capability("reporting.templates"))):
    form = await _form(request)
    try:
        tmpl.create_kpi_group(code=_one(form, "code"), name=_one(form, "name"),
                              metric_keys=_keys(form, "metric_keys"),
                              actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/reporting", status_code=303)


@router.post("/export-profiles")
async def create_export_profile(request: Request,
                                principal: Principal = Depends(require_capability("reporting.templates"))):
    form = await _form(request)
    try:
        tmpl.create_export_profile(code=_one(form, "code"), name=_one(form, "name"),
                                   export_format=_one(form, "export_format") or "pdf",
                                   delivery=_one(form, "delivery") or "download",
                                   actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/reporting", status_code=303)


@router.post("/schedules")
async def create_schedule(request: Request,
                          principal: Principal = Depends(require_capability("reporting.manage"))):
    form = await _form(request)
    try:
        s = schedules.create_schedule(principal, name=_one(form, "name"),
                                      frequency=_one(form, "frequency") or "manual",
                                      report_definition_id=_int(form, "report_definition_id"),
                                      dashboard_id=_int(form, "dashboard_id"),
                                      export_profile_id=_int(form, "export_profile_id"),
                                      conversation_id=_int(form, "conversation_id"),
                                      actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": s["id"], "name": s["name"]}, status_code=201)


@router.post("/schedules/{schedule_id}/run")
async def run_schedule(request: Request, schedule_id: int,
                       principal: Principal = Depends(require_capability("reporting.manage"))):
    try:
        r = schedules.run_schedule(principal, schedule_id, actor_user_id=principal.user_id)
    except common.ReportingNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"report_id": r["id"], "status": r["status"]})


# --- saved views (owner-scoped) ----------------------------------------------

@router.post("/saved-views")
async def create_saved_view(request: Request,
                            principal: Principal = Depends(require_capability("reporting.view"))):
    form = await _form(request)
    try:
        v = tmpl.create_saved_view(principal, name=_one(form, "name"),
                                   target_type=_one(form, "target_type") or "dashboard",
                                   target_id=_int(form, "target_id"),
                                   shared=(_one(form, "shared") == "1"),
                                   actor_user_id=principal.user_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"id": v["id"], "name": v["name"]}, status_code=201)


@router.post("/saved-views/{view_id}/delete")
async def delete_saved_view(request: Request, view_id: int,
                            principal: Principal = Depends(require_capability("reporting.view"))):
    try:
        ok = tmpl.delete_saved_view(principal, view_id)
    except common.ReportingError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "Not found")
    return RedirectResponse(url="/reporting", status_code=303)
