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
from app.services.microsoft_documents import get_person_microsoft_documents
from app.services.timeline import add_timeline_event
from app.templating import render_error

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
            "microsoft_documents": get_person_microsoft_documents(person_id),
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

    document_id = save_person_document(
        person_id=person_id,
        original_name=file.filename,
        source=file.file,
        content_type=file.content_type,
        category=category,
        description=description,
        uploaded_by=uploaded_by,
    )

    await file.close()

    add_timeline_event(
        person_id=person_id,
        source="client360",
        event_type="document_uploaded",
        title="Document Uploaded",
        summary=file.filename,
        external_id=f"document-uploaded-{document_id}",
        event_metadata={
            "document_id": document_id,
            "category": category,
            "description": description or None,
            "uploaded_by": uploaded_by or None,
            "content_type": file.content_type,
        },
    )

    return RedirectResponse(
        url=f"/people/{person_id}/documents?uploaded=1",
        status_code=303,
    )


def _is_inline_viewable(content_type: str | None, name: str | None) -> bool:
    """Types safe to render inline in the browser (PDF / image / plain text). Everything else
    downloads. Falls back to the filename extension when no content_type is recorded."""
    ct = (content_type or "").lower()
    if ct == "application/pdf" or ct.startswith("image/") or ct == "text/plain":
        return True
    ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    return ext in {"pdf", "png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "bmp", "txt",
                   "heic", "heif"}


@router.get("/documents/{document_id}/download")
def download_document(document_id: int, request: Request, inline: bool = False):
    document = get_document(document_id)

    if document is None or document["archived"]:
        return render_error(request, 404,
                            detail="This document is no longer available. It may have been archived.")

    # Serve the absolute storage_uri whenever the document carries one — this covers every durable
    # canonical copy (TaxDome-synced "Client360 Local" AND relocated "Client360 Repository" documents,
    # whose storage_path is stored relative to the content root). Directly-uploaded legacy documents have
    # no absolute storage_uri and continue to resolve via their repo-relative storage_path.
    if document["storage_uri"] and Path(document["storage_uri"]).is_absolute():
        path = Path(document["storage_uri"])
    else:
        path = Path(document["storage_path"])

    if not path.exists():
        return render_error(request, 404,
                            detail="The stored copy of this document could not be found on the server.")

    # ?inline=1 renders viewable types (PDF/image/text) in the browser so an operator can inspect a
    # document without downloading it. Authorization is unchanged (enforced by the middleware on this
    # path). Non-viewable types always download.
    disposition = "inline" if (inline and _is_inline_viewable(
        document["content_type"], document["original_name"])) else "attachment"
    return FileResponse(
        path=path,
        media_type=document["content_type"] or "application/octet-stream",
        filename=document["original_name"],
        content_disposition_type=disposition,
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
