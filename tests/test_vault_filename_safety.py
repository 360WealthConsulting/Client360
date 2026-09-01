"""Vault / portal filename safety (R1.1).

``vault_documents`` is a SEPARATE storage model from the canonical ``documents`` table: it keeps the
uploaded name in ``original_filename``. Three download routes returned that column verbatim and handed
it to ``FileResponse(filename=...)``, which becomes ``Content-Disposition`` and therefore the name the
browser SAVES the file under. A client who uploaded ``2024 W2 SSN 123-45-6789.pdf`` had that name
delivered straight back by the staff vault, the client portal and the portal JSON API — the same class
of exposure the canonical ``documents`` path already closed.

These tests are deliberately route-level for all three surfaces: the service seam is shared, so a
service-only test would not prove the routes actually emit the safe name. They fail against the
pre-fix implementation.

The safety DECISION is not re-tested here — that is ``tests/test_document_name_safety.py``'s job and
there is only one detector. What is pinned here is that the vault surfaces route through it, and that
provenance survives untouched.
"""
from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.db import engine, vault_documents
from app.portal import vault_documents as pv
from app.routes.portal import api_portal_document_download
from app.routes.portal_api import api_download_document
from app.routes.vault import download_document as vault_route_download
from app.services.vault import service as vault
from app.services.vault.naming import safe_vault_delivery_filename, safe_vault_label
from tests._portal_util import fake_request
from tests.test_portal_vault import _Env

pytestmark = pytest.mark.usefixtures("portal_documents_upload_on", "portal_documents_download_on")


SSN_NAME = "2024 W2 SSN 123-45-6789.pdf"
ACCT_NAME = "2023 Stmt Account No 4471002983 Chase.pdf"
NOTHING_CLEAN = "123-45-6789.pdf"          # unsafe with no clean alternative once scrubbed
SAFE_NAME = "2024 Engagement Letter.pdf"


@pytest.fixture
def env():
    e = _Env()
    try:
        yield e
    finally:
        e.cleanup()


def _staff_doc(env, person_id, *, original_filename, display_name, client_visible=True,
               approve=True):
    """A vault document whose stored original_filename is exactly ``original_filename``."""
    doc_id = vault.create_document(
        env.staff, source=io.BytesIO(b"vault bytes"), original_filename=original_filename,
        display_name=display_name, category="tax", actor_user_id=env.user_id, person_id=person_id)
    env.docs.append(doc_id)
    changes = {"client_visible": True} if client_visible else {}
    if approve:
        changes["status"] = "approved"
    if changes:
        vault.update_metadata(env.staff, doc_id, changes=changes, actor_user_id=env.user_id)
    return doc_id


def _row(doc_id):
    with engine.connect() as c:
        return c.execute(select(vault_documents).where(vault_documents.c.id == doc_id)).mappings().one()


# ---------------------------------------------------------------------------
# The pure seam
# ---------------------------------------------------------------------------
def test_ssn_filename_is_never_the_delivered_name():
    doc = {"id": 1, "display_name": SSN_NAME, "original_filename": SSN_NAME}
    delivered = safe_vault_delivery_filename(doc)
    assert "123-45-6789" not in delivered
    assert "123456789" not in delivered.replace(" ", "").replace("-", "")
    assert delivered.lower().endswith(".pdf")            # extension preserved


def test_account_number_filename_is_never_the_delivered_name():
    doc = {"id": 2, "display_name": ACCT_NAME, "original_filename": ACCT_NAME}
    delivered = safe_vault_delivery_filename(doc)
    assert "4471002983" not in delivered
    assert "Chase" in delivered                          # custodian survives; only the number goes
    assert delivered.lower().endswith(".pdf")


def test_unsafe_original_with_no_clean_alternative_falls_back_to_generic():
    """Both names unsafe and nothing meaningful survives the scrub: never emit the original."""
    doc = {"id": 77, "display_name": NOTHING_CLEAN, "original_filename": NOTHING_CLEAN}
    delivered = safe_vault_delivery_filename(doc)
    assert "123-45-6789" not in delivered
    assert delivered == "Document 77.pdf"


def test_safe_filename_is_delivered_unchanged():
    doc = {"id": 3, "display_name": SAFE_NAME, "original_filename": SAFE_NAME}
    assert safe_vault_delivery_filename(doc) == SAFE_NAME
    assert safe_vault_label(doc) == SAFE_NAME


