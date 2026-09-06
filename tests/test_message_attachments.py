"""Vault-backed secure-message attachments (Batch 3b) — schema, invariant and the client→staff flow.

Batch 3a established that both attachment tables reference the CANONICAL `documents` table because
both predate the Vault, while every client-safe document surface is vault-only by design. Migration
`msgatt01` adds a second, nullable reference so an attachment can point at either store.

TWO TABLES, TWO CONSTRAINTS, because they have deliberately different lifecycles:

  portal_message_attachments   document_id CASCADE, no tombstone  -> EXACTLY ONE reference
  communication_attachments    document_id SET NULL, tombstones   -> AT MOST ONE reference

Forcing exactly-one on the communications table would outlaw the state D.18 chose on purpose — a row
whose document was deleted but whose history survives — and there were 25 such rows before this
change. Those rows are proven still valid below.

THE INVARIANT: a CLIENT-VISIBLE portal message may carry only vault-backed attachments; an INTERNAL
note may carry either. The discriminator lives on the parent row and a PostgreSQL CHECK cannot read
another table, so it is enforced in `portal.message_attachments` — the one write path — and pinned
here from every caller. Canonical `documents` never becomes a client-facing fallback.
"""
from __future__ import annotations

import io
import uuid

import pytest
from sqlalchemy import func, insert, select, text

from app.db import (
    communication_attachments,
    documents,
    engine,
    portal_message_attachments,
    portal_messages,
    portal_threads,
    vault_documents,
)
from app.portal import message_attachments as msg_attachments
from app.portal import vault_documents as portal_vault
from app.portal.service import create_thread, list_messages, send_message, staff_send_message
from app.routes.portal import portal_message_thread_page
from app.routes.portal_admin import portal_admin_thread
from app.security.models import Principal
from tests._portal_util import fake_request, render, seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("production_identity_provider")

PDF = b"%PDF-1.4 attachment bytes"
#: Staff who may open the thread AND download from the vault. See the client_service note at the end.
STAFF_CAPS = frozenset({"communications.message.read", "communications.message.write",
                        "client.read", "client.write", "record.read_all", "record.write_all",
                        "vault.view", "vault.download", "vault.category.general"})


@pytest.fixture
def gates(portal_master_on):
    """Messaging + vault upload/download, and nothing else."""
    portal_master_on.update({"portal.messaging_enabled", "portal.documents.upload_enabled",
                             "portal.documents.download_enabled"})
    return portal_master_on


def _staff(uid=None, caps=STAFF_CAPS):
    return Principal(uid or seed_staff_user(), "staff@example.com", "Staff", frozenset(caps))


def _upload(principal, name=None):
    """A client vault upload through the EXISTING path — the only way a client gets a document."""
    name = name or f"statement-{uuid.uuid4().hex[:8]}.pdf"
    return portal_vault.upload_document(
        principal, source=io.BytesIO(PDF), original_filename=name, display_name=name)


def _client_thread(staff_uid=None):
    staff_uid = staff_uid or seed_staff_user()
    _, principal, person_id, household_id = seed_portal_account(staff_uid)
    return principal, person_id, household_id, staff_uid


def _canonical_document(person_id):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(documents.insert().values(
            person_id=person_id, original_name="internal.pdf", stored_name=f"i-{tag}.pdf",
            storage_path=f"/tmp/i-{tag}.pdf", size_bytes=1,
            sha256=("a" * 54) + tag[:10]).returning(documents.c.id)).scalar_one()


def _rows(message_id):
    with engine.connect() as c:
        return c.execute(select(portal_message_attachments).where(
            portal_message_attachments.c.message_id == message_id)).mappings().all()


# ============================ SCHEMA ============================

def test_both_tables_have_the_vault_reference():
    assert "vault_document_id" in portal_message_attachments.c
    assert "vault_document_id" in communication_attachments.c


