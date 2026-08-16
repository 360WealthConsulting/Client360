from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.services.dashboard import can_view_firm_metrics, get_dashboard_data
from app.services.exception_reporting import dashboard_summary

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/health")
def health():
    return {
        "status": "ok",
        "application": "Client360",
    }


@router.get("/api/stats")
def stats(request: Request):
    # Server-side authorization: firm-wide financial aggregates are included ONLY for a principal holding
    # portfolio.firm_metrics; otherwise get_dashboard_data omits them from the JSON entirely.
    principal = getattr(request.state, "principal", None)
    return get_dashboard_data(principal)


@router.get("/")
def advisor_dashboard(request: Request):
    principal = getattr(request.state, "principal", None)
    dashboard = get_dashboard_data(principal)

    return templates.TemplateResponse(
        request=request,
        name="dashboard/index.html",
        context={
            "dashboard": dashboard,
            "show_firm_metrics": can_view_firm_metrics(principal),
            "exception_summary": dashboard_summary(principal, audience="advisor"),
        },
    )