def test_a_safe_original_filename_still_wins_over_a_different_display_name():
    """The vault has always DELIVERED original_filename. Closing this exposure must not silently
    rename everyone's safe downloads to their curated display_name."""
    doc = {"id": 5, "display_name": "Other Name", "original_filename": SAFE_NAME}
    assert safe_vault_delivery_filename(doc) == SAFE_NAME


def test_display_name_is_used_when_the_original_scrubs_down_to_nothing():
    """Second candidate: a curated name beats the generic id fallback."""
    doc = {"id": 4, "display_name": "Engagement Letter", "original_filename": NOTHING_CLEAN}
    delivered = safe_vault_delivery_filename(doc)
    assert delivered == "Engagement Letter.pdf"
    assert "123-45-6789" not in delivered


def test_seam_never_mutates_the_row():
    doc = {"id": 5, "display_name": SSN_NAME, "original_filename": SSN_NAME}
    before = dict(doc)
    safe_vault_delivery_filename(doc)
    safe_vault_label(doc)
    assert doc == before


# ---------------------------------------------------------------------------
# Route: staff vault  (GET /api/vault/documents/{id}/download)
# ---------------------------------------------------------------------------
def test_vault_route_never_emits_an_unsafe_filename(env):
    _, _, person_id, _ = env.account()
    doc_id = _staff_doc(env, person_id, original_filename=SSN_NAME, display_name=SSN_NAME)

    response = vault_route_download(fake_request(), doc_id, principal=env.staff)

    assert "123-45-6789" not in response.filename
    assert "123-45-6789" not in response.headers["content-disposition"]
    assert response.filename.lower().endswith(".pdf")


def test_vault_route_passes_a_safe_filename_through(env):
    _, _, person_id, _ = env.account()
    doc_id = _staff_doc(env, person_id, original_filename=SAFE_NAME, display_name=SAFE_NAME)

    response = vault_route_download(fake_request(), doc_id, principal=env.staff)

    assert response.filename == SAFE_NAME


# ---------------------------------------------------------------------------
# Route: client portal  (GET /api/v1/portal/documents/{id}/download)
# ---------------------------------------------------------------------------
def test_portal_route_never_emits_an_unsafe_filename(env):
    _, principal, person_id, _ = env.account()
    doc_id = _staff_doc(env, person_id, original_filename=ACCT_NAME, display_name=ACCT_NAME)

    response = api_portal_document_download(fake_request(), doc_id, principal=principal)

    assert "4471002983" not in response.filename
    assert "4471002983" not in response.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Route: portal JSON API  (GET /api/v1/portal/documents/{id}/download)
# ---------------------------------------------------------------------------
def test_portal_api_route_never_emits_an_unsafe_filename(env):
    _, principal, person_id, _ = env.account()
    doc_id = _staff_doc(env, person_id, original_filename=SSN_NAME, display_name=SSN_NAME)

    response = api_download_document(fake_request(), doc_id, principal=principal)

    assert "123-45-6789" not in response.filename
    assert "123-45-6789" not in response.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Client-facing list label
# ---------------------------------------------------------------------------
def test_portal_list_label_is_scrubbed(env):
    _, principal, person_id, _ = env.account()
    _staff_doc(env, person_id, original_filename=SSN_NAME, display_name=SSN_NAME)

    rows = pv.portal_documents(principal)

    assert rows, "the client should see their own client-visible approved document"
    for row in rows:
        assert "123-45-6789" not in row["display_name"]


# ---------------------------------------------------------------------------
# Provenance is untouched  (requirement 4)
# ---------------------------------------------------------------------------
def test_download_does_not_alter_stored_provenance(env):
    _, _, person_id, _ = env.account()
    doc_id = _staff_doc(env, person_id, original_filename=SSN_NAME, display_name=SSN_NAME)
    before = _row(doc_id)

    vault_route_download(fake_request(), doc_id, principal=env.staff)
    after = _row(doc_id)

    assert after["original_filename"] == SSN_NAME          # provenance preserved verbatim
    assert after["display_name"] == before["display_name"]
    assert after["storage_key"] == before["storage_key"]   # file neither renamed nor moved
    assert after["checksum_sha256"] == before["checksum_sha256"]
    assert after["file_size"] == before["file_size"]


def test_delivered_bytes_are_the_untouched_original(env):
    _, _, person_id, _ = env.account()
    doc_id = _staff_doc(env, person_id, original_filename=SSN_NAME, display_name=SSN_NAME)

    response = vault_route_download(fake_request(), doc_id, principal=env.staff)

    assert response.path is not None
    with open(response.path, "rb") as fh:
        assert fh.read() == b"vault bytes"                 # only the LABEL changed