def test_the_portal_table_requires_exactly_one_reference(gates):
    """CASCADE on both FKs means a row can never lose its reference, so exactly-one is permanently
    satisfiable — and is what stops a null-null row appearing."""
    principal, person_id, household_id, _ = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="S", body="B")
    with engine.connect() as c:
        message_id = c.scalar(select(portal_messages.c.id).where(
            portal_messages.c.thread_id == thread_id))
    vault_id, doc_id = _upload(principal), _canonical_document(person_id)

    # both -> rejected
    with pytest.raises(Exception) as both:
        with engine.begin() as c:
            c.execute(insert(portal_message_attachments).values(
                message_id=message_id, document_id=doc_id, vault_document_id=vault_id))
    assert "ck_pma_exactly_one_reference" in str(both.value)

    # neither -> rejected
    with pytest.raises(Exception) as neither:
        with engine.begin() as c:
            c.execute(insert(portal_message_attachments).values(
                message_id=message_id, document_id=None, vault_document_id=None))
    assert "ck_pma_exactly_one_reference" in str(neither.value)

    # each alone -> accepted
    with engine.begin() as c:
        c.execute(insert(portal_message_attachments).values(
            message_id=message_id, document_id=doc_id, vault_document_id=None))
        c.execute(insert(portal_message_attachments).values(
            message_id=message_id, document_id=None, vault_document_id=vault_id))
    assert len(_rows(message_id)) == 2


def test_the_communications_table_allows_neither_because_it_keeps_tombstones(gates):
    """SET NULL on both FKs is deliberate: deleting the document leaves the attachment as history.
    At-most-one is the only constraint consistent with that, so 'neither' stays legal."""
    from app.db import communication_conversations, communication_messages

    with engine.begin() as c:
        conv = c.execute(communication_conversations.insert().values(
            subject="Attachment shape").returning(communication_conversations.c.id)).scalar_one()
        msg = c.execute(communication_messages.insert().values(
            conversation_id=conv, body="b").returning(communication_messages.c.id)).scalar_one()
        # neither -> ACCEPTED (the tombstone state)
        c.execute(insert(communication_attachments).values(
            message_id=msg, document_id=None, vault_document_id=None))

    _, principal, person_id, _ = seed_portal_account(seed_staff_user())
    vault_id, doc_id = _upload(principal), _canonical_document(person_id)
    with engine.begin() as c:
        c.execute(insert(communication_attachments).values(message_id=msg, document_id=doc_id))
        c.execute(insert(communication_attachments).values(message_id=msg,
                                                           vault_document_id=vault_id))
    # both -> rejected
    with pytest.raises(Exception) as both:
        with engine.begin() as c:
            c.execute(insert(communication_attachments).values(
                message_id=msg, document_id=doc_id, vault_document_id=vault_id))
    assert "ck_comm_attachment_at_most_one_reference" in str(both.value)


def test_the_pre_existing_tombstone_rows_survived_the_migration():
    """The 25 rows Batch 3a found. If the migration had used exactly-one they would have been
    rejected or destroyed; they must still be present and still legal."""
    with engine.connect() as c:
        tombstones = c.scalar(select(func.count()).select_from(communication_attachments).where(
            communication_attachments.c.document_id.is_(None),
            communication_attachments.c.vault_document_id.is_(None)))
    assert tombstones >= 25, f"tombstone rows were lost: {tombstones}"


def test_the_vault_foreign_keys_are_enforced_and_carry_the_right_delete_rules():
    with engine.connect() as c:
        rules = dict(c.execute(text("""
            select rel.relname || '.' || con.conname,
                   case con.confdeltype when 'c' then 'CASCADE' when 'n' then 'SET NULL' end
            from pg_constraint con join pg_class rel on rel.oid = con.conrelid
            where con.contype='f' and con.conname in
                  ('fk_pma_vault_document','fk_comm_attachment_vault_document')""")).all())
    assert rules["portal_message_attachments.fk_pma_vault_document"] == "CASCADE"
    assert rules["communication_attachments.fk_comm_attachment_vault_document"] == "SET NULL"

    with pytest.raises(Exception):          # a non-existent vault document is refused
        with engine.begin() as c:
            c.execute(insert(portal_message_attachments).values(
                message_id=None, vault_document_id=-1))


