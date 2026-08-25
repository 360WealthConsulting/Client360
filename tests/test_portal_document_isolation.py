"""Portal document listing exposes ONLY client-visible vault documents.

client_documents() used to read the canonical staff ``documents`` table filtered by person scope and
``archived is False``. That table has no client-publication flag, so every staff work product owned
by a reachable person was listed to the client -- with every column, including storage paths,
checksums, staff user ids and internal review state -- and soft-deleted rows stayed visible because
only ``archived`` was filtered. It also resolved scope WITHOUT ``permission="documents"``, so the
declared grant was never enforced.

These pin the corrected behaviour: the Vault is the only source, the grant is required, and the
projection is explicit.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import audit_events, documents, engine, portal_messages, vault_documents
from app.portal import vault_documents as pv
from app.portal.service import client_documents, dashboard
from tests.test_portal_vault import _Env

#: Columns that must never reach a portal client.
INTERNAL_FIELDS = ("storage_path", "storage_uri", "stored_name", "sha256", "storage_key",
                   "checksum_sha256", "created_by_user_id", "owner_user_id", "updated_by_user_id",
                   "reviewer_team_id", "review_status", "review_due_at", "notes",
                   "uploaded_by", "retention_policy_id", "folder_id")


@pytest.fixture
def env():
    e = _Env()
    try:
        yield e
    finally:
        e.cleanup()


def _canonical_doc(person_id, name="INTERNAL-WORKPAPER.pdf", *, archived=False, status="active"):
    """A canonical staff document — exactly what must NOT reach the portal."""
    with engine.begin() as c:
        return c.execute(insert(documents).values(
            person_id=person_id, original_name=name, stored_name=f"{uuid.uuid4().hex}.pdf",
            storage_path=f"/srv/docs/{uuid.uuid4().hex}.pdf", size_bytes=11,
            sha256=uuid.uuid4().hex * 2, content_type="application/pdf",
            archived=archived, status=status, uploaded_by="staff@firm.test",
        ).returning(documents.c.id)).scalar_one()


# --- A. canonical staff documents are never exposed -------------------------------------------
def test_a_canonical_staff_document_is_not_listed(env):
    _, principal, pid, _ = env.account()
    doc_id = _canonical_doc(pid)
    with engine.connect() as c:
        assert c.scalar(select(documents.c.id).where(documents.c.id == doc_id)) == doc_id
        assert c.scalar(select(func.count()).select_from(vault_documents)
                        .where(vault_documents.c.id == doc_id)) == 0     # not a vault doc
    listed = client_documents(principal)
    assert doc_id not in {d["id"] for d in listed}
    assert not any("INTERNAL-WORKPAPER" in str(d) for d in listed)


def test_a_canonical_staff_document_is_not_on_the_dashboard(env):
    _, principal, pid, _ = env.account()
    _canonical_doc(pid, name="DASHBOARD-LEAK.pdf")
    payload = dashboard(principal)
    assert not any("DASHBOARD-LEAK" in str(d) for d in payload["documents"])


def test_a_soft_deleted_canonical_document_is_not_exposed(env):
    """Only `archived` was filtered before; the workspace soft-delete sets status='deleted'."""
    _, principal, pid, _ = env.account()
    _canonical_doc(pid, name="SOFT-DELETED.pdf", status="deleted")
    assert not any("SOFT-DELETED" in str(d) for d in client_documents(principal))


# --- B/C. vault client-visibility -------------------------------------------------------------
def test_b_vault_document_not_marked_client_visible_is_absent(env):
    _, principal, pid, _ = env.account()
    hidden = env.staff_doc(pid, client_visible=False)
    assert hidden not in {d["id"] for d in client_documents(principal)}


def test_c_client_visible_vault_document_is_listed(env):
    _, principal, pid, _ = env.account()
    visible = env.staff_doc(pid, client_visible=True)
    assert visible in {d["id"] for d in client_documents(principal)}


# --- D. cross-client isolation ------------------------------------------------------------------
def test_d_client_a_cannot_see_or_download_client_b_documents(env):
    _, a_principal, a_pid, _ = env.account()
    _, _b_principal, b_pid, _ = env.account()
    a_doc = env.staff_doc(a_pid, client_visible=True)
    b_doc = env.staff_doc(b_pid, client_visible=True)

    a_ids = {d["id"] for d in client_documents(a_principal)}
    assert a_doc in a_ids
    assert b_doc not in a_ids                       # not in the list
    with pytest.raises(PermissionError):            # nor by id
        pv.download_document(a_principal, b_doc)


# --- E. the documents grant is enforced ------------------------------------------------------------
def test_e_account_without_documents_permission_gets_nothing(env):
    """Previously the dashboard resolved a permission-less scope and handed it to the document
    query, so a messages-only grant still received documents."""
    _, principal, pid, _ = env.account(permissions={"messages": True})
    visible = env.staff_doc(pid, client_visible=True)
    assert client_documents(principal) == []
    assert dashboard(principal)["documents"] == []
    with pytest.raises(PermissionError):
        pv.download_document(principal, visible)


def test_e_documents_permission_grants_access(env):
    _, principal, pid, _ = env.account(permissions={"documents": True})
    visible = env.staff_doc(pid, client_visible=True)
    assert visible in {d["id"] for d in client_documents(principal)}


# --- F. vault lifecycle semantics ------------------------------------------------------------------
def test_f_archived_vault_document_follows_vault_semantics(env):
    from app.services.vault import service as vault
    _, principal, pid, _ = env.account()
    doc = env.staff_doc(pid, client_visible=True)
    assert doc in {d["id"] for d in client_documents(principal)}
    vault.update_metadata(env.staff, doc, changes={"client_visible": False},
                          actor_user_id=env.user_id)
    assert doc not in {d["id"] for d in client_documents(principal)}


# --- G. no internal fields in the payload ------------------------------------------------------------
def test_g_payload_contains_no_internal_fields(env):
    _, principal, pid, _ = env.account()
    env.staff_doc(pid, client_visible=True)
    _canonical_doc(pid)
    for row in client_documents(principal):
        for field in INTERNAL_FIELDS:
            assert field not in row, f"{field} leaked into the portal payload"
    blob = str(client_documents(principal))
    assert "/srv/docs/" not in blob                 # no filesystem path anywhere


def test_g_dashboard_documents_contain_no_internal_fields(env):
    _, principal, pid, _ = env.account()
    env.staff_doc(pid, client_visible=True)
    for row in dashboard(principal)["documents"]:
        for field in INTERNAL_FIELDS:
            assert field not in row, f"{field} leaked into the dashboard payload"


# --- H. reads perform no writes -----------------------------------------------------------------------
def test_h_listing_and_dashboard_write_nothing(env):
    _, principal, pid, _ = env.account()
    env.staff_doc(pid, client_visible=True)
    _canonical_doc(pid)

    def counts():
        with engine.connect() as c:
            return {t.name: c.scalar(select(func.count()).select_from(t))
                    for t in (documents, vault_documents, portal_messages, audit_events)}

    before = counts()
    client_documents(principal)
    client_documents(principal)
    dashboard(principal)
    assert counts() == before
