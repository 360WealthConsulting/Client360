from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, people
from app.services.documents import (
    archive_document,
    get_document,
    get_person_documents,
    save_person_document,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get(
    "/people/{person_id}/documents",
    response_class=HTMLResponse,
)
def person_documents(request: Request, person_id: int):
    with engine.connect() as connection:
        person = connection.execute(
            select(people).where(people.c.id == person_id)
        ).mappings().one_or_none()

    if person is None:
        return HTMLResponse(
            "<h1>Person not found</h1>",
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="people/documents.html",
        context={
            "person": person,
            "documents": get_person_documents(person_id),
            "uploaded": request.query_params.get("uploaded") == "1",
            "archived": request.query_params.get("archived") == "1",
        },
    )


@router.post("/people/{person_id}/documents")
async def upload_person_document(
    person_id: int,
    file: UploadFile = File(...),
    category: str = Form("other"),
    description: str = Form(""),
    uploaded_by: str = Form(""),
):
    with engine.connect() as connection:
        person_exists = connection.execute(
            select(people.c.id).where(people.c.id == person_id)
        ).scalar_one_or_none()

    if person_exists is None:
        return HTMLResponse(
            "<h1>Person not found</h1>",
            status_code=404,
        )

    if not file.filename:
        return HTMLResponse(
            "<h1>A file is required</h1>",
            status_code=400,
        )

    save_person_document(
        person_id=person_id,
        original_name=file.filename,
        source=file.file,
        content_type=file.content_type,
        category=category,
        description=description,
        uploaded_by=uploaded_by,
    )

    await file.close()

    return RedirectResponse(
        url=f"/people/{person_id}/documents?uploaded=1",
        status_code=303,
    )


@router.get("/documents/{document_id}/download")
def download_document(document_id: int):
    document = get_document(document_id)

    if document is None or document["archived"]:
        return HTMLResponse(
            "<h1>Document not found</h1>",
            status_code=404,
        )

    path = Path(document["storage_path"])

    if not path.exists():
        return HTMLResponse(
            "<h1>Stored file is missing</h1>",
            status_code=404,
        )

    return FileResponse(
        path=path,
        media_type=document["content_type"],
        filename=document["original_name"],
    )


@router.post(
    "/people/{person_id}/documents/{document_id}/archive"
)
def archive_person_document(
    person_id: int,
    document_id: int,
):
    if not archive_document(document_id, person_id):
        return HTMLResponse(
            "<h1>Document not found</h1>",
            status_code=404,
        )

    return RedirectResponse(
        url=f"/people/{person_id}/documents?archived=1",
        status_code=303,
    )
