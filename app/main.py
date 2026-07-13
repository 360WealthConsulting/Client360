from fastapi import FastAPI

from app.routes.dashboard import router as dashboard_router
from app.routes.matches import router as matches_router
from app.routes.notes import router as notes_router
from app.routes.people import router as people_router
from app.routes.search import router as search_router
from app.routes.source import router as source_router
from app.routes.tasks import router as tasks_router


app = FastAPI(title="Client360")

app.include_router(dashboard_router)
app.include_router(search_router)
app.include_router(source_router)
app.include_router(matches_router)
app.include_router(people_router)
app.include_router(notes_router)
app.include_router(tasks_router)
