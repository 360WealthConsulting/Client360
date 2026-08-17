"""Client-upload content sniffing: the untrusted portal upload path rejects a file whose bytes do not
match its claimed extension (e.g. a renamed executable/HTML), while trusted/staff paths are unchanged.
"""
from __future__ import annotations

import io

import pytest

from app.portal import vault_documents as pv
from app.security.models import Principal
from app.services.vault import service as vault
from app.services.vault.storage import VaultStorageError, content_matches_extension
from tests._portal_util import sample_upload, seed_portal_account, seed_staff_user

STAFF_CAPS = frozenset({"vault.view", "vault.upload", "vault.download", "vault.manage",
                        "vault.access.all", "record.read_all"})


# --- pure predicate ---------------------------------------------------------

def test_content_matches_extension_predicate():
    assert content_matches_extension("pdf", b"%PDF-1.7 ...")
    assert content_matches_extension("png", b"\x89PNG\r\n\x1a\n....")
    assert content_matches_extension("jpg", b"\xff\xd8\xff\xe0....")
    assert content_matches_extension("docx", b"PK\x03\x04....")
    assert content_matches_extension("xlsx", b"PK\x03\x04....")
    # mismatches
    assert not content_matches_extension("pdf", b"<html><script>evil()</script>")
    assert not content_matches_extension("png", b"not-a-png")
    assert not content_matches_extension("docx", b"%PDF-1.4 actually a pdf")
    # types without a signature are permitted (csv/txt), incl. unknown extensions
    assert content_matches_extension("csv", b"a,b,c\n1,2,3")
    assert content_matches_extension("txt", b"\x00\x01binary-ish but txt is not sniffed")


# --- client path enforces it ------------------------------------------------

def test_client_upload_rejects_mismatched_content():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    with pytest.raises(VaultStorageError):
        pv.upload_document(principal, source=io.BytesIO(b"<html>not a pdf</html>"),
                           original_filename="evil.pdf", display_name="Evil", category="general")


def test_client_upload_accepts_genuine_content():
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    doc_id = pv.upload_document(principal, source=io.BytesIO(sample_upload("pdf")),
                               original_filename="real.pdf", display_name="Real", category="general")
    assert doc_id


# --- trusted/staff path is unchanged (not sniffed) --------------------------

def test_staff_vault_upload_is_not_content_sniffed():
    staff_uid = seed_staff_user()
    staff = Principal(staff_uid, "s@e.test", "Staff", STAFF_CAPS)
    _, _, pid, _ = seed_portal_account(staff_uid)
    # Dummy bytes with a .pdf name succeed on the trusted path — behavior preserved.
    doc_id = vault.create_document(staff, source=io.BytesIO(b"dummy staff content"),
                                   original_filename="staff.pdf", display_name="Staff",
                                   category="general", actor_user_id=staff_uid, person_id=pid)
    assert doc_id
