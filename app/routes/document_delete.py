"""Remove an obsolete document from a person, household or business workspace.

Thin routing over the EXISTING canonical soft delete
(``document_platform.service.soft_delete``). No second deletion system, no retention policy, no
trash UI, no bulk delete, and nothing here touches the filesystem, SharePoint/OneDrive, OCR, naming
or the relationship graph.

**Delete, not archive, and deliberately so.** Both canonical transitions exist, but the workspace
listings (``documents_for_entity`` and ``business_workspace``) exclude only ``status != 'deleted'``
— nothing filters ``status == 'archived'``. Archiving would therefore leave the document sitting in
the very list staff are trying to clear, so the established transition that actually removes it from
normal view is the soft delete. It is reversible through the existing ``service.restore``.

Soft: the row stays, ``status`` becomes ``deleted`` and ``deleted_at`` is stamped. ``original_name``,
``display_name``, ``stored_name``, ``storage_path``, ``storage_uri``, ``sha256`` and the ownership
columns are all left exactly as they were, and the stored file is never touched.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.db import documents, engine
from app.security.authorization import organization_in_scope, record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_platform import service as doc_service
from app.templating import render_error

router = APIRouter(tags=["documents"])

_OWNER_COLUMN = {"person": documents.c.person_id, "household": documents.c.household_id,
                 "organization": documents.c.organization_id}
_WORKSPACE_URL = {
    "person": lambda owner_id: f"/client/{owner_id}?tab=documents",
    "household": lambda owner_id: f"/client/household/{owner_id}?tab=documents",
    "organization": lambda owner_id: f"/business/{owner_id}",
}


def _in_scope(principal, owner_type, owner_id) -> bool:
    """WRITE record scope on the owning record — stricter than the canonical service's read-scope
    visibility check, which is deliberately not enough to authorise a removal."""
    if owner_type == "organization":
        return organization_in_scope(principal, owner_id, write=True)
    return record_in_scope(principal, owner_type, owner_id, write=True)


def _owns(owner_type, owner_id, document_id) -> bool:
    """The document must actually belong to the workspace it is being deleted from, so a person
    route can never remove a household's or a business's document."""
    with engine.connect() as conn:
        return conn.scalar(select(documents.c.id).where(
            documents.c.id == document_id,
            _OWNER_COLUMN[owner_type] == owner_id,
            documents.c.status != "deleted")) is not None


def _delete(request, principal, owner_type, owner_id, document_id, confirm):
    # Out of scope, wrong owner and non-existent all answer identically: existence is never disclosed.
    if not _in_scope(principal, owner_type, owner_id) or not _owns(owner_type, owner_id, document_id):
        return render_error(request, 404, detail="Not found.")
    if (confirm or "").strip().lower() != "yes":
        return render_error(request, 400, detail="Removal requires the confirmation value.")
    try:
        doc_service.soft_delete(principal, document_id, actor_user_id=principal.user_id)
    except doc_service.DocumentNotFound:
        return render_error(request, 404, detail="Not found.")
    except doc_service.DocumentError as exc:
        return render_error(request, 400, detail=str(exc))
    return RedirectResponse(_WORKSPACE_URL[owner_type](owner_id), status_code=303)


@router.post("/client/{person_id}/documents/{document_id}/delete")
def delete_person_document(
        request: Request, person_id: int, document_id: int, confirm: str = Form(""),
        principal: Principal = Depends(require_capability("documents.delete"))):
    return _delete(request, principal, "person", person_id, document_id, confirm)


@router.post("/client/household/{household_id}/documents/{document_id}/delete")
def delete_household_document(
        request: Request, household_id: int, document_id: int, confirm: str = Form(""),
        principal: Principal = Depends(require_capability("documents.delete"))):
    return _delete(request, principal, "household", household_id, document_id, confirm)


@router.post("/business/{organization_id}/documents/{document_id}/delete")
def delete_business_document(
        request: Request, organization_id: int, document_id: int, confirm: str = Form(""),
        principal: Principal = Depends(require_capability("documents.delete"))):
    return _delete(request, principal, "organization", organization_id, document_id, confirm)