def test_deleting_a_vault_document_removes_the_portal_link_but_tombstones_the_communication_one(gates):
    """The lifecycle difference, proven rather than asserted."""
    from app.db import communication_conversations, communication_messages

    principal, person_id, household_id, _ = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Lifecycle", body="B")
    with engine.connect() as c:
        message_id = c.scalar(select(portal_messages.c.id).where(
            portal_messages.c.thread_id == thread_id))
    vault_id = _upload(principal)
    with engine.begin() as c:
        c.execute(insert(portal_message_attachments).values(
            message_id=message_id, vault_document_id=vault_id))
        conv = c.execute(communication_conversations.insert().values(
            subject="L").returning(communication_conversations.c.id)).scalar_one()
        msg = c.execute(communication_messages.insert().values(
            conversation_id=conv, body="b").returning(communication_messages.c.id)).scalar_one()
        comm_row = c.execute(insert(communication_attachments).values(
            message_id=msg, vault_document_id=vault_id).returning(
            communication_attachments.c.id)).scalar_one()

    with engine.begin() as c:
        c.execute(text("delete from vault_document_links where document_id = :d"), {"d": vault_id})
        c.execute(vault_documents.delete().where(vault_documents.c.id == vault_id))

    with engine.connect() as c:
        assert _rows(message_id) == [], "the portal link should CASCADE away with the document"
        surviving = c.execute(select(communication_attachments).where(
            communication_attachments.c.id == comm_row)).mappings().one()
        assert surviving["vault_document_id"] is None, "the communication row should tombstone"


# ============================ SECURITY INVARIANT ============================

def test_a_client_visible_message_refuses_a_canonical_document(gates):
    """The whole point. Canonical documents are internal work product with no client-visibility flag
    and no client-facing serving path; one must never be attached where a client can read it."""
    principal, person_id, household_id, staff_uid = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="S", body="B")
    doc_id = _canonical_document(person_id)

    with pytest.raises(msg_attachments.MessageAttachmentError):
        send_message(principal, thread_id, "with an internal doc", attachment_document_ids=[doc_id])

    with pytest.raises(msg_attachments.MessageAttachmentError):
        staff_send_message(thread_id=thread_id, user_id=staff_uid, body="staff reply",
                           attachment_document_ids=[doc_id])

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_message_attachments).where(
            portal_message_attachments.c.document_id == doc_id)) == 0


def test_an_internal_note_may_still_carry_a_canonical_document(gates):
    """Staff-only, never delivered to a client — the one place a canonical attachment is legitimate."""
    principal, person_id, household_id, staff_uid = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="S", body="B")
    doc_id = _canonical_document(person_id)

    note_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Internal",
                                 internal_note=True, attachment_document_ids=[doc_id])

    rows = _rows(note_id)
    assert len(rows) == 1 and rows[0]["document_id"] == doc_id
    assert rows[0]["vault_document_id"] is None


def test_the_invariant_helper_refuses_only_client_visibility():
    msg_attachments.assert_canonical_allowed("internal")            # no raise
    with pytest.raises(msg_attachments.MessageAttachmentError):
        msg_attachments.assert_canonical_allowed("client")


def test_a_client_never_receives_a_canonical_attachment_view(gates):
    """Even for an internal note's canonical attachment, the client read model drops it — there is no
    client fallback to canonical documents anywhere in the render path."""
    principal, person_id, household_id, staff_uid = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="S", body="B")
    note_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Internal",
                                 internal_note=True,
                                 attachment_document_ids=[_canonical_document(person_id)])

    client_view = msg_attachments.attachments_for_messages(
        [note_id], audience=msg_attachments.CLIENT)
    assert client_view == {}
    staff_view = msg_attachments.attachments_for_messages([note_id],
                                                          audience=msg_attachments.STAFF)
    assert staff_view[note_id][0]["kind"] == "document"


# ============================ CLIENT -> STAFF WORKFLOW ============================

def test_a_client_starts_a_thread_with_an_attachment(gates):
    from app.routes.portal import portal_messages_new

    _, principal, person_id, household_id = seed_portal_account(seed_staff_user())
    vault_id = _upload(principal)
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="With file", body="See attached",
                              attachment_vault_document_ids=[vault_id])

    with engine.connect() as c:
        message_id = c.scalar(select(portal_messages.c.id).where(
            portal_messages.c.thread_id == thread_id))
    rows = _rows(message_id)
    assert len(rows) == 1
    assert rows[0]["vault_document_id"] == vault_id and rows[0]["document_id"] is None
    assert portal_messages_new is not None       # the route exists for the browser path


