"""Staff → client message attachments (Batch 3c).

The client→staff half shipped in 3b. This is the other direction, and it is deliberately the SMALLEST
safe subset of the three possible staff sources:

  1. upload a new file into the vault for the message   — needs vault.upload, which the coordinator
                                                           roles do not hold. NOT built.
  2. attach an ALREADY client-visible vault document    — needs no write capability at all. BUILT.
  3. publish a canonical document into the vault        — needs vault.manage (administrator-only) and
                                                           is a deliberate review step. DEFERRED.

So attaching PUBLISHES NOTHING. Making a document client-visible stays the audited Vault action it
already was, which is what keeps internal work product from reaching a client by way of a message.

Three conditions, all pre-existing rules, are re-derived on submit rather than checked against the
form's claim — the picker and the guard run the same function, so they cannot diverge:

  * the document is already ``client_visible``;
  * it is linked to this thread's person or household through ``vault_document_links``;
  * the staff principal passes the Vault's own authorization for it (category + record scope).

The invariant from 3b is unchanged and still absolute: a client-visible message may carry only
vault-backed attachments, and canonical ``documents`` never become a client-facing fallback.
"""
from __future__ import annotations

import io
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.db import (
    engine,
    portal_message_attachments,
    portal_messages,
    record_assignments,
    vault_document_links,
    vault_documents,
)
from app.portal import communication_hub as hub
from app.portal import message_attachments as msg_attachments
from app.portal import vault_documents as portal_vault
from app.portal.service import list_messages, staff_send_message
from app.routes.portal import portal_message_thread_page
from app.routes.portal_admin import portal_admin_thread, portal_admin_thread_reply
from app.security.models import Principal
from tests._portal_util import fake_request, render, seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("production_identity_provider")

STAFF_CAPS = frozenset({"communications.message.read", "communications.message.write",
                        "client.read", "client.write", "record.read_all", "record.write_all",
                        "vault.view", "vault.download", "vault.category.general"})


@pytest.fixture
def gates(portal_master_on):
    portal_master_on.update({"portal.messaging_enabled", "portal.documents.upload_enabled",
                             "portal.documents.download_enabled"})
    return portal_master_on


def _staff(uid=None, caps=STAFF_CAPS):
    return Principal(uid or seed_staff_user(), "staff@example.com", "Staff", frozenset(caps))


def _shared_document(client_principal, person_id, *, label=None):
    """A vault document ALREADY shared with the client — the only source this batch attaches from."""
    doc_id = portal_vault.upload_document(
        client_principal, source=io.BytesIO(b"%PDF-1.4 shared"),
        original_filename=f"{label or 'plan'}-{uuid.uuid4().hex[:8]}.pdf",
        display_name=label or "Retirement plan")
    with engine.begin() as c:                      # published + approved, as staff would leave it
        c.execute(vault_documents.update().where(vault_documents.c.id == doc_id).values(
            client_visible=True, status="approved"))
    return doc_id


def _thread(staff_uid=None):
    staff_uid = staff_uid or seed_staff_user()
    _, client, person_id, household_id = seed_portal_account(staff_uid)
    with engine.begin() as c:
        c.execute(record_assignments.insert().values(
            entity_type="person", entity_id=person_id, user_id=staff_uid,
            assignment_type="owner", effective_date=date.today()))
    thread_id = hub.staff_start_thread(_staff(staff_uid), person_id=person_id,
                                       subject="Your plan", body="Opening message")
    return client, person_id, household_id, thread_id, staff_uid


def _rows(message_id):
    with engine.connect() as c:
        return c.execute(select(portal_message_attachments).where(
            portal_message_attachments.c.message_id == message_id)).mappings().all()


# --- 7 + 8. staff can attach on a new thread and on a reply -------------------

def test_staff_start_a_thread_with_an_attachment(gates):
    staff_uid = seed_staff_user()
    _, client, person_id, _ = seed_portal_account(staff_uid)
    with engine.begin() as c:
        c.execute(record_assignments.insert().values(
            entity_type="person", entity_id=person_id, user_id=staff_uid,
            assignment_type="owner", effective_date=date.today()))
    doc_id = _shared_document(client, person_id)

    thread_id = hub.staff_start_thread(
        _staff(staff_uid), person_id=person_id, subject="Your plan",
        body="Please review the attached.", attachment_vault_document_ids=[doc_id])

    with engine.connect() as c:
        message_id = c.scalar(select(portal_messages.c.id).where(
            portal_messages.c.thread_id == thread_id))
    rows = _rows(message_id)
    assert len(rows) == 1
    assert rows[0]["vault_document_id"] == doc_id and rows[0]["document_id"] is None


def test_staff_reply_with_an_attachment(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id)

    message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Attached",
                                    attachment_vault_document_ids=[doc_id],
                                    principal=_staff(staff_uid))

    rows = _rows(message_id)
    assert len(rows) == 1 and rows[0]["vault_document_id"] == doc_id


# --- 9-11. it is a vault document, explicitly shared and correctly linked ------

