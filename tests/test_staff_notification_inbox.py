"""The staff notification inbox — `/notifications` (Batch 2b).

Batch 2 began recording staff notifications in the canonical `notifications` ledger, but nothing
displayed them: clients had `/portal/notifications` and staff had nothing at all. This surface closes
that with a recipient-scoped read model over the SAME ledger — no table, no migration, no channel, no
provider, no transport.

WHY NOT `/admin/notifications`. The generic `^/admin` rule demands `identity.manage`, so a personal
inbox there would have been invisible to every non-administrator. `/notifications` matches no RULES
pattern (like `/communications` and `/engagement`), so the route gates itself and `^/admin` is
untouched. Both facts are pinned below, because either could regress on its own.

WHAT IS PINNED HERE:

  1. a staff user sees ONLY their own notifications, and another user's are absent;
  2. read/unread state is projected correctly, and marking read affects one user's row only;
  3. a secure-message notification links to its real thread;
  4. an arbitrary or external metadata URL is NEVER rendered as a destination — the allowlist fails
     closed, which is what stops a shared ledger becoming an open-redirect surface;
  5. the raw metadata blob never reaches the projection;
  6. the route is capability-gated, and `^/admin` did not widen;
  7. Batch 2's secure-message notification creation still works end to end.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import engine
from app.routes import notifications as route
from app.security.middleware import RULES
from app.security.models import Principal
from app.services import notification_inbox as inbox
from app.services.notifications import (
    _notifications_table,
    list_notifications,
    record_notification,
    unread_notification_count,
)
from tests._portal_util import seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("portal_messaging_on", "production_identity_provider")

CAPS = frozenset({"communications.message.read", "communications.message.write",
                  "client.read", "client.write", "record.read_all", "record.write_all"})


def _principal(uid=None, caps=CAPS):
    return Principal(uid or seed_staff_user(), "staff@example.com", "Staff", frozenset(caps))


def _seed(principal, *, title="New secure message from Ada", link=None, thread_id=None,
          notification_type="portal.secure_message", body=None):
    """One ledger row addressed at a staff principal, with a unique source so it never dedupes."""
    metadata = {"thread_id": thread_id} if thread_id else {}
    if link is not None:
        metadata["link"] = link
    return record_notification(
        notification_type=notification_type, recipient_type="user",
        recipient_ref=str(principal.user_id), title=title, body=body,
        source_ref=f"test-{uuid.uuid4()}", metadata=metadata)


def _fake_request(path="/notifications", params=None):
    from types import SimpleNamespace
    return SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}"),
        query_params=params or {}, session={}, headers={}, url=SimpleNamespace(path=path),
        client=SimpleNamespace(host="127.0.0.1"), scope={"type": "http"})


def _rows(principal):
    return inbox.staff_notifications(principal)["rows"]


# --- 1 + 2. one user's own notifications, and nobody else's -------------------

def test_a_staff_user_sees_only_their_own_notifications():
    mine, theirs = _principal(), _principal()
    a = _seed(mine, title="Mine")
    b = _seed(theirs, title="Theirs")

    ids = [n["id"] for n in _rows(mine)]
    assert a.id in ids
    assert b.id not in ids, "another user's notification reached this inbox"
    assert "Theirs" not in [n["title"] for n in _rows(mine)]


def test_another_users_notifications_are_not_reachable_by_id():
    """Even knowing the id: the recipient is part of every WHERE clause."""
    mine, theirs = _principal(), _principal()
    other = _seed(theirs, title="Theirs")

    assert inbox.mark_read(mine, other.id) is False
    with engine.connect() as c:
        n = _notifications_table()
        assert c.scalar(select(n.c.read_at).where(n.c.id == other.id)) is None


def test_the_recipient_reference_is_the_staff_user_id():
    """The mapping Batch 2 writes and this reads must be the same one."""
    p = _principal()
    assert inbox.staff_recipient_ref(p) == str(p.user_id)
    assert inbox.STAFF_RECIPIENT_TYPE == "user"


# --- 3. read/unread state and mark-read --------------------------------------

def test_unread_state_is_projected_and_counted():
    p = _principal()
    first, second = _seed(p, title="One"), _seed(p, title="Two")

    result = inbox.staff_notifications(p)
    assert result["unread_count"] == 2
    assert all(n["unread"] and n["read_at"] is None for n in result["rows"])

    assert inbox.mark_read(p, first.id) is True

    result = inbox.staff_notifications(p)
    assert result["unread_count"] == 1
    by_id = {n["id"]: n for n in result["rows"]}
    assert by_id[first.id]["unread"] is False and by_id[first.id]["read_at"] is not None
    assert by_id[second.id]["unread"] is True


def test_marking_read_is_idempotent():
    p = _principal()
    n = _seed(p)
    assert inbox.mark_read(p, n.id) is True
    assert inbox.mark_read(p, n.id) is False, "an already-read row must not be touched again"


def test_mark_all_read_affects_only_the_caller():
    mine, theirs = _principal(), _principal()
    _seed(mine, title="a"), _seed(mine, title="b")
    other = _seed(theirs, title="theirs")

    assert inbox.mark_all_read(mine) == 2
    assert unread_notification_count(recipient_type="user", recipient_ref=str(mine.user_id)) == 0
    assert unread_notification_count(recipient_type="user", recipient_ref=str(theirs.user_id)) >= 1
    with engine.connect() as c:
        n = _notifications_table()
        assert c.scalar(select(n.c.read_at).where(n.c.id == other.id)) is None


def test_the_unread_filter_returns_only_unread_rows():
    p = _principal()
    read_one, unread_one = _seed(p, title="read"), _seed(p, title="unread")
    inbox.mark_read(p, read_one.id)

    ids = [n["id"] for n in inbox.staff_notifications(p, unread_only=True)["rows"]]
    assert unread_one.id in ids and read_one.id not in ids


# --- 4. links: the real thread, and nothing else ------------------------------

def test_a_secure_message_notification_links_to_its_thread():
    p = _principal()
    n = _seed(p, link="/admin/client-portal/threads/4242", thread_id=4242)

    row = next(r for r in _rows(p) if r["id"] == n.id)
    assert row["link"] == "/admin/client-portal/threads/4242"
    assert row["label"] == "Secure message"


@pytest.mark.parametrize("hostile", [
    "https://evil.example/steal",           # absolute URL
    "http://evil.example",                  # absolute URL, other scheme
    "//evil.example/steal",                 # protocol-relative — the classic open-redirect payload
    "javascript:alert(1)",                  # scheme, no slash
    "/admin/client-portal/threads/4242?next=https://evil.example",   # query smuggling
    "/admin/client-portal/threads/../../../etc/passwd",              # traversal
    "/admin/client-portal/threads/4242 ",                            # trailing whitespace
    "/admin/client-portal/threads/abc",     # right shape, not a real thread id
    "/some/unlisted/internal/path",         # internal but not an allow-listed destination
    "\\\\evil.example\\share",              # UNC / backslash
    "/admin/client-portal/threads/4242\nSet-Cookie: x=1",            # header injection attempt
    "",
    None,
    12345,
])
def test_an_unsafe_or_unknown_destination_is_never_rendered_as_a_link(hostile):
    """The ledger is shared: any producer can write metadata. A link is rendered ONLY when it is a
    safe site-relative path AND an explicitly allow-listed destination. Everything else renders as
    plain text, so a notification row can never become an open redirect."""
    assert inbox.safe_link(hostile) is None

    p = _principal()
    n = _seed(p, link=hostile)
    row = next(r for r in _rows(p) if r["id"] == n.id)
    assert row["link"] is None
    assert row["title"] == "New secure message from Ada", "the row itself must still be shown"


def test_a_notification_with_no_link_metadata_renders_without_one():
    p = _principal()
    n = _seed(p, notification_type="scheduling.reminder", title="Meeting tomorrow")
    row = next(r for r in _rows(p) if r["id"] == n.id)
    assert row["link"] is None and row["label"] == "Reminder"


# --- 5. the projection is a fixed key set -------------------------------------

def test_the_raw_metadata_blob_never_reaches_the_view():
    """A future producer must not be able to leak a payload onto this page through metadata."""
    p = _principal()
    n = record_notification(
        notification_type="portal.secure_message", recipient_type="user",
        recipient_ref=str(p.user_id), title="Ada replied", source_ref=f"test-{uuid.uuid4()}",
        metadata={"link": "/admin/client-portal/threads/9", "secret": "SSN-000-00-0000"})

    row = next(r for r in _rows(p) if r["id"] == n.id)
    assert set(row) == {"id", "notification_type", "label", "title", "body", "created_at",
                        "read_at", "unread", "link"}
    assert "SSN-000-00-0000" not in str(row)
    assert "dedupe_key" not in row and "notification_metadata" not in row


# --- 6. authorization ---------------------------------------------------------

def _required(path, method="GET"):
    cap = next((code for pattern, code in RULES if pattern.search(path)), None)
    if method not in {"GET", "HEAD", "OPTIONS"} and cap:
        cap = cap.replace(".read", ".write")
    return cap


@pytest.mark.parametrize("path,method", [
    ("/notifications", "GET"), ("/notifications/7/read", "POST"),
    ("/notifications/read-all", "POST"),
])
def test_the_route_matches_no_middleware_rule_and_gates_itself(path, method):
    """Deliberately NOT under /admin: the generic rule there demands identity.manage, which would
    have hidden a personal inbox from everyone but the Administrator."""
    assert _required(path, method) is None
    assert _required("/admin/notifications") == "identity.manage", \
        "the /admin rule must stay exactly as it was"


@pytest.mark.parametrize("handler", [
    route.notification_inbox, route.notification_mark_read, route.notification_mark_all_read,
])
def test_every_endpoint_declares_the_message_read_capability(handler):
    from app.security.dependencies import CAPABILITY_DEP_ATTR

    codes = set()
    for default in handler.__defaults__ or ():
        codes.update(getattr(getattr(default, "dependency", None), CAPABILITY_DEP_ATTR, ()))
    assert codes == {"communications.message.read"}


def test_a_principal_without_the_capability_is_refused():
    from app.security.dependencies import require_capability

    gate = require_capability(route.CAPABILITY)
    allowed = _principal()
    assert gate(principal=allowed) is allowed
    for caps in (("client.read", "client.write", "record.read_all"), ("identity.manage",), ()):
        with pytest.raises(HTTPException) as excinfo:
            gate(principal=_principal(caps=frozenset(caps)))
        assert excinfo.value.status_code == 403


def test_the_sidebar_gates_notifications_on_the_capability_the_route_enforces():
    """Gated more loosely it is shown-then-403; more tightly it hides the surface from its audience."""
    import pathlib
    src = pathlib.Path("app/templates/base.html").read_text(encoding="utf-8")
    assert "{% set can_notifications = 'communications.message.read' in caps %}" in src
    item = next(line for line in src.splitlines() if '"href": "/notifications"' in line)
    assert '"show": can_notifications' in item


# --- 7. the route renders, and Batch 2 still works end to end -----------------

def test_the_inbox_page_renders_the_users_own_notifications():
    p = _principal()
    n = _seed(p, title="Ada sent a secure message", link="/admin/client-portal/threads/77")

    html = route.notification_inbox(_fake_request(), principal=p).body.decode()

    assert "Ada sent a secure message" in html
    assert 'href="/admin/client-portal/threads/77"' in html
    assert "Unread" in html
    assert str(n.id) in html


def test_a_hostile_link_is_not_emitted_into_the_page():
    p = _principal()
    _seed(p, title="Hostile", link="https://evil.example/steal")

    html = route.notification_inbox(_fake_request(), principal=p).body.decode()

    assert "Hostile" in html
    assert "evil.example" not in html


def test_the_empty_inbox_renders():
    html = route.notification_inbox(_fake_request(), principal=_principal()).body.decode()
    assert "No notifications" in html


def test_mark_read_endpoints_redirect_back_to_the_inbox():
    p = _principal()
    n = _seed(p)

    one = route.notification_mark_read(n.id, _fake_request(), principal=p)
    assert one.status_code == 303 and one.headers["location"].startswith("/notifications")
    assert next(r for r in _rows(p) if r["id"] == n.id)["unread"] is False

    _seed(p, title="another")
    every = route.notification_mark_all_read(_fake_request(), principal=p)
    assert every.status_code == 303 and every.headers["location"].startswith("/notifications")
    assert inbox.staff_notifications(p)["unread_count"] == 0


def test_a_secure_message_from_batch_2_appears_in_the_owners_inbox():
    """End to end: a client reply on an assigned thread becomes a row on that owner's inbox, with a
    working link back to the conversation. This is the whole point of the two batches together."""
    from app.db import portal_threads
    from app.portal.service import create_thread, send_message

    staff = _principal()
    _, client, person_id, household_id = seed_portal_account(staff.user_id)
    thread_id = create_thread(client, household_id=household_id, person_id=person_id,
                              subject="Rollover", body="Opening message")
    with engine.begin() as c:
        c.execute(portal_threads.update().where(portal_threads.c.id == thread_id).values(
            assigned_user_id=staff.user_id))

    send_message(client, thread_id, "Any update?")

    rows = _rows(staff)
    assert rows, "the assigned owner received nothing"
    row = rows[0]
    assert row["notification_type"] == "portal.secure_message"
    assert row["link"] == f"/admin/client-portal/threads/{thread_id}"
    assert row["unread"] is True

    html = route.notification_inbox(_fake_request(), principal=staff).body.decode()
    assert f'href="/admin/client-portal/threads/{thread_id}"' in html


def test_team_addressed_notifications_are_recorded_but_not_shown_to_a_user():
    """A known gap, recorded rather than assumed away: message_notifications can address a thread's
    assigned TEAM, and resolving team membership to individuals is out of scope for this surface."""
    p = _principal()
    team_row = record_notification(
        notification_type="portal.secure_message", recipient_type="team", recipient_ref="1",
        title="Team message", source_ref=f"test-{uuid.uuid4()}")

    assert team_row.id not in [n["id"] for n in _rows(p)]
    assert list_notifications(recipient_type="team", recipient_ref="1", limit=200), \
        "the row is still durably recorded for a future team-aware surface"
