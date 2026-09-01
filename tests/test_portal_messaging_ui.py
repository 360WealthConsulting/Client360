"""Secure messaging UI — client thread browse/compose/reply + the staff reply side that makes it a
real two-way conversation. Client routes go through the scoped portal services; staff routes enforce
BOTH capability (client.read/write) and record scope on the thread's person/household.
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

def test_messages_page_lists_threads_and_compose_form():
    _, principal, pid, hid = seed_portal_account(seed_staff_user())
    create_thread(principal, household_id=hid, person_id=pid, subject="Tax question", body="Hi")
    html = render(portal_messages_page(fake_request("/portal/messages"), principal))
    assert "Tax question" in html
    assert 'action="/portal/messages/new"' in html


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
    """Gated on the DEDICATED Messages capabilities (msgcap01), not client.read/client.write: eleven
    roles hold those, and reading a client's correspondence is a narrower authority than reading
    their record. Viewing and replying stay separately gated."""
    src = inspect.getsource(portal_admin_thread_reply)
    assert 'require_capability("communications.message.write")' in src
    assert 'require_capability("communications.message.read")' in inspect.getsource(portal_admin_thread)
    # The old over-broad gate must not linger on either handler.
    assert 'require_capability("client.write")' not in src
    assert 'require_capability("client.read")' not in inspect.getsource(portal_admin_thread)


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