def test_a_client_replies_with_an_attachment(gates):
    principal, person_id, household_id, _ = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="S", body="B")
    vault_id = _upload(principal)

    message_id = send_message(principal, thread_id, "Here it is",
                              attachment_vault_document_ids=[vault_id])

    rows = _rows(message_id)
    assert len(rows) == 1 and rows[0]["vault_document_id"] == vault_id


def test_the_attachment_lands_on_the_right_message_and_thread(gates):
    principal, person_id, household_id, _ = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="S", body="B")
    plain = send_message(principal, thread_id, "no file")
    with_file = send_message(principal, thread_id, "file",
                             attachment_vault_document_ids=[_upload(principal)])

    assert _rows(plain) == []
    assert len(_rows(with_file)) == 1
    with engine.connect() as c:
        assert c.scalar(select(portal_messages.c.thread_id).where(
            portal_messages.c.id == with_file)) == thread_id


def test_another_clients_vault_document_cannot_be_attached(gates):
    """The id arrives from a browser, so it is a claim. It is re-resolved against this account's
    documents scope by the SAME rule the client download uses."""
    _, alice, alice_pid, alice_hid = seed_portal_account(seed_staff_user())
    _, bob, _, _ = seed_portal_account(seed_staff_user())
    bobs_file = _upload(bob)
    thread_id = create_thread(alice, household_id=alice_hid, person_id=alice_pid,
                              subject="S", body="B")

    with pytest.raises(PermissionError):
        send_message(alice, thread_id, "not mine", attachment_vault_document_ids=[bobs_file])

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_message_attachments).where(
            portal_message_attachments.c.vault_document_id == bobs_file)) == 0


def test_a_guessed_vault_id_is_not_enough(gates):
    principal, person_id, household_id, _ = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="S", body="B")
    with pytest.raises(PermissionError):
        send_message(principal, thread_id, "guess", attachment_vault_document_ids=[999_999_999])


# ============================ STAFF RENDERING AND DOWNLOAD ============================

def _seed_attached(gates_unused=None):
    staff_uid = seed_staff_user()
    _, principal, person_id, household_id = seed_portal_account(staff_uid)
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Docs", body="Opening")
    vault_id = _upload(principal, name="w2-statement.pdf")
    message_id = send_message(principal, thread_id, "Attached",
                              attachment_vault_document_ids=[vault_id])
    return principal, person_id, thread_id, message_id, vault_id, staff_uid


def test_staff_see_the_attachment_on_the_thread(gates):
    principal, _, thread_id, message_id, vault_id, staff_uid = _seed_attached()
    staff = _staff(staff_uid)

    html = render(portal_admin_thread(
        thread_id, fake_request(f"/admin/client-portal/threads/{thread_id}",
                                state_principal=staff), staff))

    assert "w2-statement" in html
    assert f"/api/vault/documents/{vault_id}/download" in html


def test_authorized_staff_can_download_through_the_existing_vault_path(gates):
    from app.services.vault import service as vault

    principal, _, _, _, vault_id, staff_uid = _seed_attached()
    path, filename, _mime = vault.download_target(_staff(staff_uid), vault_id)
    assert path.is_file() and filename


def test_unauthorized_staff_cannot_download(gates):
    """Category and record scope still decide, exactly as they do for any vault document."""
    from app.services.vault import service as vault
    from app.services.vault.service import VaultPermissionError

    principal, _, _, _, vault_id, _ = _seed_attached()
    no_category = _staff(caps=frozenset({"vault.view", "vault.download", "record.read_all"}))
    with pytest.raises(VaultPermissionError):
        vault.download_target(no_category, vault_id)

    no_scope = _staff(caps=frozenset({"vault.view", "vault.download", "vault.category.general"}))
    with pytest.raises(VaultPermissionError):
        vault.download_target(no_scope, vault_id)


def test_another_client_cannot_download_the_attachment(gates):
    _, alice, _, _ = seed_portal_account(seed_staff_user())
    _, _, _, _, vault_id, _ = _seed_attached()
    with pytest.raises(PermissionError):
        portal_vault.download_document(alice, vault_id)


