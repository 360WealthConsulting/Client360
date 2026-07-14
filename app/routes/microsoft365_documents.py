from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update

from app.db import engine, microsoft_documents, people
from app.jobs.microsoft_document_sync import (
    drive_item_external_id,
    sync_microsoft_documents,
)
from app.services.timeline import add_timeline_event


router = APIRouter(prefix="/microsoft365")
templates = Jinja2Templates(directory="app/templates")


@router.post("/documents/sync")
def manual_document_sync():
    try:
        return sync_microsoft_documents()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/documents-review", response_class=HTMLResponse)
def documents_review(request: Request):
    with engine.connect() as connection:
        documents = connection.execute(
            select(microsoft_documents)
            .where(
                microsoft_documents.c.status == "pending",
                microsoft_documents.c.deleted.is_(False),
            )
            .order_by(
                microsoft_documents.c.modified_at_microsoft.desc(),
                microsoft_documents.c.id.desc(),
            )
            .limit(100)
        ).mappings().all()
        person_rows = connection.execute(
            select(people.c.id, people.c.full_name, people.c.primary_email)
            .where(people.c.active.is_(True))
            .order_by(people.c.last_name, people.c.first_name, people.c.id)
        ).mappings().all()

    return templates.TemplateResponse(
        request=request,
        name="microsoft365/documents_review.html",
        context={"documents": documents, "people": person_rows},
    )


@router.post("/documents-review/{document_id}/ignore")
def ignore_document(document_id: int):
    with engine.begin() as connection:
        result = connection.execute(
            update(microsoft_documents)
            .where(
                microsoft_documents.c.id == document_id,
                microsoft_documents.c.status == "pending",
            )
            .values(status="ignored")
        )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Document not found.")
    return RedirectResponse(url="/microsoft365/documents-review", status_code=303)


@router.post("/documents-review/{document_id}/match/{person_id}")
def match_document(document_id: int, person_id: int):
    with engine.connect() as connection:
        document = connection.execute(
            select(microsoft_documents).where(
                microsoft_documents.c.id == document_id,
                microsoft_documents.c.status == "pending",
            )
        ).mappings().one_or_none()
        person_exists = connection.execute(
            select(people.c.id).where(people.c.id == person_id)
        ).scalar_one_or_none()

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if person_exists is None:
        raise HTTPException(status_code=404, detail="Person not found.")

    metadata = {
        "microsoft_drive_id": document["microsoft_drive_id"],
        "microsoft_item_id": document["microsoft_item_id"],
        "name": document["name"],
        "mime_type": document["mime_type"],
        "size_bytes": document["size_bytes"],
        "web_url": document["web_url"],
        "parent_path": document["parent_path"],
        "match_method": "manual_review",
    }
    add_timeline_event(
        person_id=person_id,
        source="microsoft",
        event_type="microsoft_document",
        title="Microsoft Document Updated",
        summary=document["name"],
        event_time=document["modified_at_microsoft"],
        external_id=drive_item_external_id(
            document["microsoft_drive_id"], document["microsoft_item_id"]
        ),
        event_metadata=metadata,
    )
    with engine.begin() as connection:
        connection.execute(
            update(microsoft_documents)
            .where(microsoft_documents.c.id == document_id)
            .values(
                person_id=person_id,
                status="matched",
                match_method="manual_review",
            )
        )
    return RedirectResponse(url="/microsoft365/documents-review", status_code=303)
