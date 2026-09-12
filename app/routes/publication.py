"""Document publication API — staff endpoints under ``/api/publications``.

Publishing does not create a document. It creates a GRANT: a row saying that an existing canonical
``documents`` row may be read by a named client audience, who decided that, and when. No file is
uploaded here, no bytes are copied, and there is no endpoint on this router that accepts a file —
that is the point of the whole feature, so it is worth stating as an absence.

Authority is ``vault.manage`` for every decision and ``vault.view`` for every read. No new capability
is introduced: "may this person decide what a client sees" is authority the firm already models, and
splitting it in two would create a second answer to the same question. Record scope is checked
against the AUDIENCE inside the service, so the capability alone does not let anyone publish to a
client outside their own book.

The preview endpoint is READ-ONLY and returns counts. It proposes bands; it publishes nothing, and
there is deliberately no "apply the preview" endpoint — accepting a proposal is a per-document
decision that goes through the publish endpoint like any other.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.publication import preview as publication_preview
from app.services.publication import service as publication

router = APIRouter(prefix="/api", tags=["document-publication"])


def _ip(request: Request):
    return request.client.host if request.client else None


def _handle(exc):
    if isinstance(exc, publication.PublicationNotFound):
        raise HTTPException(404, str(exc))
    if isinstance(exc, publication.PublicationPermissionError):
        raise HTTPException(403, str(exc))
    if isinstance(exc, publication.PublicationUnavailable):
        raise HTTPException(503, str(exc))
    if isinstance(exc, (publication.PublicationError, ValueError)):
        raise HTTPException(400, str(exc))
    raise exc


class PublishRequest(BaseModel):
    document_id: int
    audience_type: str              # person | household | organization
    audience_id: int
    client_visible: bool = False
    decision_source: str = "staff_manual"
    note: str | None = None


class RevokeRequest(BaseModel):
    note: str | None = None


@router.post("/publications")
def create_publication(request: Request, body: PublishRequest,
                       principal: Principal = Depends(require_capability("vault.manage"))):
    """Publish an existing canonical document to one audience.

    Takes a document id, never a file. Re-publishing to an audience that already holds a live
    publication updates that decision rather than creating a second row — the partial unique index
    in ``docpub01`` makes that the only representable outcome.
    """
    try:
        publication_id = publication.publish(
            principal, body.document_id, audience_type=body.audience_type,
            audience_id=body.audience_id, client_visible=body.client_visible,
            decision_source=body.decision_source, note=body.note,
            actor_user_id=principal.user_id, ip_address=_ip(request))
    except Exception as exc:  # noqa: BLE001 — mapped to HTTP above
        _handle(exc)
    return JSONResponse({"id": publication_id}, status_code=201)


@router.post("/publications/{publication_id}/revoke")
def revoke_publication(request: Request, publication_id: int, body: RevokeRequest | None = None,
                       principal: Principal = Depends(require_capability("vault.manage"))):
    """Withdraw a publication. The row survives as evidence that the document WAS published."""
    try:
        publication.revoke(principal, publication_id, actor_user_id=principal.user_id,
                           ip_address=_ip(request), note=(body.note if body else None))
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    return JSONResponse({"id": publication_id, "status": "revoked"})


@router.post("/publications/{publication_id}/archive")
def archive_publication(request: Request, publication_id: int,
                        principal: Principal = Depends(require_capability("vault.manage"))):
    """Retire a publication. Housekeeping, distinct from the withdrawal decision that revoke records."""
    try:
        publication.archive(principal, publication_id, actor_user_id=principal.user_id,
                            ip_address=_ip(request))
    except Exception as exc:  # noqa: BLE001
        _handle(exc)
    return JSONResponse({"id": publication_id, "status": "archived"})


@router.get("/documents/{document_id}/publications")
def document_publications(document_id: int,
                          principal: Principal = Depends(require_capability("vault.view"))):
    """Who can see this document, and the decision ledger that says how it got that way.

    Returns live AND withdrawn publications: "nobody can see this now, and here is when that
    changed" is the answer an audit is usually looking for.
    """
    return JSONResponse({
        "publications": [_json_publication(p) for p in
                         publication.publications_for_document(document_id)],
        "events": [_json_event(e) for e in publication.events_for_document(document_id)],
    })


@router.get("/publications/preview")
def corpus_publication_preview(source_systems: str | None = None,
                               principal: Principal = Depends(require_capability("vault.view"))):
    """Read-only band counts for the corpus. Publishes nothing.

    ``source_systems`` is a comma-separated override of the default Drake + TaxDome scope. It selects
    WHICH documents are counted; it can never move one between bands, because source system is not an
    input to the policy.
    """
    from app.db import engine
    systems = tuple(s.strip() for s in source_systems.split(",")) if source_systems \
        else publication_preview.DEFAULT_SOURCE_SYSTEMS
    with engine.connect() as conn:
        return JSONResponse(publication_preview.corpus_preview(conn, source_systems=systems))


# --- JSON serialization (never exposes storage paths) ------------------------

def _iso(value):
    return value.isoformat() if value else None


def _json_publication(p):
    return {
        "id": p["id"], "document_id": p["document_id"], "audience_type": p["audience_type"],
        "person_id": p["person_id"], "household_id": p["household_id"],
        "organization_id": p["organization_id"], "client_visible": p["client_visible"],
        "decision_source": p["decision_source"], "document_type": p["document_type"],
        "tax_year": p["tax_year"], "note": p["note"],
        "created_by_user_id": p["created_by_user_id"],
        "created_at": _iso(p["created_at"]), "updated_at": _iso(p["updated_at"]),
        "revoked_at": _iso(p["revoked_at"]), "revoked_by_user_id": p["revoked_by_user_id"],
        "archived_at": _iso(p["archived_at"]),
        "live": p["revoked_at"] is None and p["archived_at"] is None,
    }


def _json_event(e):
    return {"action": e["action"], "publication_id": e["publication_id"],
            "audience_type": e["audience_type"], "audience_id": e["audience_id"],
            "client_visible": e["client_visible"], "user_id": e["actor_user_id"],
            "timestamp": _iso(e["occurred_at"]), "ip_address": e["ip_address"],
            "metadata": e["metadata_json"]}