def test_the_attachment_is_a_vault_document_never_a_canonical_one(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id)
    message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="A",
                                    attachment_vault_document_ids=[doc_id],
                                    principal=_staff(staff_uid))

    row = _rows(message_id)[0]
    assert row["document_id"] is None
    with engine.connect() as c:
        assert c.scalar(select(vault_documents.c.id).where(
            vault_documents.c.id == row["vault_document_id"])) == doc_id
        assert c.scalar(select(vault_documents.c.client_visible).where(
            vault_documents.c.id == doc_id)) is True
        links = c.execute(select(vault_document_links).where(
            vault_document_links.c.document_id == doc_id)).mappings().all()
    assert any(link["person_id"] == person_id for link in links)


def test_attaching_publishes_nothing(gates):
    """A document that is NOT client-visible cannot be attached, and attaching does not make it so.
    Publication stays the deliberate, audited Vault action it already was."""
    client, person_id, _, thread_id, staff_uid = _thread()
    unpublished = portal_vault.upload_document(
        client, source=io.BytesIO(b"%PDF-1.4 x"), original_filename="internal.pdf",
        display_name="Internal working paper")
    with engine.begin() as c:
        c.execute(vault_documents.update().where(
            vault_documents.c.id == unpublished).values(client_visible=False))

    with pytest.raises(msg_attachments.MessageAttachmentError):
        staff_send_message(thread_id=thread_id, user_id=staff_uid, body="try",
                           attachment_vault_document_ids=[unpublished],
                           principal=_staff(staff_uid))

    with engine.connect() as c:
        assert c.scalar(select(vault_documents.c.client_visible).where(
            vault_documents.c.id == unpublished)) is False, "attaching must never publish"
        assert c.scalar(select(func.count()).select_from(portal_message_attachments).where(
            portal_message_attachments.c.vault_document_id == unpublished)) == 0


def test_another_clients_document_cannot_be_attached(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    _, other_client, other_person, _ = seed_portal_account(seed_staff_user())
    theirs = _shared_document(other_client, other_person)

    with pytest.raises(msg_attachments.MessageAttachmentError):
        staff_send_message(thread_id=thread_id, user_id=staff_uid, body="wrong client",
                           attachment_vault_document_ids=[theirs], principal=_staff(staff_uid))


def test_a_staff_member_cannot_attach_a_document_they_may_not_read(gates):
    """The picker and the guard both run the Vault's own authorization, so a category the staff
    member lacks is refused even though the document IS shared with the client."""
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id)
    with engine.begin() as c:
        c.execute(vault_documents.update().where(
            vault_documents.c.id == doc_id).values(category="payroll"))

    general_only = _staff(staff_uid)          # holds vault.category.general, not payroll
    assert doc_id not in [d["id"] for d in msg_attachments.attachable_for_client(
        general_only, person_id=person_id)]
    with pytest.raises(msg_attachments.MessageAttachmentError):
        staff_send_message(thread_id=thread_id, user_id=staff_uid, body="x",
                           attachment_vault_document_ids=[doc_id], principal=general_only)


def test_a_guessed_id_is_refused(gates):
    _, _, _, thread_id, staff_uid = _thread()
    with pytest.raises(msg_attachments.MessageAttachmentError):
        staff_send_message(thread_id=thread_id, user_id=staff_uid, body="guess",
                           attachment_vault_document_ids=[999_999_999],
                           principal=_staff(staff_uid))


def test_attaching_requires_the_staff_principal_not_just_a_user_id(gates):
    """``user_id`` is an identifier; authorization needs the principal's capabilities."""
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id)
    with pytest.raises(msg_attachments.MessageAttachmentError):
        staff_send_message(thread_id=thread_id, user_id=staff_uid, body="no principal",
                           attachment_vault_document_ids=[doc_id])


# --- 12 + 13 + 14. the client sees it, downloads it, others cannot ------------

def test_the_client_sees_and_can_download_the_staff_attachment(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id, label="statement")
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Attached",
                       attachment_vault_document_ids=[doc_id], principal=_staff(staff_uid))

    html = render(portal_message_thread_page(
        thread_id, fake_request(f"/portal/messages/{thread_id}"), client))
    assert "statement" in html
    assert f"/api/v1/portal/documents/{doc_id}/download" in html
    assert "/api/vault/documents/" not in html

    path, filename, _mime = portal_vault.download_document(client, doc_id)
    assert path.is_file() and filename


def test_another_client_cannot_download_the_staff_attachment(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id)
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="A",
                       attachment_vault_document_ids=[doc_id], principal=_staff(staff_uid))
    _, stranger, _, _ = seed_portal_account(seed_staff_user())

    with pytest.raises(PermissionError):
        portal_vault.download_document(stranger, doc_id)


def test_staff_see_it_too(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id, label="statement")
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Attached",
                       attachment_vault_document_ids=[doc_id], principal=_staff(staff_uid))
    staff = _staff(staff_uid)

    html = render(portal_admin_thread(thread_id, fake_request("/x", state_principal=staff), staff))
    assert "statement" in html and f"/api/vault/documents/{doc_id}/download" in html


