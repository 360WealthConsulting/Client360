from fastapi import APIRouter, HTTPException

from app.jobs.microsoft_calendar_sync import sync_calendar_events


router = APIRouter(prefix="/microsoft365")


@router.post("/calendar/sync")
def sync_microsoft365_calendar():
    try:
        return sync_calendar_events()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
