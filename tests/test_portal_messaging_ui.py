"""Secure messaging UI — client thread browse/compose/reply + the staff reply side that makes it a
real two-way conversation. Client routes go through the scoped portal services; staff routes enforce
BOTH capability (communications.message.read/write) and record scope on the thread's person/household.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import audit_events, engine, portal_messages
from app.portal.service import create_thread, list_messages, staff_send_message
from app.routes.portal import (
    portal_message_reply,
    portal_message_thread_page,
    portal_messages_new,
    portal_messages_page,
)
from app.routes.portal_admin import (
    portal_admin_thread,
    portal_admin_thread_reply,
    portal_admin_threads,
)
from app.security.models import Principal
from tests._portal_util import fake_request, render, seed_portal_account, seed_staff_user


def _msg_count(thread_id, *, visibility=None):
    with engine.connect() as c:
        q = select(func.count()).select_from(portal_messages).where(
            portal_messages.c.thread_id == thread_id)
        if visibility:
            q = q.where(portal_messages.c.visibility == visibility)
        return c.scalar(q)


# --- client side -------------------------------------------------------------

def test_messages_page_lists_threads_and_compose_form(portal_documents_upload_on):
    """The Vault shortcut is gated, so upload must be switched ON to assert it is offered."""
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    create_thread(principal, household_id=hid, person_id=pid, subject="Tax question", body="Hi")
    html = render(portal_messages_page(fake_request("/portal/messages"), principal))
    assert "Tax question" in html
    assert 'action="/portal/messages/new"' in html
    assert 'class="conversation-shell portal-conversation-shell"' in html
    assert "Select a conversation" in html
    assert "Upload to Vault" in html


def test_new_thread_prg_creates_thread_on_own_record():
    _, principal, pid, _ = seed_portal_account(seed_staff_user())
    resp = portal_messages_new(request=fake_request("/portal/messages/new", "POST"),
                               subject="Question", body="My question", principal=principal)
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith("/portal/messages/") and "notice=" in loc
    thread_id = int(loc.split("/portal/messages/")[1].split("?")[0])
    # Thread is readable by its owner and holds the opening message.
    assert _msg_count(thread_id) == 1


def test_thread_page_shows_client_messages_but_not_internal_notes():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    staff_uid = seed_staff_user()
    thread_id = create_thread(principal, household_id=hid, person_id=pid, subject="Re: docs",
                              body="Client opening message")
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="SECRET INTERNAL NOTE",
                       internal_note=True)
    staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Visible staff reply")
    html = render(portal_message_thread_page(thread_id, fake_request(f"/portal/messages/{thread_id}"),
                                             principal))
    assert "Client opening message" in html
    assert "Visible staff reply" in html
    assert "SECRET INTERNAL NOTE" not in html          # internal notes never reach the client
    assert "conversation-shell portal-conversation-shell has-selection" in html
    assert "Write a reply" in html


def test_reply_prg_appends_message():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    thread_id = create_thread(principal, household_id=hid, person_id=pid, subject="S", body="B")
    resp = portal_message_reply(thread_id, request=fake_request(f"/portal/messages/{thread_id}/reply", "POST"),
                                body="A follow-up", principal=principal)
    assert resp.status_code == 303
    assert _msg_count(thread_id) == 2


def test_out_of_scope_thread_is_404_for_other_client():
    _, alice, _, _ = seed_portal_account(seed_staff_user())
    _, bob, bob_pid, bob_hid = seed_portal_account(seed_staff_user())
    bob_thread = create_thread(bob, household_id=bob_hid, person_id=bob_pid, subject="Bob", body="Private")
    with pytest.raises(HTTPException) as ei:
        portal_message_thread_page(bob_thread, fake_request(f"/portal/messages/{bob_thread}"), alice)
    assert ei.value.status_code == 404                 # existence never disclosed
    with pytest.raises(HTTPException) as ei:
        portal_message_reply(bob_thread, request=fake_request("/x", "POST"), body="hi", principal=alice)
    assert ei.value.status_code == 404


# --- staff side (the reply half of the two-way conversation) -----------------

def _staff_principal(uid, caps):
    return Principal(uid, "staff@e.test", "Staff", frozenset(caps))


def test_staff_can_view_and_reply_when_in_scope():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    thread_id = create_thread(principal, household_id=hid, person_id=pid, subject="Help", body="Hi")
    staff_uid = seed_staff_user()
    staff = _staff_principal(staff_uid, {"client.read", "client.write",
                                         "record.read_all", "record.write_all"})
    # View renders (record-scoped) and includes the client's message.
    html = render(portal_admin_thread(thread_id, fake_request(
        f"/admin/client-portal/threads/{thread_id}", state_principal=staff), staff))
    assert "Hi" in html
    assert "conversation-shell staff-conversation-shell has-selection" in html
    assert "Client messages" in html

    # Reply to the client (visible) and add an internal note (staff-only).
    r1 = portal_admin_thread_reply(thread_id, request=fake_request("/x", "POST"),
                                   body="Staff answer", internal_note=None, principal=staff)
    assert r1.status_code == 303 and "notice=" in r1.headers["location"]
    r2 = portal_admin_thread_reply(thread_id, request=fake_request("/x", "POST"),
                                   body="internal", internal_note="1", principal=staff)
    assert r2.status_code == 303
    # The client sees only the client-visible messages (opening + staff answer), not the note.
    client_visible = [m["body"] for m in list_messages(principal, thread_id)]
    assert "Staff answer" in client_visible and "internal" not in client_visible
    assert _msg_count(thread_id, visibility="internal") == 1


def test_staff_reply_denied_without_record_scope():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    thread_id = create_thread(principal, household_id=hid, person_id=pid, subject="S", body="B")
    # client.read/write but NO record.* and no assignment → out of record scope.
    outsider = _staff_principal(seed_staff_user(), {"client.read", "client.write"})
    with pytest.raises(HTTPException) as ei:
        portal_admin_thread(thread_id, fake_request("/x", state_principal=outsider), outsider)
    assert ei.value.status_code == 404                 # view: existence hidden
    with pytest.raises(HTTPException) as ei:
        portal_admin_thread_reply(thread_id, request=fake_request("/x", "POST"), body="hi",
                                  internal_note=None, principal=outsider)
    assert ei.value.status_code == 403                 # reply: explicit scope denial
    assert _msg_count(thread_id) == 1                  # nothing was written


def test_staff_reply_route_is_capability_gated():
    """The thread handlers declare the dedicated Messages capabilities — the SAME ones the
    ``^/admin/client-portal/threads`` middleware rule enforces. They previously declared
    client.read/client.write, so the door and the handler asked for different authorities.
    tests/test_messages_handler_capability.py pins the full contract."""
    src = inspect.getsource(portal_admin_thread_reply)
    assert 'require_capability("communications.message.write")' in src
    assert 'require_capability("communications.message.read")' in inspect.getsource(portal_admin_thread)


def test_staff_reply_is_audited():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    thread_id = create_thread(principal, household_id=hid, person_id=pid, subject="S", body="B")
    staff = _staff_principal(seed_staff_user(), {"client.read", "client.write",
                                                 "record.read_all", "record.write_all"})
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "portal.message.sent"))
    portal_admin_thread_reply(thread_id, request=fake_request("/x", "POST"), body="Answer",
                              internal_note=None, principal=staff)
    with engine.connect() as c:
        after = c.scalar(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "portal.message.sent"))
    assert after == before + 1


# --- the three-pane workspace: empty states, gates and the narrow-screen contract -------------
#
# The workspace replaced a stacked list with a channels/list/thread shell. Everything below pins a
# property the rewrite is capable of losing silently, because none of it is visible in a diff:
# the Vault shortcut is an ENTITLEMENT (not decoration), the two empty states say different things,
# and the narrow-screen layout is driven entirely by two class names the templates must emit.

def test_the_vault_shortcut_is_absent_when_upload_is_switched_off(portal_messaging_on):
    """The shortcut is an upload entitlement, so a client without it must not be offered one.

    The paired assertion — that it IS offered when upload is on — lives in
    test_messages_page_lists_threads_and_compose_form."""
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    create_thread(principal, household_id=hid, person_id=pid, subject="Tax question", body="Hi")
    html = render(portal_messages_page(fake_request("/portal/messages"), principal))
    assert "Upload to Vault" not in html
    assert 'href="/portal/upload"' not in html
    assert "Tax question" in html                      # the rest of the workspace still renders


def test_the_client_empty_state_differs_from_a_selected_conversation():
    """No threads is a distinct fact from "you have threads, none is open"."""
    _, principal, _, _ = seed_portal_account(seed_staff_user())
    html = render(portal_messages_page(fake_request("/portal/messages"), principal))
    assert "No conversations yet" in html
    assert "Select a conversation" in html             # the thread pane still states its own emptiness


def test_the_staff_inbox_keeps_every_documented_filter():
    """The channel rail is the only route to these filters now that the chip bar is gone.

    Deliberately no empty-state assertion: a record.read_all principal sees every thread in the
    database, including ones other tests seeded, so "empty" is not a state this test can create.
    The client side covers the empty state on an account it owns outright."""
    staff = _staff_principal(seed_staff_user(), {"communications.message.read", "client.read",
                                                 "record.read_all"})
    html = render(portal_admin_threads(
        fake_request("/admin/client-portal/threads", state_principal=staff), principal=staff))
    for target in ("?filter=unread", "?filter=mine", "?filter=unassigned",
                   "?status=open", "?status=resolved"):
        assert target in html, f"the staff inbox lost the {target} filter"
    assert 'aria-label="Matching clients"' in html     # the typeahead listbox stays named
    assert "The client reads this in their 360Plus portal." in html


def test_a_selected_thread_carries_the_narrow_screen_affordances():
    """`has-selection` and the back link are what the <=800px rules key off: without them a phone
    shows the list and the thread stacked, with no way back to the list."""
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    thread_id = create_thread(principal, household_id=hid, person_id=pid, subject="S", body="B")
    staff = _staff_principal(seed_staff_user(), {"client.read", "client.write",
                                                 "record.read_all", "record.write_all"})
    client_html = render(portal_message_thread_page(
        thread_id, fake_request(f"/portal/messages/{thread_id}"), principal))
    staff_html = render(portal_admin_thread(
        thread_id, fake_request("/x", state_principal=staff), staff))
    for html in (client_html, staff_html):
        assert "has-selection" in html
        assert 'class="conversation-mobile-back"' in html


def test_the_paperclip_stays_an_attachment_indicator_not_a_button_label():
    """📎 marks a message that HAS a file. Reusing it on the compose control made every thread
    look like it carried one — see tests/test_message_attachments.py."""
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    thread_id = create_thread(principal, household_id=hid, person_id=pid, subject="S", body="B")
    html = render(portal_message_thread_page(
        thread_id, fake_request(f"/portal/messages/{thread_id}"), principal))
    assert "📎" not in html
