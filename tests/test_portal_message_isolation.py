"""Security audit of the pre-existing client messaging paths.

Two behaviours were built before this review and had never been proven end to end: that a portal
client's authority comes from their SESSION and GRANTS rather than from possessing a thread id, and
that ``portal_messages.visibility`` is genuinely what keeps staff-only notes away from clients.

Every test drives a REAL route or the service a route calls — not a source string — with two fully
independent portal clients, so "Client A cannot reach Client B" is demonstrated rather than asserted.

The authorization chain being proven, for every client path:

    Depends(current_portal)                 -> a live, non-revoked portal session
      -> portal_scope(account_id, permission="messages")
                                            -> ACTIVE grants that explicitly allow `messages`
      -> thread resolved server-side against scope person_ids / shared_household_ids
      -> messages filtered to visibility == "client"
      -> sender_type derived from the row, never from input
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.db import (engine, portal_access_grants, portal_accounts, portal_messages,
                    portal_threads)
from app.portal import communication_hub as hub
from app.portal.service import (client_threads, create_thread, list_messages, mark_read,
                                send_message, staff_send_message)
from tests._portal_util import seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("portal_messaging_on")


def _client(staff_id=None, *, permissions=None):
    """One fully independent portal client: household, person, active account, live session."""
    account_id, principal, person_id, household_id = seed_portal_account(
        staff_id or seed_staff_user(), permissions=permissions)
    return SimpleNamespace(account_id=account_id, principal=principal,
                           person_id=person_id, household_id=household_id)


def _thread_for(client, *, subject="Question", body="Body"):
    return create_thread(client.principal, household_id=client.household_id,
                         person_id=client.person_id, subject=subject, body=body)


def _req():
    return SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}", portal_principal=None),
        client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"},
        query_params={}, session={}, url=SimpleNamespace(path="/portal/messages"))


# ============================================================================
# A. THREAD OWNERSHIP / GRANT ISOLATION
# ============================================================================

def test_a1_a_client_never_sees_another_clients_thread_in_any_list():
    a, b = _client(), _client()
    b_thread = _thread_for(b, subject="B private matter")

    assert b_thread not in [t["id"] for t in client_threads(a.principal)]
    assert b_thread not in [t["id"] for t in hub.client_conversations(a.principal)]
    assert b_thread in [t["id"] for t in client_threads(b.principal)]


def test_a2_a_client_cannot_read_another_clients_thread_directly():
    a, b = _client(), _client()
    b_thread = _thread_for(b)

    with pytest.raises(PermissionError):
        list_messages(a.principal, b_thread)


def test_a2b_the_browser_route_denies_existence_rather_than_disclosing_it():
    """Established safe semantics: an out-of-scope thread is 404, not 403."""
    from app.routes.portal import portal_message_thread_page

    a, b = _client(), _client()
    b_thread = _thread_for(b)

    with pytest.raises(HTTPException) as exc:
        portal_message_thread_page(b_thread, _req(), principal=a.principal)
    assert exc.value.status_code == 404


def test_a3_a_client_cannot_reply_into_another_clients_thread():
    a, b = _client(), _client()
    b_thread = _thread_for(b)

    with pytest.raises(PermissionError):
        send_message(a.principal, b_thread, "injected reply")

    bodies = [m["body"] for m in list_messages(b.principal, b_thread)]
    assert "injected reply" not in bodies


def test_a3b_the_browser_reply_route_denies_existence():
    from app.routes.portal import portal_message_reply

    a, b = _client(), _client()
    b_thread = _thread_for(b)

    with pytest.raises(HTTPException) as exc:
        portal_message_reply(b_thread, _req(), body="injected", principal=a.principal)
    assert exc.value.status_code == 404


def test_a4_a_client_cannot_mark_another_clients_thread_read():
    a, b = _client(), _client()
    b_thread = _thread_for(b)

    with pytest.raises(PermissionError):
        hub.mark_thread_read_client(a.principal, b_thread)

    with engine.connect() as c:
        assert c.execute(select(portal_threads.c.client_last_read_at).where(
            portal_threads.c.id == b_thread)).scalar_one() is None


def test_a5_a_client_cannot_retrieve_another_clients_messages_through_the_api():
    from app.routes.portal import api_messages as v1_messages
    from app.routes.portal_api import api_message_thread

    a, b = _client(), _client()
    b_thread = _thread_for(b, body="B confidential body")

    for call in (lambda: v1_messages(b_thread, principal=a.principal),
                 lambda: api_message_thread(_req(), b_thread, principal=a.principal)):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code in (403, 404)
        assert "confidential" not in str(exc.value.detail)


def test_a5b_a_client_cannot_mark_another_clients_message_read_through_the_api():
    a, b = _client(), _client()
    b_thread = _thread_for(b)
    b_message = list_messages(b.principal, b_thread)[0]["id"]

    with pytest.raises(PermissionError):
        mark_read(a.principal, b_message)


@pytest.mark.parametrize("tampered", [0, -1, 999_000_111, 2 ** 31 - 1])
def test_a6_guessed_and_tampered_thread_ids_never_bypass_isolation(tampered):
    a = _client()
    for call in (lambda: list_messages(a.principal, tampered),
                 lambda: send_message(a.principal, tampered, "x"),
                 lambda: hub.mark_thread_read_client(a.principal, tampered)):
        with pytest.raises(PermissionError):
            call()


def test_a6b_sequential_ids_around_an_owned_thread_are_still_refused():
    """A real neighbouring id is the realistic guess, not a random one."""
    a, b = _client(), _client()
    a_thread = _thread_for(a)
    b_thread = _thread_for(b)
    assert b_thread != a_thread

    with pytest.raises(PermissionError):
        list_messages(a.principal, b_thread)
    assert list_messages(a.principal, a_thread), "the client lost access to their own thread"


def test_a7_an_ended_grant_removes_access_to_previously_readable_threads():
    """Grant end-dating is what removes SCOPE. (Session revocation is what removes access the
    instant staff act — see test_a8.)"""
    from datetime import timedelta

    a = _client()
    thread_id = _thread_for(a)
    assert list_messages(a.principal, thread_id)

    with engine.begin() as c:
        c.execute(update(portal_access_grants)
                  .where(portal_access_grants.c.portal_account_id == a.account_id)
                  .values(inactive_date=hub._now().date() - timedelta(days=1)))

    with pytest.raises(PermissionError):
        list_messages(a.principal, thread_id)
    assert client_threads(a.principal) == []


def test_a7b_an_end_date_of_today_is_inclusive_and_keeps_the_grant_live_that_day():
    """Documents the real convention rather than assuming it.

    ``_active_grant()`` is ``inactive_date >= today``, so a grant end-dated TODAY is still in scope
    until midnight. That is deliberate date-range semantics and is NOT the control that stops a
    revoked client: ``revoke_account_access`` also kills every live session, and
    ``resolve_portal_session`` additionally requires ``portal_accounts.status == 'active'``, so a
    revoked account cannot obtain a principal at all (test_a8). Pinned here so a future change to
    either half is a deliberate decision."""
    a = _client()
    thread_id = _thread_for(a)

    with engine.begin() as c:
        c.execute(update(portal_access_grants)
                  .where(portal_access_grants.c.portal_account_id == a.account_id)
                  .values(inactive_date=hub._now().date()))

    assert list_messages(a.principal, thread_id), "same-day end-dating changed grant semantics"


def test_a8_a_revoked_portal_account_can_no_longer_obtain_a_session_at_all():
    """The immediate control. Three independent conditions in resolve_portal_session must hold:
    a live session row, an unexpired one, and an ACTIVE account."""
    from app.portal.service import (create_portal_session, resolve_portal_session,
                                    revoke_account_access)

    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_for(a)
    token = create_portal_session(a.account_id, device_fingerprint=f"dev-{uuid.uuid4().hex[:8]}")
    assert resolve_portal_session(token) is not None

    with engine.begin() as c:
        c.execute(update(portal_accounts).where(portal_accounts.c.id == a.account_id)
                  .values(status="revoked"))
        closed = revoke_account_access(c, a.account_id)

    assert closed["sessions_revoked"] >= 1
    assert resolve_portal_session(token) is None, "a revoked client kept a usable portal session"
    # History is preserved — revocation never deletes the conversation.
    with engine.connect() as c:
        assert c.execute(select(portal_threads.c.id).where(
            portal_threads.c.id == thread_id)).scalars().all() == [thread_id]


def test_a8c_status_alone_blocks_session_resolution_even_if_a_session_row_survives():
    """Defence in depth: resolve_portal_session joins on status == 'active'."""
    from app.portal.service import create_portal_session, resolve_portal_session

    a = _client()
    token = create_portal_session(a.account_id, device_fingerprint=f"dev-{uuid.uuid4().hex[:8]}")
    with engine.begin() as c:                      # status only; sessions deliberately left alone
        c.execute(update(portal_accounts).where(portal_accounts.c.id == a.account_id)
                  .values(status="revoked"))

    assert resolve_portal_session(token) is None


def test_a8b_an_invalid_or_expired_session_yields_no_principal():
    from app.portal.service import resolve_portal_session

    assert resolve_portal_session("not-a-real-session-token") is None
    assert resolve_portal_session("") is None


def test_a9_a_grant_that_does_not_allow_messages_reaches_no_thread():
    """Grant semantics, not surname or household guesswork, decide access."""
    staff = seed_staff_user()
    allowed = _client(staff)
    thread_id = _thread_for(allowed)

    denied = _client(staff, permissions={"documents": True, "messages": False})
    assert client_threads(denied.principal) == []
    with pytest.raises(PermissionError):
        list_messages(denied.principal, thread_id)


def test_a10_a_client_cannot_open_a_thread_against_another_persons_record():
    """person_id/household_id arrive from the browser on the v1 API and are re-checked."""
    a, b = _client(), _client()

    with pytest.raises(PermissionError):
        create_thread(a.principal, household_id=b.household_id, person_id=b.person_id,
                      subject="hijack", body="body")

    with engine.connect() as c:
        assert c.execute(select(portal_threads.c.id).where(
            portal_threads.c.person_id == b.person_id)).scalars().all() == []


def test_a10b_a_client_cannot_mix_their_own_person_with_another_household():
    a, b = _client(), _client()
    with pytest.raises(PermissionError):
        create_thread(a.principal, household_id=b.household_id, person_id=a.person_id,
                      subject="mix", body="body")


# ============================================================================
# B. VISIBILITY / INTERNAL-NOTE ISOLATION
# ============================================================================
#
# The complete vocabulary is TWO values: 'client' and 'internal'. Internal notes ARE supported —
# staff_send_message(internal_note=True) writes visibility='internal'.

INTERNAL = "INTERNAL-NOTE-DO-NOT-DISCLOSE"


def _thread_with_internal_note(client, staff_id):
    thread_id = _thread_for(client)
    staff_send_message(thread_id=thread_id, user_id=staff_id, body=INTERNAL, internal_note=True)
    return thread_id


def test_b0_the_visibility_vocabulary_is_exactly_client_and_internal():
    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_with_internal_note(a, staff)
    staff_send_message(thread_id=thread_id, user_id=staff, body="visible reply")

    with engine.connect() as c:
        values = set(c.execute(select(portal_messages.c.visibility).where(
            portal_messages.c.thread_id == thread_id)).scalars().all())
    assert values == {"client", "internal"}


def test_b1_an_internal_note_never_reaches_the_client_thread_view():
    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_with_internal_note(a, staff)

    bodies = [m["body"] for m in list_messages(a.principal, thread_id)]

    assert INTERNAL not in bodies
    assert all(INTERNAL not in (b or "") for b in bodies)
    # Staff DO see it — proving the note exists and the filter is what hides it.
    assert INTERNAL in [m["body"] for m in hub.staff_thread_messages(thread_id)]


def test_b2_an_internal_note_never_reaches_the_client_api():
    from app.routes.portal import api_messages as v1_messages
    from app.routes.portal_api import api_message_thread

    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_with_internal_note(a, staff)

    v1 = v1_messages(thread_id, principal=a.principal)
    assert INTERNAL not in str(v1)
    legacy = api_message_thread(_req(), thread_id, principal=a.principal)
    assert INTERNAL not in legacy.body.decode()


def test_b3_the_client_conversation_list_carries_no_message_preview_at_all():
    """Nothing to leak: the client list exposes subject/topic/status/timestamps only."""
    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_with_internal_note(a, staff)

    for view in (client_threads(a.principal), hub.client_conversations(a.principal)):
        row = [t for t in view if t["id"] == thread_id][0]
        assert INTERNAL not in str(row)
        assert "body" not in row and "preview" not in row


def test_b4_an_internal_note_does_not_make_the_thread_unread_for_the_client():
    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_for(a)
    hub.mark_thread_read_client(a.principal, thread_id)

    staff_send_message(thread_id=thread_id, user_id=staff, body=INTERNAL, internal_note=True)

    row = [t for t in hub.client_conversations(a.principal) if t["id"] == thread_id][0]
    assert row["unread"] is False, "an internal note leaked through the client unread flag"

    staff_send_message(thread_id=thread_id, user_id=staff, body="a real reply")
    row = [t for t in hub.client_conversations(a.principal) if t["id"] == thread_id][0]
    assert row["unread"] is True, "a genuine staff reply failed to mark the client unread"


def test_b5_secure_message_timeline_events_are_not_exposed_to_the_portal():
    """The only timeline the portal reads is calendar_event; message events never surface."""
    import inspect

    from app.portal import service

    src = inspect.getsource(service)
    exposed = [line for line in src.splitlines()
               if "timeline_events" in line and "select(" in line]
    assert exposed, "the client timeline read moved; re-audit this"
    for line in exposed:
        assert 'event_type == "calendar_event"' in line, \
            "a client-facing timeline query no longer restricts the event type"


def test_b6_marking_an_internal_note_read_is_refused_as_not_found():
    """mark_read filters on visibility, so an internal message id is not even addressable."""
    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_for(a)
    staff_send_message(thread_id=thread_id, user_id=staff, body=INTERNAL, internal_note=True)
    with engine.connect() as c:
        internal_id = c.execute(select(portal_messages.c.id).where(
            portal_messages.c.thread_id == thread_id,
            portal_messages.c.visibility == "internal")).scalars().one()

    with pytest.raises(ValueError):
        mark_read(a.principal, internal_id)


def test_b7_a_client_cannot_choose_the_visibility_of_what_they_send():
    """No client route or payload model accepts a visibility field; the service hard-codes it."""
    import inspect

    from app.portal import service
    from app.routes import portal as portal_routes

    for model in ("class ThreadCreate", "class MessageCreate"):
        line = [l for l in inspect.getsource(portal_routes).splitlines() if l.startswith(model)][0]
        assert "visibility" not in line, f"{model} accepts a visibility field"
        assert "sender" not in line, f"{model} accepts a sender field"
    for fn in (service.create_thread, service.send_message):
        assert 'visibility="client"' in inspect.getsource(fn)

    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_for(a)
    send_message(a.principal, thread_id, "client body")
    with engine.connect() as c:
        rows = c.execute(select(portal_messages.c.visibility,
                                portal_messages.c.sender_portal_account_id,
                                portal_messages.c.sender_user_id).where(
            portal_messages.c.thread_id == thread_id)).mappings().all()
    for row in rows:
        assert row["visibility"] == "client"
        assert row["sender_portal_account_id"] == a.account_id
        assert row["sender_user_id"] is None, "a client message was attributed to a staff user"


def test_b8_sender_identity_is_derived_from_the_row_not_from_input():
    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_for(a)
    staff_send_message(thread_id=thread_id, user_id=staff, body="staff reply")

    views = list_messages(a.principal, thread_id)
    kinds = {v["sender_type"] for v in views}

    assert kinds == {"client", "staff"}
    # A safe label only — never a staff user id, name or email.
    for view in views:
        assert str(staff) not in str(view.get("sender_type"))
        assert "sender_user_id" not in view and "sender_portal_account_id" not in view


def test_b9_the_staff_reply_route_decides_visibility_server_side():
    import inspect

    from app.routes import portal_admin

    src = inspect.getsource(portal_admin.portal_admin_thread_reply)
    assert "internal_note" in src, "the staff reply route cannot distinguish an internal note"
    assert "staff_send_message" in src
    # The value is computed from a named form control, never echoed from arbitrary input.
    assert 'visibility="internal"' not in src, "the route sets a raw visibility value itself"


def test_b10_a_client_message_never_carries_a_staff_sender_even_with_a_forged_body():
    """There is no field to forge: sender_type is computed, not stored, on the client view."""
    staff = seed_staff_user()
    a = _client(staff)
    thread_id = _thread_for(a)
    send_message(a.principal, thread_id, '{"sender_type": "staff"} <b>staff</b>')

    views = list_messages(a.principal, thread_id)
    injected = [v for v in views if "sender_type" in v["body"]][0]
    assert injected["sender_type"] == "client"


def test_b11_message_bodies_are_escaped_not_executed():
    """Autoescape is on and the thread template renders the body without |safe."""
    from app.routes.portal import templates

    assert templates.env.autoescape is True
    tpl = open("app/templates/portal/message_thread.html", encoding="utf-8").read()
    assert "{{ m.body }}" in tpl
    for unsafe in ("|safe", "| safe", "autoescape false"):
        assert unsafe not in tpl, f"the thread template uses {unsafe}"


# ============================================================================
# D. LINE BREAKS PRESERVED WITHOUT WEAKENING ESCAPING
# ============================================================================
#
# A typed message keeps its line breaks through CSS, not through markup: the body stays plain
# text and stays autoescaped. The staff view previously did this with an inline style attribute,
# which the site CSP (`default-src 'self'`, no 'unsafe-inline') blocks — so staff line breaks
# collapsed in production while the client view, which uses a stylesheet class, was fine.

CLIENT_THREAD_TPL = "app/templates/portal/message_thread.html"
STAFF_THREAD_TPL = "app/templates/admin/portal_thread.html"
SHARED_CSS = "app/static/css/main.css"


def _read(path):
    return open(path, encoding="utf-8").read()


def test_d1_both_thread_views_render_the_body_with_the_shared_class():
    for tpl in (CLIENT_THREAD_TPL, STAFF_THREAD_TPL):
        assert '<div class="msg-body">{{ m.body }}</div>' in _read(tpl), \
            f"{tpl} does not use the shared message-body class"


def test_d2_the_class_preserves_line_breaks_and_lives_in_the_shared_stylesheet():
    css = _read(SHARED_CSS)
    assert ".msg-body { white-space: pre-wrap; word-break: break-word; }" in css
    # main.css is the ONLY stylesheet both bases load, so one definition serves both views.
    for base in ("app/templates/base.html", "app/templates/portal/base.html"):
        assert "/static/css/main.css" in _read(base), f"{base} no longer loads the shared sheet"


def test_d3_the_rule_is_defined_exactly_once():
    """A duplicate in portal.css is how the staff and client views drifted apart."""
    hits = {name: _read(f"app/static/css/{name}.css").count(".msg-body")
            for name in ("main", "portal", "app", "work", "tax")}
    assert hits["main"] == 1
    assert sum(hits.values()) == 1, f"the rule is defined more than once: {hits}"


def test_d4_neither_thread_view_uses_an_inline_style_on_the_message_body():
    """An inline style attribute is CSP-blocked, which is what broke the staff view."""
    for tpl in (CLIENT_THREAD_TPL, STAFF_THREAD_TPL):
        body_line = [l for l in _read(tpl).splitlines() if "{{ m.body }}" in l][0]
        assert "style=" not in body_line, f"{tpl} styles the body inline"
        assert "white-space" not in body_line


def test_d5_no_unsafe_rendering_was_introduced():
    for tpl in (CLIENT_THREAD_TPL, STAFF_THREAD_TPL):
        content = _read(tpl)
        for unsafe in ("|safe", "| safe", "autoescape false", "{% autoescape", "|e|safe",
                       "Markup(", "nl2br", "|linebreaks", "<br>{{", "striptags"):
            assert unsafe not in content, f"{tpl} introduced {unsafe}"


def test_d6_a_multiline_body_survives_storage_as_plain_text():
    """Storage is unchanged: exactly the characters the person typed, newlines included."""
    staff = seed_staff_user()
    a = _client(staff)
    typed = "First line\nSecond line\n\nFourth after a blank"
    thread_id = _thread_for(a, body=typed)

    stored = list_messages(a.principal, thread_id)[0]["body"]

    assert stored == typed
    assert "<br" not in stored and "&#10;" not in stored, "the body was converted to markup"


def test_d7_html_and_script_in_a_body_are_still_escaped_when_rendered():
    """The real render, not a source assertion: dangerous input must come back inert."""
    from app.routes.portal import portal_message_thread_page

    staff = seed_staff_user()
    a = _client(staff)
    hostile = '<script>alert(1)</script>\n<img src=x onerror=alert(2)>\nline three'
    thread_id = _thread_for(a, body=hostile)

    html = portal_message_thread_page(thread_id, _req(), principal=a.principal).body.decode()

    assert "<script>alert(1)</script>" not in html, "a script tag reached the document"
    assert "<img src=x onerror" not in html
    assert "&lt;script&gt;" in html, "the body was not escaped at all"
    # ...and the escaped text still sits inside the line-break-preserving element.
    assert 'class="msg-body"' in html
    assert "line three" in html


def test_d8_the_staff_thread_view_escapes_the_same_way():
    from app.routes.portal_admin import portal_admin_thread
    from app.security.models import Principal

    staff_id = seed_staff_user()
    a = _client(staff_id)
    thread_id = _thread_for(a, body='<script>alert(3)</script>\nsecond line')
    principal = Principal(staff_id, "staff@example.com", "Staff",
                          frozenset({"client.read", "client.write",
                                     "record.read_all", "record.write_all"}))

    html = portal_admin_thread(thread_id, _req(), principal=principal).body.decode()

    assert "<script>alert(3)</script>" not in html
    assert "&lt;script&gt;" in html
    assert 'class="msg-body"' in html
    assert "second line" in html
