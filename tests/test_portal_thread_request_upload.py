"""A linked document request on a client's secure-message thread offers the Upload action.

The Batch 3 attachment audit stopped: `portal_message_attachments` FKs to the canonical `documents`
table, while every client-safe document surface is vault-only by design, so true message attachments
need a schema decision. This is the interim that needs neither — the firm ALREADY has a complete,
audited client→staff file path on a thread:

    staff "create request" on a thread  ->  portal_document_requests.thread_id links it
    ->  client sees it under Linked requests  ->  uploads via /portal/upload?request_id=<id>
    ->  vault document, pending  ->  staff approve

Only the last affordance was missing: the thread showed the request but gave no way to act on it, so
a client had to go and find the same request again on Documents. This adds the SAME link
portal/documents.html and portal/requests.html already render, pointed at the same route.

NOTHING ELSE MOVES. No attachment table, no canonical-document exposure, no new upload mechanism, no
migration, no change to vault validation or to the document-request workflow. The link is presentation
only: `portal_can` mirrors the surface gate the middleware enforces, and `upload_document` re-resolves
the request against the account's documents scope, so the href is not a capability — which is what
`test_the_link_is_not_authorization` proves by driving the service with a forged request id.
"""
from __future__ import annotations

import io
import re
import uuid

import pytest
from sqlalchemy import select

from app.db import engine, portal_document_requests
from app.portal import vault_documents as portal_vault
from app.portal.service import create_document_request, create_thread
from app.routes.portal import portal_message_thread_page
from tests._portal_util import fake_request, render, seed_portal_account, seed_staff_user

#: The thread page itself needs messaging; the Upload action additionally needs the upload surface,
#: because `portal_can` asks the real gate. Both are the production-safe OFF default outside tests.
pytestmark = pytest.mark.usefixtures("production_identity_provider")

UPLOAD_HREF = "/portal/upload?request_id={request_id}"


@pytest.fixture
def gates(portal_master_on):
    """Messaging + document upload, and nothing else."""
    portal_master_on.update({"portal.messaging_enabled", "portal.documents.upload_enabled"})
    return portal_master_on


def _thread_with_request(staff_uid=None, *, status=None):
    """A client thread with one document request linked to it."""
    staff_uid = staff_uid or seed_staff_user()
    _, principal, person_id, household_id = seed_portal_account(staff_uid)
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Rollover", body="Opening message")
    request_id = create_document_request(
        person_id=person_id, household_id=household_id, title="2025 W-2",
        requested_by_user_id=staff_uid)
    with engine.begin() as c:
        values = {"thread_id": thread_id}
        if status is not None:
            values["status"] = status
        c.execute(portal_document_requests.update().where(
            portal_document_requests.c.id == request_id).values(**values))
    return principal, person_id, thread_id, request_id


def _thread_html(principal, thread_id):
    return render(portal_message_thread_page(
        thread_id, fake_request(f"/portal/messages/{thread_id}"), principal))


# --- 1 + 2. an eligible request renders the action, pointing at the real route -

def test_an_open_linked_request_renders_the_upload_action(gates):
    principal, _, thread_id, request_id = _thread_with_request()

    html = _thread_html(principal, thread_id)

    assert "2025 W-2" in html
    assert f'href="{UPLOAD_HREF.format(request_id=request_id)}"' in html
    assert "Upload for this request" in html


def test_the_action_carries_this_requests_id_and_no_other(gates):
    """A thread with two linked requests must link each to its own id."""
    principal, person_id, thread_id, first = _thread_with_request()
    with engine.connect() as c:
        household_id = c.scalar(select(portal_document_requests.c.household_id).where(
            portal_document_requests.c.id == first))
    second = create_document_request(person_id=person_id, household_id=household_id,
                                     title="Bank statement", requested_by_user_id=seed_staff_user())
    with engine.begin() as c:
        c.execute(portal_document_requests.update().where(
            portal_document_requests.c.id == second).values(thread_id=thread_id))

    html = _thread_html(principal, thread_id)

    ids = sorted(int(m) for m in re.findall(r"/portal/upload\?request_id=(\d+)", html))
    assert ids == sorted([first, second])


def test_the_action_matches_the_route_the_documents_page_already_uses():
    """One upload mechanism, not two: the same href shape as the existing client surfaces."""
    import pathlib
    thread = pathlib.Path("app/templates/portal/message_thread.html").read_text(encoding="utf-8")
    documents = pathlib.Path("app/templates/portal/documents.html").read_text(encoding="utf-8")
    assert "/portal/upload?request_id={{ r.id }}" in thread
    assert "/portal/upload?request_id={{ req.id }}" in documents


# --- 3. the link is not authorization ----------------------------------------

def test_the_link_is_not_authorization(gates):
    """Hand-editing request_id to another client's request must fail in the SERVICE, not rely on the
    link being absent. This is the existing upload-route rule, asserted from the thread's angle."""
    _, alice, _, _ = seed_portal_account(seed_staff_user())
    _, _bob, bob_pid, bob_hid = seed_portal_account(seed_staff_user())
    bobs_request = create_document_request(person_id=bob_pid, household_id=bob_hid,
                                           title="Bob's W-2", requested_by_user_id=seed_staff_user())

    with pytest.raises(PermissionError):
        portal_vault.upload_document(
            alice, source=io.BytesIO(b"%PDF-1.4 test"), original_filename="a.pdf",
            display_name="Attempt", request_id=bobs_request)

    with engine.connect() as c:
        assert c.scalar(select(portal_document_requests.c.status).where(
            portal_document_requests.c.id == bobs_request)) == "open", "Bob's request was touched"


