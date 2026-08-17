"""Client document upload UI — browser surface over the EXISTING Vault↔Portal upload backend.

Exercises the real /portal routes (called directly with a live ``PortalPrincipal``): the upload form
renders, Post/Redirect/Get creates a PENDING vault document via the same ``portal_vault.upload_document``
service (bound to the client's OWN person, awaiting employee approval), the documents page lists it as
awaiting review, and validation failures redirect back with a friendly banner (never a stack trace).
"""
from __future__ import annotations

import asyncio
import io

from sqlalchemy import select
from starlette.datastructures import UploadFile

from app.db import engine, vault_document_links, vault_documents
from app.portal import vault_documents as pv
from app.routes.portal import (
    portal_documents_page,
    portal_upload_page,
    portal_upload_submit,
)
from tests._portal_util import (
    fake_request,
    render,
    sample_upload,
    seed_portal_account,
    seed_staff_user,
)


def _upload(principal, *, display_name, category="general", request_id=None,
            filename="w2.pdf", body=None):
    if body is None:
        body = sample_upload(filename.rsplit(".", 1)[-1])
    upload = UploadFile(io.BytesIO(body), filename=filename)
    return asyncio.run(portal_upload_submit(
        request=fake_request("/portal/upload", "POST"), file=upload,
        display_name=display_name, category=category, request_id=request_id, principal=principal))


def test_upload_page_renders_a_multipart_form():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_upload_page(fake_request("/portal/upload"), principal))
    assert 'action="/portal/upload"' in html
    assert 'enctype="multipart/form-data"' in html
    assert 'type="file"' in html


def test_upload_prg_creates_pending_vault_document_on_own_person():
    _, principal, pid, _ = seed_portal_account(seed_staff_user())
    resp = _upload(principal, display_name="My W-2", category="tax")
    assert resp.status_code == 303                                   # Post/Redirect/Get
    assert resp.headers["location"].startswith("/portal/documents?notice=")

    mine = [d for d in pv.portal_documents(principal) if d["display_name"] == "My W-2"]
    assert mine and mine[0]["pending_approval"] is True
    with engine.connect() as c:
        linked = c.scalar(select(vault_document_links.c.person_id).where(
            vault_document_links.c.document_id == mine[0]["id"]))
        status = c.scalar(select(vault_documents.c.status).where(
            vault_documents.c.id == mine[0]["id"]))
    assert linked == pid            # bound to the client's OWN person, not an arbitrary entity
    assert status == "uploaded"     # pending employee approval, not yet official


def test_documents_page_lists_the_pending_upload():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    pv.upload_document(principal, source=io.BytesIO(sample_upload("pdf")), original_filename="f.pdf",
                       display_name="Statement 2025", category="general")
    html = render(portal_documents_page(fake_request("/portal/documents"), principal))
    assert "Statement 2025" in html
    assert "Awaiting review" in html


def test_missing_name_redirects_back_with_banner_no_stacktrace():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    resp = _upload(principal, display_name="   ")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portal/upload?error=Please+name+the+document"


def test_documents_page_empty_state_for_new_client():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_documents_page(fake_request("/portal/documents"), principal))
    assert "No documents yet" in html
