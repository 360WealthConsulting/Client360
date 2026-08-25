"""Upload a document directly from a person, household or business workspace.

Thin routing over the EXISTING canonical uploader (``services.documents.save_workspace_document``).
No second uploader, no second document store, no ingestion redesign, and nothing here touches
SharePoint/OneDrive, OCR, naming or the relationship graph.

The owner comes from the URL, never the form: a staff user who is already looking at a client,
household or business does not re-pick the owner, and a request cannot retarget the upload. Exactly
one owner column is set; the other two stay NULL. Uploads run with ``verify_content=True`` so the
staff path gets the same extension allow-list, size cap and content check as the client-facing vault
upload, rather than the looser trusted-internal setting the legacy person form used.

Authorization is the existing pair: the ``documents.edit`` capability plus WRITE record scope on the
specific person/household/organization being uploaded to.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from app.security.authorization import organization_in_scope, record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.documents import DocumentOwnerNotFound, save_workspace_document
from app.services.vault.storage import VaultStorageError
from app.templating import render_error

router = APIRouter(tags=["documents"])

_WORKSPACE_URL = {
    "person": lambda owner_id: f"/client/{owner_id}?tab=documents",
    "household": lambda owner_id: f"/client/household/{owner_id}?tab=documents",
    "organization": lambda owner_id: f"/business/{owner_id}",
}


def _in_scope(principal, owner_type, owner_id) -> bool:
    """WRITE record scope for the upload target, using the existing checks for each anchor."""
    if owner_type == "organization":
        return organization_in_scope(principal, owner_id, write=True)
    return record_in_scope(principal, owner_type, owner_id, write=True)


async def _upload(request, principal, owner_type, owner_id, file, category):
    """Shared body for the three workspace upload routes."""
    if not _in_scope(principal, owner_type, owner_id):
        # Same response as a missing record: out of scope never discloses existence.
        return render_error(request, 404, detail="Not found.")
    if not file or not (file.filename or "").strip():
        return render_error(request, 400, detail="A file is required.")
    try:
        save_workspace_document(
            owner_type=owner_type, owner_id=owner_id,
            original_name=file.filename, source=file.file,
            content_type=file.content_type, category=(category or "").strip() or None,
            uploaded_by=principal.email or str(principal.user_id), verify_content=True)
    except DocumentOwnerNotFound:
        return render_error(request, 404, detail="Not found.")
    except VaultStorageError as exc:
        return render_error(request, 400, detail=str(exc))
    finally:
        await file.close()
    return RedirectResponse(_WORKSPACE_URL[owner_type](owner_id), status_code=303)


@router.post("/client/{person_id}/documents/upload")
async def upload_person_document(
        request: Request, person_id: int, file: UploadFile = File(...), category: str = Form(""),
        principal: Principal = Depends(require_capability("documents.edit"))):
    return await _upload(request, principal, "person", person_id, file, category)


@router.post("/client/household/{household_id}/documents/upload")
async def upload_household_document(
        request: Request, household_id: int, file: UploadFile = File(...), category: str = Form(""),
        principal: Principal = Depends(require_capability("documents.edit"))):
    return await _upload(request, principal, "household", household_id, file, category)


@router.post("/business/{organization_id}/documents/upload")
async def upload_business_document(
        request: Request, organization_id: int, file: UploadFile = File(...),
        category: str = Form(""),
        principal: Principal = Depends(require_capability("documents.edit"))):
    return await _upload(request, principal, "organization", organization_id, file, category)