def test_another_clients_thread_is_not_reachable_at_all(gates):
    """The link cannot be reached in the first place from someone else's thread."""
    from fastapi import HTTPException

    _, alice, _, _ = seed_portal_account(seed_staff_user())
    _, _bob, _, _ = seed_portal_account(seed_staff_user())
    _, _, bob_thread, _ = _thread_with_request()

    with pytest.raises(HTTPException) as excinfo:
        _thread_html(alice, bob_thread)
    assert excinfo.value.status_code == 404


# --- 4. ineligible requests offer nothing ------------------------------------

@pytest.mark.parametrize("status", ["uploaded", "approved", "rejected"])
def test_a_request_that_is_no_longer_awaiting_a_file_offers_no_upload(gates, status):
    """Mirrors the existing rule rather than inventing one: client_document_requests serves only
    ('open', 'uploaded') and requests.html hides the action once uploaded — leaving 'open'. An
    approved or rejected request must never offer an upload that would reopen it."""
    principal, _, thread_id, request_id = _thread_with_request(status=status)

    html = _thread_html(principal, thread_id)

    assert "2025 W-2" in html, "the request itself must still be listed with its status"
    assert f"request_id={request_id}" not in html
    assert "Upload for this request" not in html


def test_the_action_is_hidden_when_the_upload_surface_is_switched_off(
        portal_master_on, production_identity_provider):
    """`portal_can` asks the real gate, so the firm-wide upload kill switch hides the action —
    never shown-then-refused."""
    portal_master_on.add("portal.messaging_enabled")        # messaging on, upload deliberately OFF
    principal, _, thread_id, request_id = _thread_with_request()

    html = _thread_html(principal, thread_id)

    assert "2025 W-2" in html
    assert "Upload for this request" not in html


# --- 5. nothing else about the thread changed --------------------------------

def test_a_thread_with_no_linked_request_renders_unchanged(gates):
    _, principal, person_id, household_id = seed_portal_account(seed_staff_user())
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Just a question", body="No documents involved")

    html = _thread_html(principal, thread_id)

    assert "Just a question" in html and "No documents involved" in html
    assert "Linked requests" not in html
    # The portal's own navigation already carries a plain /portal/upload link, so this asserts the
    # absence of the REQUEST-SCOPED action rather than of the path.
    assert "request_id=" not in html
    assert "Upload for this request" not in html


def test_the_conversation_itself_is_untouched(gates):
    """The messages, the reply form and the internal-note exclusion all behave as before."""
    from app.portal.service import staff_send_message

    staff_uid = seed_staff_user()
    principal, _, thread_id, _ = _thread_with_request(staff_uid)
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Visible staff reply")
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="SECRET NOTE", internal_note=True)

    html = _thread_html(principal, thread_id)

    assert "Opening message" in html and "Visible staff reply" in html
    assert "SECRET NOTE" not in html
    assert f'action="/portal/messages/{thread_id}/reply"' in html


def test_no_storage_or_workflow_internals_reach_the_page(gates):
    """The linked-request block shows title, status and due date — never storage or workflow ids."""
    principal, _, thread_id, request_id = _thread_with_request()

    html = _thread_html(principal, thread_id)

    for leaked in ("storage_key", "storage_path", "checksum", "uploaded_document_id",
                   "workflow_instance_id", "requested_by_user_id"):
        assert leaked not in html


def test_the_end_to_end_path_still_works_from_the_thread(gates):
    """Following the link performs the real, existing workflow: a vault document is created and the
    request is marked fulfilled. No attachment row and no canonical document are involved."""
    from sqlalchemy import func

    from app.db import portal_message_attachments, vault_documents

    principal, person_id, thread_id, request_id = _thread_with_request()
    unique = uuid.uuid4().hex[:8]
    with engine.connect() as c:
        attachments_before = c.scalar(select(func.count()).select_from(portal_message_attachments))

    doc_id = portal_vault.upload_document(
        principal, source=io.BytesIO(b"%PDF-1.4 statement"), original_filename=f"w2-{unique}.pdf",
        display_name=f"W-2 {unique}", request_id=request_id)

    with engine.connect() as c:
        assert c.scalar(select(vault_documents.c.status).where(
            vault_documents.c.id == doc_id)) == "uploaded"
        assert c.scalar(select(portal_document_requests.c.status).where(
            portal_document_requests.c.id == request_id)) == "uploaded"
        # The vault path is the ONLY path: no attachment row is created, so the scaffold pointing at
        # canonical `documents` stays exactly as unused as it was before this change.
        assert c.scalar(select(func.count()).select_from(
            portal_message_attachments)) == attachments_before

    # And the thread now shows the request as fulfilled, with the action withdrawn.
    html = _thread_html(principal, thread_id)
    assert "Upload for this request" not in html
