"""Client Vault API — authenticated employee endpoints under /api/vault.

Every route is Entra-authenticated (the app auth middleware) and capability-gated via the existing
``require_capability`` dependency. Per-document category + record-scope authorization and the audit
trail live in ``app.services.vault.service`` — each view, download, upload, version, metadata edit,
and archive writes a ``vault_document_audit_events`` row.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.vault import service as vault
from app.services.vault.storage import VaultStorageError

router = APIRouter(prefix="/api/vault", tags=["vault"])


def _ip(request: Request):
    return request.client.host if request.client else None


def _handle(exc):
    if isinstance(exc, vault.VaultNotFound):
        raise HTTPException(404, str(exc))
    if isinstance(exc, vault.VaultPermissionError):
        raise HTTPException(403, str(exc))
    if isinstance(exc, (VaultStorageError, ValueError)):
        raise HTTPException(400, str(exc))
    raise exc


class VaultPatch(BaseModel):
    display_name: str | None = None
    category: str | None = None
    document_type: str | None = None
    security_classification: str | None = None
    status: str | None = None
    client_visible: bool | None = None


@router.post("/documents")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    display_name: str = Form(...),
    category: str = Form(...),
    document_type: str | None = Form(None),
    security_classification: str = Form("internal"),
    status: str = Form("uploaded"),
    person_id: int | None = Form(None),
    household_id: int | None = Form(None),
    organization_id: int | None = Form(None),
    engagement_id: int | None = Form(None),
    work_item_id: int | None = Form(None),
    principal: Principal = Depends(require_capability("vault.upload")),
):
    try:
        document_id = vault.create_document(
            principal, source=file.file, original_filename=file.filename,
            display_name=display_name, category=category, document_type=document_type,
            security_classification=security_classification, status=status,
            mime_type=file.content_type, actor_user_id=principal.user_id, ip_address=_ip(request),
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            engagement_id=engagement_id, work_item_id=work_item_id)
    except Exception as exc:  # noqa: BLE001 — mapped to HTTP below
        _handle(exc)
    return JSONResponse({"id": document_id}, status_code=201)


@router.get("/documents")
def list_documents(
    request: Request,
    person_id: int | None = None,
    household_id: int | None = None,
    category: str | None = None,
    document_type: str | None = None,
    status: str | None = None,
    year: int | None = None,
    q: str | None = None,
    principal: Principal = Depends(require_capability("vault.view")),
):
    rows = vault.list_documents(
        principal, person_id=person_id, household_id=household_id, category=category,
        document_type=document_type, status=status, year=year, query=q)
    return JSONResponse({"documents": [_json_doc(d) for d in rows]})


@router.get("/documents/{document_id}")
def get_document(request: Request, document_id: int,
                 principal: Principal = Depends(require_capability("vault.view"))):
    try:
        detail = vault.get_document(principal, document_id, actor_user_id=principal.user_id,
                                    ip_address=_ip(request), audit_action="view")
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    return JSONResponse({
        "document": _json_doc(detail["document"]),
        "versions": [_json_version(v) for v in detail["versions"]],
        "links": [_json_link(lk) for lk in detail["links"]],
        "audit": [_json_audit(a) for a in detail["audit"]],
    })


@router.get("/documents/{document_id}/download")
def download_document(request: Request, document_id: int,
                      principal: Principal = Depends(require_capability("vault.download"))):
    try:
        path, filename, mime_type = vault.download_target(
            principal, document_id, actor_user_id=principal.user_id, ip_address=_ip(request))
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    if not path.exists():
        raise HTTPException(404, "Stored file is unavailable.")
    return FileResponse(path=str(path), media_type=mime_type or "application/octet-stream",
                        filename=filename)


@router.post("/documents/{document_id}/versions")
async def upload_version(request: Request, document_id: int, file: UploadFile = File(...),
                         principal: Principal = Depends(require_capability("vault.upload"))):
    try:
        version = vault.add_version(principal, document_id, source=file.file,
                                    original_filename=file.filename, actor_user_id=principal.user_id,
                                    ip_address=_ip(request))
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    return JSONResponse({"id": document_id, "version_number": version}, status_code=201)


@router.patch("/documents/{document_id}")
def patch_document(request: Request, document_id: int, body: VaultPatch,
                   principal: Principal = Depends(require_capability("vault.manage"))):
    try:
        changed = vault.update_metadata(principal, document_id, changes=body.model_dump(),
                                        actor_user_id=principal.user_id, ip_address=_ip(request))
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    return JSONResponse({"id": document_id, "changed": sorted(changed)})


@router.post("/documents/{document_id}/archive")
def archive_document(request: Request, document_id: int,
                     principal: Principal = Depends(require_capability("vault.manage"))):
    try:
        vault.archive_document(principal, document_id, actor_user_id=principal.user_id,
                               ip_address=_ip(request))
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    return JSONResponse({"id": document_id, "status": "archived"})


@router.get("/documents/{document_id}/audit")
def document_audit(request: Request, document_id: int,
                   principal: Principal = Depends(require_capability("vault.view"))):
    try:
        rows = vault.get_audit(principal, document_id)
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    return JSONResponse({"audit": [_json_audit(a) for a in rows]})


# --- JSON serialization (never exposes storage paths) ------------------------

def _iso(value):
    return value.isoformat() if value else None


def _json_doc(d):
    return {
        "id": d["id"], "display_name": d["display_name"], "original_filename": d["original_filename"],
        "document_type": d["document_type"], "category": d["category"],
        "security_classification": d["security_classification"], "status": d["status"],
        "mime_type": d["mime_type"], "file_size": d["file_size"], "version": d["current_version"],
        "checksum_sha256": d["checksum_sha256"], "uploaded_by_user_id": d["uploaded_by_user_id"],
        "client_visible": d.get("client_visible", False),
        "uploaded_by_client": d.get("uploaded_by_portal_account_id") is not None,
        "created_at": _iso(d["created_at"]), "updated_at": _iso(d["updated_at"]),
        "archived_at": _iso(d["archived_at"]),
    }


def _json_version(v):
    return {"version_number": v["version_number"], "file_size": v["file_size"],
            "checksum_sha256": v["checksum_sha256"], "uploaded_by_user_id": v["uploaded_by_user_id"],
            "created_at": _iso(v["created_at"])}


def _json_link(lk):
    return {"person_id": lk["person_id"], "household_id": lk["household_id"],
            "organization_id": lk["organization_id"], "engagement_id": lk["engagement_id"],
            "work_item_id": lk["work_item_id"]}


def _json_audit(a):
    return {"action": a["action"], "user_id": a["user_id"], "timestamp": _iso(a["occurred_at"]),
            "ip_address": a["ip_address"], "metadata": a["metadata_json"]}
