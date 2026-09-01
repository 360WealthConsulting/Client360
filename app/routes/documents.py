from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import engine, people
from app.services.document_naming import document_delivery_filename
from app.services.documents import (
    archive_document,
    get_document,
    get_person_documents,
    save_person_document,
)
from app.services.microsoft_documents import get_person_microsoft_documents
from app.services.timeline import add_timeline_event
from app.services.workbook_preview import (
    PREVIEW_MAX_COLS,
    PREVIEW_MAX_ROWS,
    read_workbook_preview,
)
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
    # Deliver under the canonical display name when the document has one, else the original filename
    # (document_naming.document_delivery_filename). Label only: the bytes, the path resolved above,
    # the media type and the authorization already enforced on this route are all unchanged.
    return FileResponse(
        path=path,
        media_type=document["content_type"] or "application/octet-stream",
        filename=document_delivery_filename(document),
        content_disposition_type=disposition,
    )


_EXCEL_EXTS = {"xlsx", "xlsm"}          # openpyxl reads these; legacy .xls is not supported here
# Aliases of the preview helper's own bounds — one source of truth, so the route and the helper
# can never drift apart.
_PREVIEW_MAX_ROWS = PREVIEW_MAX_ROWS
_PREVIEW_MAX_COLS = PREVIEW_MAX_COLS
_PREVIEW_MAX_FILE_BYTES = 25 * 1024 * 1024

_HEIF_EXTS = {"heic", "heif"}
_IMAGE_PREVIEW_MAX_PX = 2000
_IMAGE_PREVIEW_MAX_FILE_BYTES = 40 * 1024 * 1024


def convert_image_to_jpeg(path):
    """Read an image (incl. HEIC/HEIF) and return a bounded JPEG (bytes) for browser display, or None if
    conversion is unavailable or fails. READ-ONLY: opens + downscales in memory, NEVER writes, converts,
    or replaces the source file. Returns None on a missing image library, a decompression bomb, or any
    read error, so the caller can fail safely."""
    try:
        import io

        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()   # enable HEIC/HEIF decoding when the plugin is present
        except Exception:                        # noqa: BLE001 — non-HEIF images still work via Pillow
            pass
        with Image.open(path) as im:
            im.load()
            im = im.convert("RGB")
            im.thumbnail((_IMAGE_PREVIEW_MAX_PX, _IMAGE_PREVIEW_MAX_PX))
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85)
            return out.getvalue()
    except Exception:  # noqa: BLE001 — library missing / unreadable / oversized: caller falls back
        return None


@router.get("/documents/{document_id}/preview")
def preview_document(document_id: int, request: Request, sheet: str = ""):
    """Read-only Client360 workbook preview for .xlsx/.xlsm documents, so an admin can inspect a
    spreadsheet in a new tab instead of downloading it. Authorization is unchanged — the middleware
    enforces the same document-scope rules on this ``/documents/{id}`` path (including the admin
    unassigned-document exception). Never modifies the source file, metadata, or ownership."""
    document = get_document(document_id)
    if document is None or document["archived"]:
        return render_error(request, 404,
                            detail="This document is no longer available. It may have been archived.")
    name = document["original_name"] or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    ctx = {"document_id": document_id, "filename": name,
           "download_url": f"/documents/{document_id}/download"}
    if ext not in _EXCEL_EXTS:
        ctx["error"] = "Preview is available for .xlsx/.xlsm workbooks only. Use Download for this file."
        return templates.TemplateResponse(request=request, name="admin/workbook_preview.html",
                                          context={**ctx, "sheetnames": [], "rows": []})

    if document["storage_uri"] and Path(document["storage_uri"]).is_absolute():
        path = Path(document["storage_uri"])
    else:
        path = Path(document["storage_path"])
    if not path.exists():
        return render_error(request, 404,
                            detail="The stored copy of this document could not be found on the server.")
    if path.stat().st_size > _PREVIEW_MAX_FILE_BYTES:
        ctx["error"] = "This workbook is too large to preview safely. Use Download instead."
        return templates.TemplateResponse(request=request, name="admin/workbook_preview.html",
                                          context={**ctx, "sheetnames": [], "rows": []})

    result = read_workbook_preview(
        path,
        sheet=sheet,
        max_rows=_PREVIEW_MAX_ROWS,
        max_cols=_PREVIEW_MAX_COLS,
    )
    return templates.TemplateResponse(request=request, name="admin/workbook_preview.html",
                                      context={**ctx, **result,
                                               "max_rows": _PREVIEW_MAX_ROWS, "max_cols": _PREVIEW_MAX_COLS})


@router.get("/documents/{document_id}/image-preview")
def image_preview_document(document_id: int, request: Request):
    """Read-only Client360 image preview for browser-incompatible images (HEIC/HEIF): serves an
    in-memory JPEG rendition so the image opens in a new tab instead of downloading. Authorization is
    unchanged — the middleware enforces the same document-scope rules on this ``/documents/{id}`` path
    (including the admin unassigned-document exception). Never modifies the source file, metadata, or
    ownership. If conversion cannot be performed, renders a Client360 page explaining that and keeps
    Download available."""
    from fastapi.responses import Response
    document = get_document(document_id)
    if document is None or document["archived"]:
        return render_error(request, 404,
                            detail="This document is no longer available. It may have been archived.")
    if document["storage_uri"] and Path(document["storage_uri"]).is_absolute():
        path = Path(document["storage_uri"])
    else:
        path = Path(document["storage_path"])
    ctx = {"filename": document["original_name"],
           "download_url": f"/documents/{document_id}/download"}
    if not path.exists():
        return render_error(request, 404,
                            detail="The stored copy of this document could not be found on the server.")
    if path.stat().st_size > _IMAGE_PREVIEW_MAX_FILE_BYTES:
        return templates.TemplateResponse(request=request, name="admin/image_preview.html",
            context={**ctx, "error": "This image is too large to preview safely. Use Download instead."})
    jpeg = convert_image_to_jpeg(path)
    if jpeg is None:
        return templates.TemplateResponse(request=request, name="admin/image_preview.html",
            context={**ctx, "error": "This image could not be previewed in the browser. Use Download to "
                                     "save the original file."})
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"content-disposition": "inline"})


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
