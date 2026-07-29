"""Client Vault MVP — service, storage, authorization, and audit.

Covers the required behaviors: upload success, unauthorized upload/download denied, path-traversal
blocked, checksum saved, version increments, archive preserves the document, audit events for
upload/view/download, Lauren-style full access, Carl-style no access, and the client list returning
only linked documents. Uses the direct-call pattern with explicit Principals (see
tests/test_client360_workspace.py) so authorization is exercised without a live HTTP client.
"""
import hashlib
import io
import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import (
    engine,
    households,
    people,
    users,
    vault_document_audit_events,
    vault_document_links,
    vault_document_versions,
    vault_documents,
)
from app.security.models import Principal
from app.services.vault import service as vault
from app.services.vault import storage

# --- principals (capabilities mirror the migration's role grants) ------------
FULL = Principal(101, "lauren@firm.test", "Lauren", frozenset({
    "vault.view", "vault.upload", "vault.download", "vault.manage", "vault.access.all", "record.read_all"}))
TAX = Principal(102, "sarah@firm.test", "Sarah", frozenset({
    "vault.view", "vault.upload", "vault.download", "vault.category.tax", "record.read_all"}))
CARL = Principal(103, "carl@firm.test", "Carl", frozenset({"client.read"}))   # no vault access


@pytest.fixture(autouse=True)
def _vault_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_STORAGE_ROOT", str(tmp_path / "vault"))


@pytest.fixture(scope="module", autouse=True)
def _actor_users():
    """The uploaded_by FK requires real users — seed the three test actors (idempotent)."""
    rows = [(FULL.user_id, FULL.email, FULL.display_name), (TAX.user_id, TAX.email, TAX.display_name),
            (CARL.user_id, CARL.email, CARL.display_name)]
    with engine.begin() as c:
        for uid, email, name in rows:
            if not c.scalar(select(users.c.id).where(users.c.id == uid)):
                c.execute(insert(users).values(
                    id=uid, email=email, normalized_email=email.lower(), display_name=name, status="active"))
    yield
    with engine.begin() as c:
        c.execute(delete(users).where(users.c.id.in_([r[0] for r in rows])))


@pytest.fixture
def client_ids():
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"Vault HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(
            household_id=hid, full_name=f"Vault Client {tag}", active=True).returning(people.c.id)).scalar_one()
    created = {"person_id": pid, "household_id": hid}
    yield created
    with engine.begin() as c:
        doc_ids = [r[0] for r in c.execute(
            select(vault_document_links.c.document_id).where(vault_document_links.c.person_id == pid)).all()]
        if doc_ids:
            c.execute(delete(vault_document_audit_events).where(vault_document_audit_events.c.document_id.in_(doc_ids)))
            c.execute(delete(vault_document_versions).where(vault_document_versions.c.document_id.in_(doc_ids)))
            c.execute(delete(vault_document_links).where(vault_document_links.c.document_id.in_(doc_ids)))
            c.execute(delete(vault_documents).where(vault_documents.c.id.in_(doc_ids)))
        c.execute(delete(people).where(people.c.id == pid))
        c.execute(delete(households).where(households.c.id == hid))


def _upload(principal, person_id, *, content=b"hello vault", name="return.pdf", category="tax", **kw):
    return vault.create_document(
        principal, source=io.BytesIO(content), original_filename=name, display_name=kw.pop("display_name", "Doc"),
        category=category, actor_user_id=principal.user_id, person_id=person_id, **kw)


def _count_audit(document_id, action):
    with engine.connect() as c:
        return c.scalar(select(func.count()).select_from(vault_document_audit_events).where(
            (vault_document_audit_events.c.document_id == document_id)
            & (vault_document_audit_events.c.action == action)))


# --- upload / checksum -------------------------------------------------------

def test_upload_success(client_ids):
    doc_id = _upload(TAX, client_ids["person_id"])
    with engine.connect() as c:
        doc = c.execute(select(vault_documents).where(vault_documents.c.id == doc_id)).mappings().one()
        vcount = c.scalar(select(func.count()).select_from(vault_document_versions).where(
            vault_document_versions.c.document_id == doc_id))
    assert doc["display_name"] == "Doc" and doc["category"] == "tax" and doc["current_version"] == 1
    assert doc["original_filename"] == "return.pdf" and vcount == 1


def test_checksum_saved(client_ids):
    content = b"the quick brown vault"
    doc_id = _upload(TAX, client_ids["person_id"], content=content)
    expected = hashlib.sha256(content).hexdigest()
    with engine.connect() as c:
        doc = c.execute(select(vault_documents).where(vault_documents.c.id == doc_id)).mappings().one()
    assert doc["checksum_sha256"] == expected and doc["file_size"] == len(content)


# --- authorization -----------------------------------------------------------

def test_unauthorized_upload_denied_by_route_gate():
    from fastapi import HTTPException

    from app.security.dependencies import require_capability
    gate = require_capability("vault.upload")
    with pytest.raises(HTTPException) as exc:
        gate(principal=CARL)                       # Carl lacks vault.upload → 403 at the API boundary
    assert exc.value.status_code == 403


def test_unauthorized_upload_denied_wrong_category(client_ids):
    # Sarah is tax-only; uploading a benefits document is refused.
    with pytest.raises(vault.VaultPermissionError):
        _upload(TAX, client_ids["person_id"], category="benefits")


def test_unauthorized_download_denied(client_ids):
    doc_id = _upload(FULL, client_ids["person_id"], category="benefits")   # a benefits doc
    with pytest.raises(vault.VaultPermissionError):
        vault.download_target(TAX, doc_id, actor_user_id=TAX.user_id)      # tax-only Sarah cannot download it