def test_the_owning_client_sees_and_can_download_their_own_attachment(gates):
    principal, _, thread_id, message_id, vault_id, _ = _seed_attached()

    html = render(portal_message_thread_page(
        thread_id, fake_request(f"/portal/messages/{thread_id}"), principal))
    assert "w2-statement" in html
    assert f"/api/v1/portal/documents/{vault_id}/download" in html
    assert "/api/vault/documents/" not in html, "the client must not be linked to the staff route"

    path, filename, _mime = portal_vault.download_document(principal, vault_id)
    assert path.is_file() and filename


def test_no_storage_identifier_reaches_either_rendered_page(gates):
    principal, _, thread_id, _, vault_id, staff_uid = _seed_attached()
    with engine.connect() as c:
        row = c.execute(select(vault_documents).where(
            vault_documents.c.id == vault_id)).mappings().one()

    staff = _staff(staff_uid)
    staff_html = render(portal_admin_thread(
        thread_id, fake_request("/x", state_principal=staff), staff))
    client_html = render(portal_message_thread_page(
        thread_id, fake_request(f"/portal/messages/{thread_id}"), principal))

    for html in (staff_html, client_html):
        assert row["storage_key"] not in html
        assert row["checksum_sha256"] not in html
        for leaked in ("storage_key", "checksum_sha256", "storage_path", "storage_uri"):
            assert leaked not in html


# ============================ FAILURE AND COMPATIBILITY ============================

def test_a_refused_message_leaves_no_attachment_row(gates):
    """The attachment write shares the message transaction, so an out-of-scope reply rolls both back."""
    _, alice, _, _ = seed_portal_account(seed_staff_user())
    _, bob, bob_pid, bob_hid = seed_portal_account(seed_staff_user())
    bobs_thread = create_thread(bob, household_id=bob_hid, person_id=bob_pid,
                                subject="Bob", body="Private")
    alices_file = _upload(alice)
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(portal_message_attachments))

    with pytest.raises(PermissionError):
        send_message(alice, bobs_thread, "let me in",
                     attachment_vault_document_ids=[alices_file])

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_message_attachments)) == before


def test_a_rejected_upload_creates_no_message_and_no_vault_document(gates):
    """Storage validation runs BEFORE the message: a disallowed type produces neither."""
    from app.services.vault.storage import VaultStorageError

    _, principal, person_id, household_id = seed_portal_account(seed_staff_user())
    with engine.connect() as c:
        docs_before = c.scalar(select(func.count()).select_from(vault_documents))
        msgs_before = c.scalar(select(func.count()).select_from(portal_messages))

    with pytest.raises((VaultStorageError, ValueError)):
        portal_vault.upload_document(principal, source=io.BytesIO(b"MZ\x90\x00"),
                                     original_filename="payload.exe", display_name="payload.exe")

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(vault_documents)) == docs_before
        assert c.scalar(select(func.count()).select_from(portal_messages)) == msgs_before


def test_existing_vault_size_and_type_rules_still_apply(gates):
    """Reused, never re-implemented and never relaxed."""
    from app.services.vault import storage
    from app.services.vault.storage import VaultStorageError

    _, principal, _, _ = seed_portal_account(seed_staff_user())
    assert storage.MAX_UPLOAD_BYTES == 50 * 1024 * 1024
    assert "exe" not in storage.ALLOWED_EXTENSIONS and "pdf" in storage.ALLOWED_EXTENSIONS

    with pytest.raises((VaultStorageError, ValueError)):        # oversized
        portal_vault.upload_document(
            principal, source=io.BytesIO(b"%PDF-1.4" + b"0" * (storage.MAX_UPLOAD_BYTES + 1)),
            original_filename="big.pdf", display_name="big.pdf")

    with pytest.raises((VaultStorageError, ValueError)):        # content does not match extension
        portal_vault.upload_document(principal, source=io.BytesIO(b"<html>not a pdf</html>"),
                                     original_filename="fake.pdf", display_name="fake.pdf")


def test_a_thread_without_attachments_renders_exactly_as_before(gates):
    principal, person_id, household_id, staff_uid = _client_thread()
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Plain", body="No files here")
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Staff reply")

    staff = _staff(staff_uid)
    staff_html = render(portal_admin_thread(
        thread_id, fake_request("/x", state_principal=staff), staff))
    client_html = render(portal_message_thread_page(
        thread_id, fake_request(f"/portal/messages/{thread_id}"), principal))

    assert "No files here" in staff_html and "Staff reply" in staff_html
    assert "No files here" in client_html
    assert "📎" not in staff_html and "📎" not in client_html
    assert [m["body"] for m in list_messages(principal, thread_id)] == ["No files here", "Staff reply"]


