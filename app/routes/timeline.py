from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.timeline import (
    add_timeline_event,
    get_person_timeline,
)


router = APIRouter(prefix="/timeline")


@router.post("/test")
def create_test_timeline_event():
    event_id = add_timeline_event(
        person_id=1,
        source="system",
        event_type="test",
        title="Timeline Engine Online",
        summary="First timeline event created successfully.",
        external_id="timeline-engine-test-person-1",
    )

    return {
        "status": "created",
        "event_id": event_id,
    }


@router.get("/person/{person_id}")
def person_timeline(person_id: int):
    events = get_person_timeline(person_id)

    return JSONResponse(
        content=[
            {
                "id": event["id"],
                "source": event["source"],
                "event_type": event["event_type"],
                "title": event["title"],
                "summary": event["summary"],
                "event_time": (
                    event["event_time"].isoformat()
                    if event["event_time"]
                    else None
                ),
                "external_id": event["external_id"],
                "event_metadata": event["event_metadata"],
            }
            for event in events
        ]
    )