# --- 15 + 16 + 17. failure, notifications, leakage ---------------------------

def test_a_refused_attachment_leaves_no_message(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    _, other_client, other_person, _ = seed_portal_account(seed_staff_user())
    theirs = _shared_document(other_client, other_person)
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(portal_messages).where(
            portal_messages.c.thread_id == thread_id))

    with pytest.raises(msg_attachments.MessageAttachmentError):
        staff_send_message(thread_id=thread_id, user_id=staff_uid, body="should not persist",
                           attachment_vault_document_ids=[theirs], principal=_staff(staff_uid))

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_messages).where(
            portal_messages.c.thread_id == thread_id)) == before
        assert "should not persist" not in [m["body"] for m in list_messages(client, thread_id)]


def test_the_client_notification_still_fires_exactly_once(gates):
    from app.db import portal_notifications

    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id)

    message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Attached",
                                    attachment_vault_document_ids=[doc_id],
                                    principal=_staff(staff_uid))

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_notifications).where(
            portal_notifications.c.idempotency_key == f"portal-message:{message_id}")) == 1


def test_no_storage_identifier_reaches_either_page(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id)
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="A",
                       attachment_vault_document_ids=[doc_id], principal=_staff(staff_uid))
    with engine.connect() as c:
        row = c.execute(select(vault_documents).where(
            vault_documents.c.id == doc_id)).mappings().one()

    staff = _staff(staff_uid)
    for html in (render(portal_admin_thread(thread_id, fake_request("/x", state_principal=staff), staff)),
                 render(portal_message_thread_page(
                     thread_id, fake_request(f"/portal/messages/{thread_id}"), client))):
        assert row["storage_key"] not in html and row["checksum_sha256"] not in html
        for leaked in ("storage_key", "checksum_sha256", "storage_path"):
            assert leaked not in html


# --- 18. internal notes are unchanged and stay invisible ---------------------

def test_an_internal_note_cannot_carry_a_client_document(gates):
    """The client cannot see an internal note, so attaching a client document to one is incoherent.
    The route refuses it rather than silently dropping the attachment."""
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id)
    staff = _staff(staff_uid)

    response = portal_admin_thread_reply(
        thread_id, request=fake_request("/x", "POST"), body="note", internal_note="1",
        attachment_vault_document_id=str(doc_id), principal=staff)

    assert response.status_code == 303 and "error=" in response.headers["location"]
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_message_attachments).where(
            portal_message_attachments.c.vault_document_id == doc_id)) == 0


def test_internal_note_canonical_attachments_are_unchanged_and_client_invisible(gates):
    """Batch 3b behaviour, re-pinned: canonical attachments remain legitimate on an internal note and
    never reach the client's view of the conversation."""
    from app.db import documents

    client, person_id, _, thread_id, staff_uid = _thread()
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        canonical = c.execute(documents.insert().values(
            person_id=person_id, original_name="wp.pdf", stored_name=f"wp-{tag}.pdf",
            storage_path=f"/tmp/wp-{tag}.pdf", size_bytes=1,
            sha256=("b" * 54) + tag).returning(documents.c.id)).scalar_one()

    note_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Internal",
                                 internal_note=True, attachment_document_ids=[canonical])

    assert _rows(note_id)[0]["document_id"] == canonical
    assert msg_attachments.attachments_for_messages(
        [note_id], audience=msg_attachments.CLIENT) == {}
    html = render(portal_message_thread_page(
        thread_id, fake_request(f"/portal/messages/{thread_id}"), client))
    assert "wp.pdf" not in html and "Internal" not in html


# --- the reply route end to end ----------------------------------------------

def test_the_reply_route_attaches_and_renders(gates):
    client, person_id, _, thread_id, staff_uid = _thread()
    doc_id = _shared_document(client, person_id, label="statement")
    staff = _staff(staff_uid)

    # The picker offers it...
    html = render(portal_admin_thread(thread_id, fake_request("/x", state_principal=staff), staff))
    assert 'name="attachment_vault_document_id"' in html and str(doc_id) in html

    # ...and submitting it attaches it.
    response = portal_admin_thread_reply(
        thread_id, request=fake_request("/x", "POST"), body="Here you go", internal_note=None,
        attachment_vault_document_id=str(doc_id), principal=staff)
    assert response.status_code == 303 and "notice=" in response.headers["location"]

    with engine.connect() as c:
        message_id = c.scalar(select(func.max(portal_messages.c.id)).where(
            portal_messages.c.thread_id == thread_id))
    assert _rows(message_id)[0]["vault_document_id"] == doc_id


def test_the_picker_is_absent_when_nothing_is_shared_with_the_client(gates):
    _, _, _, thread_id, staff_uid = _thread()
    staff = _staff(staff_uid)
    html = render(portal_admin_thread(thread_id, fake_request("/x", state_principal=staff), staff))
    assert 'name="attachment_vault_document_id"' not in html
