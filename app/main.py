import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import SESSION_HTTPS_ONLY, SESSION_SECRET, validate_startup_configuration
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.observability import configure_logging
from app.routes.activities import router as activities_router
from app.routes.activity_dashboard import router as activity_dashboard_router
from app.routes.activity_timeline import router as activity_timeline_router
from app.routes.admin import router as admin_router
from app.routes.advisor_work import router as advisor_work_router
from app.routes.analytics import router as analytics_router
from app.routes.annual_review import router as annual_review_router
from app.routes.auth import router as auth_router
from app.routes.automation import router as automation_router
from app.routes.benefits import router as benefits_router
from app.routes.business_development import router as business_development_router
from app.routes.business_owner import router as business_owner_router
from app.routes.campaign import router as campaign_router
from app.routes.communications import router as communications_router
from app.routes.compliance import router as compliance_router
from app.routes.configuration import router as configuration_router
from app.routes.dashboard import router as dashboard_router
from app.routes.dev_auth import dev_auth_enabled
from app.routes.dev_auth import router as dev_auth_router
from app.routes.document_library import router as document_library_router
from app.routes.documents import router as documents_router
from app.routes.exceptions import router as exceptions_router
from app.routes.governance import router as governance_router
from app.routes.households import router as households_router
from app.routes.identity_review import router as identity_review_router
from app.routes.insurance import router as insurance_router
from app.routes.integration import router as integration_router
from app.routes.matches import router as matches_router
from app.routes.microsoft365 import router as microsoft365_router
from app.routes.microsoft365_calendar import (
    router as microsoft365_calendar_router,
)
from app.routes.microsoft365_documents import (
    router as microsoft365_documents_router,
)
from app.routes.microsoft365_inbox_review import router as microsoft365_inbox_review_router
from app.routes.microsoft365_mail import router as microsoft365_mail_router
from app.routes.microsoft365_oauth import router as microsoft365_oauth_router
from app.routes.notes import router as notes_router
from app.routes.observability import router as observability_router
from app.routes.operations import router as operations_router
from app.routes.opportunity import router as opportunity_router
from app.routes.ops import router as ops_router
from app.routes.people import router as people_router
from app.routes.person_edit import router as person_edit_router
from app.routes.portal import router as portal_router
from app.routes.portal_admin import router as portal_admin_router
from app.routes.engagement import router as engagement_router
from app.routes.portfolio import router as portfolio_router
from app.routes.referral import router as referral_router
from app.routes.relationships import router as relationships_router
from app.routes.reporting import router as reporting_router
from app.routes.runtime import router as runtime_router
from app.routes.runtime_cluster import router as runtime_cluster_router
from app.routes.runtime_behavior import router as runtime_behavior_router
from app.routes.policy import router as policy_router
from app.routes.orchestration import router as orchestration_router
from app.routes.events import router as events_router
from app.routes.projections import router as projections_router
from app.routes.scheduling import router as scheduling_router
from app.routes.search import router as search_router
from app.routes.security import router as security_router
from app.routes.session import router as session_router
from app.routes.source import router as source_router
from app.routes.task_dashboard import router as task_dashboard_router
from app.routes.tasks import router as tasks_router
from app.routes.tax import router as tax_router
from app.routes.tax_documents import router as tax_documents_router
from app.routes.tax_intake import router as tax_intake_router
from app.routes.tax_returns import router as tax_returns_router
from app.routes.timeline import router as timeline_router
from app.routes.wealth import router as wealth_router
from app.routes.work import router as work_router
from app.routes.workflow_automation import router as workflow_automation_router
from app.routes.workflows import router as workflows_router
from app.routes.workspace import router as workspace_router
from app.routes.client360 import router as client360_router
from app.routes.ai_assist import router as ai_assist_router
from app.security.middleware import AuthenticationMiddleware
from app.services.runtime.middleware import RuntimeContextMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    validate_startup_configuration()
    start_scheduler()

    # (D.28) Hydrate the Runtime Configuration Engine — GUARDED so a configuration failure can never
    # prevent safe application startup (the engine falls back to defaults / the last-known snapshot).
    try:
        from app.services.runtime import engine as runtime_engine
        runtime_engine.hydrate()
    except Exception:
        logging.getLogger("client360.runtime").exception(
            "runtime config hydration failed at startup; continuing with defaults")

    # (D.29) Join the runtime cluster — register this worker + converge onto the current generation.
    # GUARDED so a coordination failure can never prevent safe application startup.
    try:
        from app.services.runtime import cluster as runtime_cluster
        runtime_cluster.initialize_cluster()
    except Exception:
        logging.getLogger("client360.runtime.coordination").exception(
            "runtime cluster join failed at startup; continuing standalone")

    # (D.43) Register the deterministic local portal identity provider for local/test activation ONLY —
    # a no-op once the portal is production-signed-off. GUARDED so it can never block startup.
    try:
        from app.portal.identity_local import register_local_provider_if_permitted
        register_local_provider_if_permitted()
    except Exception:
        logging.getLogger("client360.portal").exception(
            "portal local identity provider registration skipped")

    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(
    title="Client360",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
# (D.28) RuntimeContextMiddleware registered BEFORE AuthenticationMiddleware so it runs INNER (after
# auth) in the request path — request.state.principal is available when it resolves the context.
app.add_middleware(RuntimeContextMiddleware)
app.add_middleware(AuthenticationMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=SESSION_HTTPS_ONLY,
    same_site="lax",
    max_age=8 * 60 * 60,
)

app.include_router(dashboard_router)
app.include_router(compliance_router)
app.include_router(advisor_work_router)
app.include_router(annual_review_router)
app.include_router(business_owner_router)
app.include_router(opportunity_router)
app.include_router(campaign_router)
app.include_router(referral_router)
app.include_router(business_development_router)
app.include_router(analytics_router)
app.include_router(document_library_router)
app.include_router(workflow_automation_router)
app.include_router(communications_router)
app.include_router(scheduling_router)
app.include_router(operations_router)
app.include_router(reporting_router)
app.include_router(automation_router)
app.include_router(governance_router)
app.include_router(integration_router)
app.include_router(security_router)
app.include_router(observability_router)
app.include_router(configuration_router)
app.include_router(runtime_router)
app.include_router(runtime_cluster_router)
app.include_router(runtime_behavior_router)
app.include_router(policy_router)
app.include_router(orchestration_router)
app.include_router(events_router)
app.include_router(projections_router)
app.include_router(activity_timeline_router)
app.include_router(ops_router)
app.include_router(exceptions_router)
app.include_router(benefits_router)
app.include_router(insurance_router)
app.include_router(search_router)
app.include_router(source_router)
app.include_router(matches_router)
app.include_router(identity_review_router)
app.include_router(people_router)
app.include_router(person_edit_router)
app.include_router(notes_router)
app.include_router(tasks_router)
app.include_router(task_dashboard_router)
app.include_router(activities_router)
app.include_router(activity_dashboard_router)
app.include_router(households_router)

app.include_router(documents_router)
app.include_router(microsoft365_router)
app.include_router(microsoft365_calendar_router)
app.include_router(microsoft365_documents_router)
app.include_router(microsoft365_oauth_router)
app.include_router(microsoft365_inbox_review_router)
app.include_router(microsoft365_mail_router)
app.include_router(timeline_router)
app.include_router(relationships_router)
app.include_router(portfolio_router)
app.include_router(wealth_router)
app.include_router(workspace_router)
app.include_router(client360_router)
app.include_router(ai_assist_router)
app.include_router(auth_router)
# Development-only sign-in provider. dev_auth_enabled() is False in production (and
# whenever CLIENT360_DEV_AUTH is unset), so this router is simply never mounted there.
if dev_auth_enabled():
    app.include_router(dev_auth_router)
app.include_router(admin_router)
app.include_router(session_router)
app.include_router(work_router)
app.include_router(workflows_router)
app.include_router(tax_router)
app.include_router(tax_intake_router)
app.include_router(tax_returns_router)
app.include_router(tax_documents_router)
app.include_router(portal_router)
app.include_router(portal_admin_router)
app.include_router(engagement_router)


# --- Styled error pages for browser navigations (JSON preserved for API/tests) ---
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402

from app.templating import render_error as _render_error  # noqa: E402
from app.templating import wants_html as _wants_html  # noqa: E402


@app.exception_handler(_StarletteHTTPException)
async def _http_exception_handler(request, exc):
    if exc.status_code in (403, 404) and _wants_html(request):
        return _render_error(request, exc.status_code, detail=exc.detail)
    return _JSONResponse(
        {"detail": exc.detail},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    # Browsers get a styled 500; API clients get JSON. Starlette re-raises after
    # this in test mode, so raise_server_exceptions behavior is unchanged.
    if _wants_html(request):
        return _render_error(request, 500)
    request_id = getattr(request.state, "request_id", None)
    body = {"detail": "Internal server error"}
    if request_id:
        body["request_id"] = request_id
    return _JSONResponse(body, status_code=500)
