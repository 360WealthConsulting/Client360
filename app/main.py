from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from starlette.middleware.sessions import SessionMiddleware

from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.routes.microsoft365 import router as microsoft365_router
from app.routes.microsoft365_calendar import (
    router as microsoft365_calendar_router,
)

from app.routes.documents import router as documents_router

from app.routes.dashboard import router as dashboard_router
from app.routes.matches import router as matches_router
from app.routes.notes import router as notes_router
from app.routes.people import router as people_router
from app.routes.search import router as search_router
from app.routes.source import router as source_router
from app.routes.tasks import router as tasks_router
from app.routes.task_dashboard import router as task_dashboard_router
from app.routes.activities import router as activities_router
from app.routes.activity_dashboard import router as activity_dashboard_router
from app.routes.households import router as households_router
from app.routes.microsoft365_oauth import router as microsoft365_oauth_router
from app.routes.microsoft365_inbox_review import router as microsoft365_inbox_review_router
from app.routes.timeline import router as timeline_router
from app.routes.microsoft365_mail import router as microsoft365_mail_router
from app.routes.portfolio import router as portfolio_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()

    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(
    title="Client360",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key="CHANGE_THIS_TO_A_LONG_RANDOM_SECRET",
)

app.include_router(dashboard_router)
app.include_router(search_router)
app.include_router(source_router)
app.include_router(matches_router)
app.include_router(people_router)
app.include_router(notes_router)
app.include_router(tasks_router)
app.include_router(task_dashboard_router)
app.include_router(activities_router)
app.include_router(activity_dashboard_router)
app.include_router(households_router)

app.include_router(documents_router)
app.include_router(microsoft365_router)
app.include_router(microsoft365_calendar_router)
app.include_router(microsoft365_oauth_router)
app.include_router(microsoft365_inbox_review_router)
app.include_router(microsoft365_mail_router)
app.include_router(timeline_router)
app.include_router(portfolio_router)
