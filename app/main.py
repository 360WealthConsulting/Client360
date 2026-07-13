from fastapi import FastAPI

from starlette.middleware.sessions import SessionMiddleware

from app.routes.microsoft365 import router as microsoft365_router

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


app = FastAPI(title="Client360")
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
app.include_router(microsoft365_oauth_router)
