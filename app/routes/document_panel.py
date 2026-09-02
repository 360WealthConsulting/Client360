"""The Documents-screen preview drawer, as a server-rendered fragment.

`GET /client/{person_id}/documents/{document_id}/panel` (and the household/business twins) return
the drawer's contents for ONE document: preview, filing information, source information and
history. The Documents screen fetches the fragment when a row is selected; with JavaScript off the
same URL is a normal page, so selecting a document still works.

Why a fragment endpoint rather than rendering 291 hidden panels into the table: history and version
data are per-document reads, and pre-rendering them would turn one page load into hundreds of
queries for panels the user will never open.

**Authorization is not re-implemented here.** The document must appear in the client's own ACTIVE
document set (``client_documents``, which is record-scoped upstream and applies the canonical
``lifecycle`` filter). That single check is what makes the route safe, and it is why a soft-deleted
document 404s from the drawer exactly as it 404s from the table: there is no second code path that
could disagree with the list about which documents exist.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import document_events, document_versions, engine, people
from app.security.authorization import record_in_scope
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services.client360 import documents_screen
from app.services.person_names import person_row_display_name
from app.templating import render_error

router = APIRouter(prefix="/client", tags=["client360"])
templates = Jinja2Templates(directory="app/templates")

#: Content types the drawer can show inline. Anything else offers Download only — the drawer never
#: pretends to render a file the browser cannot display.
_INLINE_PREVIEW = ("application/pdf", "image/", "text/plain")
_INLINE_EXTS = frozenset({"pdf", "png", "jpg", "jpeg", "gif", "webp", "bmp", "txt"})


def _can_preview_inline(row) -> bool:
    mime = (row.get("content_type") or "").lower()
    if any(mime == t or (t.endswith("/") and mime.startswith(t)) for t in _INLINE_PREVIEW):
        return True
    name = row.get("original_name") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in _INLINE_EXTS


def _history(document_id: int):
    """The document's real lifecycle history: ``document_events`` plus ``document_versions``.

    Both tables are real and are written by ``document_platform.service`` on every registration and
    status change. They are EMPTY for documents that arrived through a bulk source sync (TaxDome,
    SharePoint), which never calls that service — so the drawer's empty state says exactly that
    rather than implying nothing ever happened to the document.
    """
    with engine.connect() as c:
        events = [dict(r) for r in c.execute(
            select(document_events).where(document_events.c.document_id == document_id)
            .order_by(document_events.c.occurred_at.desc()).limit(50)).mappings()]
        versions = [dict(r) for r in c.execute(
            select(document_versions).where(document_versions.c.document_id == document_id)
            .order_by(document_versions.c.version_number.desc()).limit(50)).mappings()]
    return {"events": events, "versions": versions, "empty": not events and not versions}


def _member_names(person_ids):
    if not person_ids:
        return {}
    with engine.connect() as c:
        return {r["id"]: person_row_display_name(r) for r in c.execute(
            select(people).where(people.c.id.in_(tuple(person_ids)))).mappings()}


def _panel(request, principal, entity_type, entity_id, document_id, panel_tab):
    from app.services.client360.sections import (
        _attach_classification,
        _attach_ocr,
        _attach_source_refs,
        enrich_documents,
    )
    from app.services.document_platform.relationships import client_document

    if not record_in_scope(principal, entity_type, entity_id):
        return render_error(request, 404, detail="Client not found.")

    # The ONE authorization + existence check: the document must be in this client's ACTIVE set.
    # Out of scope, not this client's, and soft-deleted all answer the same 404 — a deleted
    # document must not be distinguishable from one that never existed.
    raw = client_document(principal, entity_type, entity_id, document_id)
    if raw is None:
        return render_error(request, 404, detail="Document not found.")

    household_id = (entity_id if entity_type == "household"
                    else _household_of_person(entity_id))
    household_name = _household_name(household_id)
    member_ids = _member_ids_for_household(household_id) if household_id else [entity_id]

    # The SAME enrichment chain the Documents tab runs, so the drawer cannot show a different
    # document type, OCR state or source than the row the user clicked.
    enriched = _attach_classification(_attach_ocr(_attach_source_refs(enrich_documents([raw]))))[0]
    doc = documents_screen.shape_row(
        {**raw, **enriched}, member_names=_member_names(member_ids),
        household_name=household_name)

    return templates.TemplateResponse(
        request=request, name="client360/_document_panel.html",
        context={"principal": principal, "d": doc, "raw": raw,
                 "panel_tab": panel_tab if panel_tab in ("preview", "details", "history") else "preview",
                 "can_preview": _can_preview_inline(raw),
                 "history": _history(document_id),
                 "back_url": (f"/client/household/{entity_id}?tab=documents"
                              if entity_type == "household" else
                              f"/client/{entity_id}?tab=documents")})


def _household_of_person(person_id):
    with engine.connect() as c:
        return c.scalar(select(people.c.household_id).where(people.c.id == person_id))


def _household_name(household_id):
    if not household_id:
        return None
    from app.db import households
    with engine.connect() as c:
        return c.scalar(select(households.c.name).where(households.c.id == household_id))


def _member_ids_for_household(household_id):
    with engine.connect() as c:
        return list(c.scalars(select(people.c.id).where(people.c.household_id == household_id)))


# The household route is declared FIRST on purpose: `person_id` below is an int, so a request
# to /client/household/1/documents/... matched against it is a 422 rather than a fall-through.
@router.get("/household/{household_id}/documents/{document_id}/panel")
def household_document_panel(
        request: Request, household_id: int, document_id: int, panel: str = "preview",
        principal: Principal = Depends(require_capability("documents.view"))):
    return _panel(request, principal, "household", household_id, document_id, panel)


@router.get("/{person_id}/documents/{document_id}/panel")
def person_document_panel(
        request: Request, person_id: int, document_id: int, panel: str = "preview",
        principal: Principal = Depends(require_capability("documents.view"))):
    return _panel(request, principal, "person", person_id, document_id, panel)
