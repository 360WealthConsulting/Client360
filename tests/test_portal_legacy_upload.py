"""Legacy request-fulfilment upload endpoint POST /api/v1/portal/requests/{id}/upload.

It stores in the `documents` table (its uploaded_document_id / document_versions FKs and
confirm_request_upload are documents-bound), so it cannot route through the vault storage — but it now
applies the SAME client-upload controls via save_person_document(verify_content=True). These adversarial
tests cover forged/oversize/disallowed/mismatch inputs and a genuine successful fulfilment.
"""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.datastructures import UploadFile

from app.db import document_versions, documents, engine, portal_document_requests
from app.portal.service import create_document_request
from app.routes.portal import api_request_upload
from tests._portal_util import sample_upload, seed_portal_account, seed_staff_user


def _call(request_id, principal, *, filename, body):
    upload = UploadFile(io.BytesIO(body), filename=filename)
    return asyncio.run(api_request_upload(request_id, file=upload, principal=principal))


def _open_request(pid, hid, staff_uid, title="Upload W-2"):
    return create_document_request(person_id=pid, household_id=hid, title=title,
                                   requested_by_user_id=staff_uid)


# --- adversarial -------------------------------------------------------------

def test_forged_request_id_is_404():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    with pytest.raises(HTTPException) as ei:
        _call(999_000_777, principal, filename="w2.pdf", body=sample_upload("pdf"))
    assert ei.value.status_code == 404


def test_another_clients_request_is_403():
    staff_uid = seed_staff_user()
    _, alice, _, _ = seed_portal_account(staff_uid)
    _, bob, bob_pid, bob_hid = seed_portal_account(staff_uid)
    bob_req = _open_request(bob_pid, bob_hid, staff_uid)
    with pytest.raises(HTTPException) as ei:
        _call(bob_req, alice, filename="w2.pdf", body=sample_upload("pdf"))
    assert ei.value.status_code == 403
    # Nothing was fulfilled on Bob's request.
    with engine.connect() as c:
        assert c.scalar(select(portal_document_requests.c.status).where(
            portal_document_requests.c.id == bob_req)) == "open"


def test_disallowed_extension_is_400():
    staff_uid = seed_staff_user()
    _, principal, pid, hid = seed_portal_account(staff_uid)
    req = _open_request(pid, hid, staff_uid)
    with pytest.raises(HTTPException) as ei:
        _call(req, principal, filename="evil.exe", body=b"MZ\x90\x00 executable")
    assert ei.value.status_code == 400
    with engine.connect() as c:
        assert c.scalar(select(portal_document_requests.c.status).where(
            portal_document_requests.c.id == req)) == "open"       # not fulfilled


def test_oversized_file_is_400(monkeypatch):
    monkeypatch.setattr("app.services.documents.MAX_UPLOAD_BYTES", 4)   # tiny cap for the test
    staff_uid = seed_staff_user()
    _, principal, pid, hid = seed_portal_account(staff_uid)
    req = _open_request(pid, hid, staff_uid)
    with pytest.raises(HTTPException) as ei:
        _call(req, principal, filename="big.pdf", body=sample_upload("pdf"))  # >4 bytes, valid header
    assert ei.value.status_code == 400


def test_content_extension_mismatch_is_400():
    staff_uid = seed_staff_user()
    _, principal, pid, hid = seed_portal_account(staff_uid)
    req = _open_request(pid, hid, staff_uid)
    with pytest.raises(HTTPException) as ei:
        _call(req, principal, filename="evil.pdf", body=b"<html><script>x()</script></html>")
    assert ei.value.status_code == 400
    with engine.connect() as c:
        assert c.scalar(select(portal_document_requests.c.status).where(
            portal_document_requests.c.id == req)) == "open"


# --- successful fulfilment (semantics preserved) -----------------------------

def test_successful_request_fulfilment():
    staff_uid = seed_staff_user()
    _, principal, pid, hid = seed_portal_account(staff_uid)
    req = _open_request(pid, hid, staff_uid)
    result = _call(req, principal, filename="w2.pdf", body=sample_upload("pdf"))
    assert result["status"] == "uploaded" and result["version"] == 1
    doc_id = result["document_id"]
    with engine.connect() as c:
        # Stored in the documents table with a real hash + size (SHA-256 handling preserved).
        doc = c.execute(select(documents.c.person_id, documents.c.sha256, documents.c.size_bytes,
                               documents.c.category).where(documents.c.id == doc_id)).mappings().one()
        assert doc["person_id"] == pid and len(doc["sha256"]) == 64 and doc["size_bytes"] > 0
        assert doc["category"] == "portal_request"
        # Request confirmation semantics preserved: status + uploaded_document_id + a version row.
        reqrow = c.execute(select(portal_document_requests.c.status,
                                  portal_document_requests.c.uploaded_document_id).where(
            portal_document_requests.c.id == req)).mappings().one()
        assert reqrow["status"] == "uploaded" and reqrow["uploaded_document_id"] == doc_id
        assert c.scalar(select(func.count()).select_from(document_versions).where(
            document_versions.c.document_id == doc_id)) == 1
