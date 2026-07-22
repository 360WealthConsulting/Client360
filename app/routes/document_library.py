"""Document library routes (Phase D.16). The document-management platform surface.

New ``/document-library`` prefix (the legacy ``/documents`` routes + ``document.read/write`` are
preserved). Outside the middleware RULES, so each endpoint enforces its ``documents.*`` capability
in-route; the service enforces record scope. Sensitive documents are gated by scope + capability.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.document_platform import relationships, service, versions
from app.templating import install_filters

router = APIRouter(prefix="/document-library", tags=["documents"])
templates = Jinja2Templates(directory="app/templates")
install_filters(templates)


async def _form(request):
    return parse_qs((await request.body()).decode("utf-8"))


def _one(form, key):
    return (form.get(key, [""])[0]).strip()


def _int(form, key):
    v = _one(form, key)
    return int(v) if v else None


@router.get("", response_class=HTMLResponse)
def library(request: Request, classification: str | None = None, status: str | None = None,
            q: str | None = None, page: int = 1,
            principal: Principal = Depends(require_capability("documents.view"))):
    result = service.list_documents(principal, classification=classification, status=status,
                                    search=q, page=page)
    return templates.TemplateResponse(request=request, name="document_library/library.html", context={
        "principal": principal, "result": result,
        "filters": {"classification": classification or "", "status": status or "", "q": q or ""},
        "folders": service.list_folders(), "can_edit": principal.can("documents.edit")})


@router.get("/folders")
def folders(request: Request, principal: Principal = Depends(require_capability("documents.view"))):
    return JSONResponse({"folders": service.list_folders()})


@router.get("/export")
def export(request: Request, principal: Principal = Depends(require_capability("documents.export"))):
    result = service.list_documents(principal, page_size=200)
    cols = ["id", "original_name", "classification", "status", "storage_provider", "current_version"]
    return JSONResponse({"columns": cols,
                         "rows": [{k: r.get(k) for k in cols} for r in result["rows"]]})


@router.get("/{document_id}", response_class=HTMLResponse)
def detail(request: Request, document_id: int,
           principal: Principal = Depends(require_capability("documents.view"))):
    doc = service.get_document(principal, document_id)
    if doc is None:
        raise HTTPException(404, "Not found")
    return templates.TemplateResponse(request=request, name="document_library/detail.html", context={
        "principal": principal, "d": doc,
        "can_edit": principal.can("documents.edit"),
        "can_approve": principal.can("documents.approve"),
        "can_archive": principal.can("documents.archive"),
        "can_version": principal.can("documents.version"),
        "can_restore": principal.can("documents.restore"),
        "can_delete": principal.can("documents.delete")})


@router.post("")
async def create(request: Request, principal: Principal = Depends(require_capability("documents.edit"))):
    form = await _form(request)
    try:
        doc = service.create_document(
            principal, original_name=_one(form, "original_name"), actor_user_id=principal.user_id,
            person_id=_int(form, "person_id"), household_id=_int(form, "household_id"),
            organization_id=_int(form, "organization_id"),
            classification=_one(form, "classification") or None,
            storage_provider=_one(form, "storage_provider") or "local",
            storage_uri=_one(form, "storage_uri") or None, content_type=_one(form, "content_type") or None)
    except service.DocumentError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/document-library/{doc['id']}", status_code=303)


@router.post("/folders")
async def create_folder(request: Request,
                        principal: Principal = Depends(require_capability("documents.edit"))):
    form = await _form(request)
    try:
        service.create_folder(principal, code=_one(form, "code"), name=_one(form, "name"),
                              actor_user_id=principal.user_id,
                              parent_folder_id=_int(form, "parent_folder_id"),
                              classification=_one(form, "classification") or None)
    except service.DocumentError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/document-library", status_code=303)


@router.post("/retention")
async def manage_retention(request: Request,
                           principal: Principal = Depends(require_capability("documents.manage_retention"))):
    form = await _form(request)
    try:
        service.create_retention_policy(principal, code=_one(form, "code"), name=_one(form, "name"),
                                        actor_user_id=principal.user_id,
                                        retention_years=_int(form, "retention_years"),
                                        action_on_expiry=_one(form, "action_on_expiry") or "review")
    except service.DocumentError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url="/document-library", status_code=303)


@router.post("/{document_id}")
async def update(request: Request, document_id: int,
                 principal: Principal = Depends(require_capability("documents.edit"))):
    form = await _form(request)
    fields = {k: _one(form, k) for k in ("classification", "subcategory", "notes", "description")
              if _one(form, k)}
    if _int(form, "folder_id"):
        fields["folder_id"] = _int(form, "folder_id")
    try:
        service.update_document(principal, document_id, actor_user_id=principal.user_id, fields=fields)
    except service.DocumentNotFound as exc:
        raise HTTPException(404, "Not found") from exc
    except service.DocumentError as exc:
        return RedirectResponse(url=f"/document-library/{document_id}?error={exc}", status_code=303)
    return RedirectResponse(url=f"/document-library/{document_id}", status_code=303)


def _lifecycle_route(action, cap):
    async def handler(request: Request, document_id: int,
                      principal: Principal = Depends(require_capability(cap))):
        form = await _form(request)
        note = _one(form, "note") or None
        try:
            if action == "status":
                service.set_status(principal, document_id, new_status=_one(form, "status"),
                                   actor_user_id=principal.user_id, note=note)
            elif action == "approve":
                service.approve(principal, document_id, actor_user_id=principal.user_id, note=note)
            elif action == "archive":
                service.archive(principal, document_id, actor_user_id=principal.user_id, note=note)
            elif action == "delete":
                service.soft_delete(principal, document_id, actor_user_id=principal.user_id)
            elif action == "restore":
                service.restore(principal, document_id, actor_user_id=principal.user_id)
        except service.DocumentNotFound as exc:
            raise HTTPException(404, "Not found") from exc
        except service.DocumentError as exc:
            return RedirectResponse(url=f"/document-library/{document_id}?error={exc}", status_code=303)
        return RedirectResponse(url=f"/document-library/{document_id}", status_code=303)
    return handler


router.add_api_route("/{document_id}/status", _lifecycle_route("status", "documents.edit"), methods=["POST"])
router.add_api_route("/{document_id}/approve", _lifecycle_route("approve", "documents.approve"), methods=["POST"])
router.add_api_route("/{document_id}/archive", _lifecycle_route("archive", "documents.archive"), methods=["POST"])
router.add_api_route("/{document_id}/delete", _lifecycle_route("delete", "documents.delete"), methods=["POST"])
router.add_api_route("/{document_id}/restore", _lifecycle_route("restore", "documents.restore"), methods=["POST"])


@router.post("/{document_id}/versions")
async def create_version(request: Request, document_id: int,
                         principal: Principal = Depends(require_capability("documents.version"))):
    form = await _form(request)
    try:
        versions.create_version(principal, document_id, actor_user_id=principal.user_id,
                                bump=_one(form, "bump") or "minor", notes=_one(form, "notes") or None,
                                storage_uri=_one(form, "storage_uri") or None)
    except versions.VersionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/document-library/{document_id}", status_code=303)


@router.post("/{document_id}/versions/{version_id}/restore")
async def restore_version(request: Request, document_id: int, version_id: int,
                          principal: Principal = Depends(require_capability("documents.restore"))):
    try:
        versions.restore_version(principal, document_id, version_id, actor_user_id=principal.user_id)
    except versions.VersionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/document-library/{document_id}", status_code=303)


@router.post("/{document_id}/relationships")
async def link_relationship(request: Request, document_id: int,
                            principal: Principal = Depends(require_capability("documents.edit"))):
    form = await _form(request)
    try:
        relationships.link_entity(principal, document_id, entity_type=_one(form, "entity_type"),
                                  entity_id=_int(form, "entity_id"), actor_user_id=principal.user_id,
                                  relationship_type=_one(form, "relationship_type") or None)
    except relationships.RelationshipError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(url=f"/document-library/{document_id}", status_code=303)