def test_the_message_notification_still_fires_exactly_once_with_an_attachment(gates):
    """Batch 2 behaviour is unchanged: one notification per message, never one per attachment."""
    from app.services.notifications import _notifications_table

    staff_uid = seed_staff_user()
    _, principal, person_id, household_id = seed_portal_account(staff_uid)
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="S", body="B")
    with engine.begin() as c:
        c.execute(portal_threads.update().where(portal_threads.c.id == thread_id).values(
            assigned_user_id=staff_uid))

    message_id = send_message(principal, thread_id, "two files",
                              attachment_vault_document_ids=[_upload(principal), _upload(principal)])

    assert len(_rows(message_id)) == 2
    n = _notifications_table()
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(n).where(
            n.c.source_ref == f"portal-message:{message_id}")) == 1


# ============================ MERGE BEHAVIOUR ============================

def test_canonical_document_merge_cannot_see_or_strand_a_vault_reference():
    """`document_merge` enumerates FKs that reference `documents` (`rc.relname = 'documents'`).
    `vault_document_id` references `vault_documents`, so it is structurally invisible to a canonical
    merge — it can be neither rewritten nor stranded. Proven, not assumed."""
    import inspect

    from app.services import document_merge
    assert "rc.relname = 'documents'" in document_merge._FK_SQL

    with engine.connect() as c:
        deps = document_merge.dependencies(c) if hasattr(document_merge, "dependencies") else None
    if deps is not None:
        assert not any(d["column"] == "vault_document_id" for d in deps)

    src = inspect.getsource(document_merge)
    assert "vault_document_id" not in src, \
        "a canonical document merge must not treat a vault id as a canonical document id"


def test_vault_ownership_follows_vault_document_links_through_a_person_merge():
    """Vault ownership is `vault_document_links`, and person_merge already repoints it. The
    attachment row is untouched by design: it names the document, never the owner."""
    from app.services import person_merge

    entries = {(t, col) for t, col, *_ in person_merge.__dict__.get("_SIMPLE_TABLES", [])} \
        if "_SIMPLE_TABLES" in person_merge.__dict__ else set()
    source = open("app/services/person_merge.py", encoding="utf-8").read()
    assert '("vault_document_links", "person_id"' in source, \
        "person merge must repoint vault ownership links"
    assert "portal_message_attachments" not in source, \
        "attachment rows name a document, not a person — a person merge must not rewrite them"
    assert entries is not None


# ============================ KNOWN GAP: client_service download ============================

def test_client_service_can_open_the_thread_but_cannot_download_the_attachment(gates):
    """BLOCKED, recorded rather than hidden under a more privileged role.

    client_service holds communications.message.read/write, vault.view and vault.category.general —
    but NOT vault.download — so the primary Messages role sees the attachment and is refused the
    bytes. advisor and operations hold no vault capability at all. Resolving this is an authorization
    decision (see the Batch 3b report), deliberately not made inside this batch."""
    from fastapi import HTTPException

    from app.security.dependencies import require_capability
    from app.services.vault import service as vault

    _, _, thread_id, _, vault_id, staff_uid = _seed_attached()
    coordinator = Principal(staff_uid, "cs@example.com", "Coordinator", frozenset({
        "communications.message.read", "communications.message.write", "client.read", "client.write",
        "record.read_all", "record.write_all", "vault.view", "vault.category.general"}))

    html = render(portal_admin_thread(
        thread_id, fake_request("/x", state_principal=coordinator), coordinator))
    assert "w2-statement" in html, "the coordinator can see the attachment"

    # The gap is EXACTLY one capability, and nothing else: the coordinator already satisfies the
    # vault service's own authorization — category `general` plus record scope — so the document is
    # genuinely within their document authority. Only the route's door capability stops them.
    vault.download_target(coordinator, vault_id)               # service authorization: PASSES
    with pytest.raises(HTTPException) as excinfo:
        require_capability("vault.download")(principal=coordinator)
    assert excinfo.value.status_code == 403                    # route door: REFUSED. BLOCKED.