def test_path_traversal_blocked():
    for bad in ("../../etc/passwd", "..\\..\\secret", "/etc/passwd", "ab/../../x.pdf", "zz/deadbeef.pdf"):
        with pytest.raises(storage.VaultStorageError):
            storage.resolve_path(bad)
    # A malicious original filename never influences the path (key is a generated uuid).
    stored = storage.save_stream(io.BytesIO(b"x"), original_filename="../../evil.pdf")
    resolved = storage.resolve_path(stored["storage_key"])
    assert storage.storage_root() in resolved.parents and resolved.exists()


# --- versions ----------------------------------------------------------------

def test_version_increments_correctly(client_ids):
    doc_id = _upload(TAX, client_ids["person_id"], content=b"v1")
    v2 = vault.add_version(TAX, doc_id, source=io.BytesIO(b"v2 content"), original_filename="return.pdf",
                           actor_user_id=TAX.user_id)
    assert v2 == 2
    with engine.connect() as c:
        doc = c.execute(select(vault_documents).where(vault_documents.c.id == doc_id)).mappings().one()
        versions = [r[0] for r in c.execute(select(vault_document_versions.c.version_number).where(
            vault_document_versions.c.document_id == doc_id).order_by(vault_document_versions.c.version_number)).all()]
    assert doc["current_version"] == 2 and versions == [1, 2]
    assert doc["checksum_sha256"] == hashlib.sha256(b"v2 content").hexdigest()   # current points at v2


# --- archive -----------------------------------------------------------------

def test_archive_preserves_document(client_ids):
    doc_id = _upload(TAX, client_ids["person_id"])
    # Archive requires vault.manage (dept uploaders can't archive); Lauren/full-access can.
    vault.archive_document(FULL, doc_id, actor_user_id=FULL.user_id)
    with engine.connect() as c:
        doc = c.execute(select(vault_documents).where(vault_documents.c.id == doc_id)).mappings().one()
        vcount = c.scalar(select(func.count()).select_from(vault_document_versions).where(
            vault_document_versions.c.document_id == doc_id))
    assert doc["status"] == "archived" and doc["archived_at"] is not None
    assert vcount == 1 and storage.resolve_path(doc["storage_key"]).exists()   # row + file preserved


# --- audit -------------------------------------------------------------------

def test_audit_event_created_for_upload(client_ids):
    doc_id = _upload(TAX, client_ids["person_id"])
    assert _count_audit(doc_id, "upload") == 1


def test_audit_event_created_for_view(client_ids):
    doc_id = _upload(TAX, client_ids["person_id"])
    vault.get_document(TAX, doc_id, actor_user_id=TAX.user_id, audit_action="view")
    assert _count_audit(doc_id, "view") == 1


def test_audit_event_created_for_download(client_ids):
    doc_id = _upload(TAX, client_ids["person_id"])
    vault.download_target(TAX, doc_id, actor_user_id=TAX.user_id)
    assert _count_audit(doc_id, "download") == 1


# --- role matrix -------------------------------------------------------------

def test_lauren_has_full_document_access(client_ids):
    pid = client_ids["person_id"]
    tax_doc = _upload(FULL, pid, category="tax")
    ben_doc = _upload(FULL, pid, category="benefits")
    # Full access reaches every category.
    assert vault.get_document(FULL, tax_doc, actor_user_id=FULL.user_id, audit_action=None)["document"]["id"] == tax_doc
    assert vault.get_document(FULL, ben_doc, actor_user_id=FULL.user_id, audit_action=None)["document"]["id"] == ben_doc
    listed = {d["id"] for d in vault.list_documents(FULL, person_id=pid)}
    assert {tax_doc, ben_doc} <= listed


def test_carl_has_no_routine_client_document_access(client_ids):
    pid = client_ids["person_id"]
    doc_id = _upload(FULL, pid, category="tax")
    with pytest.raises(vault.VaultPermissionError):
        _upload(CARL, pid)                                      # cannot upload
    with pytest.raises(vault.VaultPermissionError):
        vault.get_document(CARL, doc_id, actor_user_id=CARL.user_id, audit_action=None)  # cannot view
    assert vault.list_documents(CARL, person_id=pid) == []      # list hides everything


def test_vault_tab_lists_person_documents(client_ids):
    # Regression: the Client 360 Vault tab (person view) must list a person-linked doc. Passing both
    # person_id AND household_id would AND them and drop person-linked docs (NULL household on the link).
    from app.services.client360 import sections
    pid, hid = client_ids["person_id"], client_ids["household_id"]
    doc_id = _upload(FULL, pid, category="tax")
    sec = sections.vault(FULL, {"entity_type": "person", "entity_id": pid, "person_id": pid,
                                "household_id": hid, "vault_view": None})
    assert doc_id in {d["id"] for d in sec["documents"]}


def test_client_list_only_returns_linked_documents(client_ids):
    pid = client_ids["person_id"]
    mine = _upload(FULL, pid, category="tax")
    # A second client with its own document.
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        other_pid = c.execute(insert(people).values(
            full_name=f"Other {tag}", active=True).returning(people.c.id)).scalar_one()
    other_doc = _upload(FULL, other_pid, category="tax")
    try:
        ids = {d["id"] for d in vault.list_documents(FULL, person_id=pid)}
        assert mine in ids and other_doc not in ids            # only this client's linked docs
    finally:
        with engine.begin() as c:
            c.execute(delete(vault_document_audit_events).where(vault_document_audit_events.c.document_id == other_doc))
            c.execute(delete(vault_document_versions).where(vault_document_versions.c.document_id == other_doc))
            c.execute(delete(vault_document_links).where(vault_document_links.c.document_id == other_doc))
            c.execute(delete(vault_documents).where(vault_documents.c.id == other_doc))
            c.execute(delete(people).where(people.c.id == other_pid))
