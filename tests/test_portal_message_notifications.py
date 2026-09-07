"""Secure portal messages notify the OTHER party (Batch 2).

Messaging has been two-way since D.43, but no message event reached either notification ledger: a
client learned about a staff reply only by signing in, staff only by opening the work queue.
``app/portal/message_notifications.py`` closes that using the infrastructure that already exists —
no new table, channel, provider or transport.

The two directions land in DIFFERENT ledgers, because the platform has two audiences:

  client → staff   ``notifications`` (canonical ledger, intent only, recipient_type user|team)
  staff  → client  ``portal_notifications`` (the ledger /portal/notifications actually renders)

A NOTE ON THE STAFF HALF. There is no staff-facing notification surface in this release, so a staff
row is durable, deduplicated INTENT and a hook for a future inbox — not something staff can see yet.
The visible staff signal is still the work queue's per-thread unread state, which these tests confirm
is unchanged. That is a deliberate limitation of this batch, pinned by
``test_the_staff_ledger_row_is_intent_only_and_carries_the_deep_link`` so the gap is recorded in the
suite rather than assumed away.

What is pinned here: both directions fire, the SENDER is never notified, internal notes notify
nobody, unassigned threads notify nobody (the Hub never guesses an assignee and neither does this),
inactive portal accounts notify nobody, neither ledger receives the message BODY, links point at the
right thread, a refused message writes no notification, and one message can never produce two rows.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import engine, people, portal_messages, portal_notifications, portal_threads, teams
from app.portal import communication_hub as hub
from app.portal import message_notifications as mn
from app.portal.service import create_thread, send_message, staff_send_message
from app.security.models import Principal
from tests._portal_util import seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("portal_messaging_on", "production_identity_provider")

STAFF_CAPS = frozenset({"client.read", "client.write", "record.read_all", "record.write_all",
                        "communications.message.read", "communications.message.write"})

BODY = "Please confirm my rollover paperwork is complete."


def _ledger():
    from app.services.notifications import _notifications_table
    return _notifications_table()


def _staff_rows(message_id):
    """Canonical-ledger rows for one message, found by the source reference this module writes."""
    n = _ledger()
    with engine.connect() as c:
        return c.execute(select(n).where(
            n.c.source_ref == f"portal-message:{message_id}")).mappings().all()


def _client_rows(message_id):
    with engine.connect() as c:
        return c.execute(select(portal_notifications).where(
            portal_notifications.c.idempotency_key == f"portal-message:{message_id}")
        ).mappings().all()


def _only_message(thread_id):
    """The single message on a freshly opened thread."""
    with engine.connect() as c:
        return c.scalar(select(portal_messages.c.id).where(
            portal_messages.c.thread_id == thread_id).order_by(portal_messages.c.id))


def _assign(thread_id, *, user_id=None, team_id=None):
    with engine.begin() as c:
        c.execute(portal_threads.update().where(portal_threads.c.id == thread_id).values(
            assigned_user_id=user_id, assigned_team_id=team_id))


def _staff_principal(uid=None):
    return Principal(uid or seed_staff_user(), "staff@example.com", "Staff", STAFF_CAPS)


def _client_thread(owner_uid=None):
    """A client-opened thread, assigned to a staff owner so the staff side has a deterministic
    recipient (a brand-new client thread carries no assignment of its own)."""
    staff_uid = owner_uid or seed_staff_user()
    _, principal, person_id, household_id = seed_portal_account(staff_uid)
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Rollover", body=BODY)
    _assign(thread_id, user_id=staff_uid)
    return principal, person_id, household_id, thread_id, staff_uid


# --- 1 + 2. a client message notifies the staff who own the thread ------------

def test_a_client_reply_notifies_the_assigned_staff_owner():
    principal, _, _, thread_id, staff_uid = _client_thread()

    message_id = send_message(principal, thread_id, "Any update?")

    rows = _staff_rows(message_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["recipient_type"] == "user" and row["recipient_ref"] == str(staff_uid)
    assert row["notification_type"] == "portal.secure_message"
    assert row["channel"] == "in_app"


def test_a_client_opening_a_thread_notifies_the_owner_derived_from_record_assignments():
    """A brand-new client thread has no assignment of its own, so the recipient comes from the SAME
    read-only derivation route_thread uses over record_assignments. Nothing is assigned by doing so."""
    from datetime import date

    from app.db import record_assignments

    staff_uid = seed_staff_user()
    _, principal, person_id, household_id = seed_portal_account(staff_uid)
    with engine.begin() as c:
        c.execute(insert(record_assignments).values(
            entity_type="person", entity_id=person_id, user_id=staff_uid,
            assignment_type="owner", effective_date=date.today()))

    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="New question", body=BODY)

    with engine.connect() as c:
        assert c.scalar(select(portal_threads.c.assigned_user_id).where(
            portal_threads.c.id == thread_id)) is None, "resolution must not assign the thread"

    rows = _staff_rows(_only_message(thread_id))
    assert len(rows) == 1 and rows[0]["recipient_ref"] == str(staff_uid)


def test_a_team_assigned_thread_notifies_the_team():
    principal, _, _, thread_id, _ = _client_thread()
    with engine.connect() as c:
        team_id = c.scalar(select(func.max(teams.c.id)))
    if team_id is None:
        pytest.skip("no team rows in this database")
    _assign(thread_id, user_id=None, team_id=team_id)

    message_id = send_message(principal, thread_id, "Following up")

    rows = _staff_rows(message_id)
    assert len(rows) == 1 and rows[0]["recipient_type"] == "team"
    assert rows[0]["recipient_ref"] == str(team_id)


def test_an_unassigned_thread_with_no_derivable_owner_notifies_nobody():
    """The Hub's rule is that an assignee is never guessed; an unrouted conversation stays in the
    work queue's UNASSIGNED review state rather than being broadcast to every staff member."""
    staff_uid = seed_staff_user()
    _, principal, person_id, household_id = seed_portal_account(staff_uid)
    thread_id = create_thread(principal, household_id=household_id, person_id=person_id,
                              subject="Unrouted", body=BODY)
    _assign(thread_id, user_id=None, team_id=None)

    message_id = send_message(principal, thread_id, "Anyone there?")

    assert _staff_rows(message_id) == []


# --- 3 + 4. a staff message notifies the client ------------------------------

def test_a_staff_reply_notifies_the_client():
    principal, person_id, _, thread_id, staff_uid = _client_thread()

    message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="All set.")

    rows = _client_rows(message_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["portal_account_id"] == principal.account_id
    assert row["notification_type"] == "secure_message"
    assert row["channel"] == "in_app" and row["status"] == "delivered"


def test_a_staff_started_thread_notifies_the_client():
    staff = _staff_principal()
    _, principal, person_id, _ = seed_portal_account(staff.user_id)

    thread_id = hub.staff_start_thread(staff, person_id=person_id, subject="Your review",
                                       body="We have scheduled your annual review.")

    rows = _client_rows(_only_message(thread_id))
    assert len(rows) == 1 and rows[0]["portal_account_id"] == principal.account_id
    assert rows[0]["entity_type"] == "portal_thread" and rows[0]["entity_id"] == thread_id


def test_a_client_without_an_active_portal_account_is_not_notified():
    """Nowhere to read it. client_portal_status is the existing decision and this reuses it."""
    from app.db import portal_accounts

    principal, person_id, _, thread_id, staff_uid = _client_thread()
    with engine.begin() as c:
        c.execute(portal_accounts.update().where(
            portal_accounts.c.id == principal.account_id).values(status="revoked"))

    message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Hello?")

    assert _client_rows(message_id) == []


# --- 5. the sender is never notified -----------------------------------------

def test_a_client_message_never_notifies_the_client_who_sent_it():
    principal, _, _, thread_id, _ = _client_thread()

    message_id = send_message(principal, thread_id, "Checking in")

    assert _client_rows(message_id) == []
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(portal_notifications).where(
            portal_notifications.c.portal_account_id == principal.account_id,
            portal_notifications.c.notification_type == "secure_message")) == 0


def test_a_staff_message_never_notifies_the_staff_who_sent_it():
    _, _, _, thread_id, staff_uid = _client_thread()

    message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Replying")

    assert _staff_rows(message_id) == []


def test_an_internal_note_notifies_nobody():
    """Staff-only: the client can neither see it nor act on it, so it publishes no notification —
    exactly as it already publishes no timeline event."""
    _, _, _, thread_id, staff_uid = _client_thread()

    message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid,
                                    body="Internal: check custodian first", internal_note=True)

    assert _client_rows(message_id) == [] and _staff_rows(message_id) == []


# --- 6 + 7. links are right, message bodies never leak ------------------------

def test_the_staff_ledger_row_is_intent_only_and_carries_the_deep_link():
    """Records the batch's known limitation: staff notifications are intent in the canonical ledger
    (no staff-facing surface renders them yet), carrying references and a link — never content."""
    principal, person_id, household_id, thread_id, staff_uid = _client_thread()

    message_id = send_message(principal, thread_id, BODY)

    row = _staff_rows(message_id)[0]
    assert row["notification_metadata"]["link"] == f"/admin/client-portal/threads/{thread_id}"
    assert row["notification_metadata"]["thread_id"] == thread_id
    assert row["notification_metadata"]["message_id"] == message_id
    assert row["notification_metadata"]["person_id"] == person_id
    assert row["notification_metadata"]["household_id"] == household_id
    assert row["status"] == "pending", "intent only — nothing dispatched it"


def test_the_client_notification_references_the_thread_it_is_about():
    _, _, _, thread_id, staff_uid = _client_thread()

    message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Done.")

    row = _client_rows(message_id)[0]
    assert row["entity_type"] == "portal_thread"
    assert row["entity_id"] == thread_id


@pytest.mark.parametrize("direction", ["client_to_staff", "staff_to_client"])
def test_no_message_body_reaches_either_ledger(direction):
    """Titles name the counterparty; metadata carries references. The body stays in portal_messages."""
    principal, _, _, thread_id, staff_uid = _client_thread()
    secret = f"ACCOUNT-{uuid.uuid4().hex}-DO-NOT-COPY"

    if direction == "client_to_staff":
        message_id = send_message(principal, thread_id, secret)
        rows = _staff_rows(message_id)
        fields = [rows[0]["title"], rows[0]["body"] or "", str(rows[0]["notification_metadata"])]
    else:
        message_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body=secret)
        rows = _client_rows(message_id)
        fields = [rows[0]["title"], rows[0]["body"] or "", str(rows[0]["delivery_metadata"])]

    assert rows, "the notification was not created at all"
    for field in fields:
        assert secret not in field, f"the message body leaked into {field!r}"


def test_the_staff_title_names_the_client_but_not_the_subject():
    """Identity, which staff already see in the work queue — not client-authored content."""
    principal, person_id, _, thread_id, _ = _client_thread()
    with engine.connect() as c:
        name = c.scalar(select(people.c.full_name).where(people.c.id == person_id))

    message_id = send_message(principal, thread_id, "hello")

    title = _staff_rows(message_id)[0]["title"]
    assert title == f"New secure message from {name}"
    assert "Rollover" not in title, "the client-authored subject was copied into the notification"


# --- 8. a refused message writes no notification ------------------------------

def test_an_out_of_scope_reply_is_refused_and_notifies_nobody():
    """The message transaction rolls back before any notification is reached, so a refused send can
    never leave a notification behind claiming something arrived."""
    _, principal_a, _, _ = seed_portal_account(seed_staff_user())
    _, principal_b, pid_b, hh_b = seed_portal_account(seed_staff_user())
    thread_b = create_thread(principal_b, household_id=hh_b, person_id=pid_b,
                             subject="B's thread", body="private")

    n = _ledger()
    with engine.connect() as c:
        staff_before = c.scalar(select(func.count()).select_from(n).where(
            n.c.notification_type == "portal.secure_message"))
        client_before = c.scalar(select(func.count()).select_from(portal_notifications).where(
            portal_notifications.c.notification_type == "secure_message"))

    with pytest.raises(PermissionError):
        send_message(principal_a, thread_b, "let me in")

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(n).where(
            n.c.notification_type == "portal.secure_message")) == staff_before
        assert c.scalar(select(func.count()).select_from(portal_notifications).where(
            portal_notifications.c.notification_type == "secure_message")) == client_before


# --- 9 + 10. messaging is unchanged, and one message means one notification ----

def test_message_creation_behaviour_is_unchanged():
    """Notifications are additive: the message, its visibility, the thread activity markers and the
    client's own view of the conversation all behave exactly as before."""
    from app.portal.service import list_messages

    principal, _, _, thread_id, staff_uid = _client_thread()
    reply_id = send_message(principal, thread_id, "Client reply")
    staff_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Staff reply")
    note_id = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="Note",
                                 internal_note=True)

    with engine.connect() as c:
        rows = c.execute(select(portal_messages).where(
            portal_messages.c.thread_id == thread_id).order_by(portal_messages.c.id)).mappings().all()
        thread = c.execute(select(portal_threads).where(
            portal_threads.c.id == thread_id)).mappings().one()
    assert [r["id"] for r in rows][-3:] == [reply_id, staff_id, note_id]
    assert [r["visibility"] for r in rows][-3:] == ["client", "client", "internal"]
    assert thread["last_client_message_at"] is not None
    assert thread["last_staff_message_at"] is not None
    # The client still sees only client-visible messages — the internal note is not among them.
    assert "Note" not in [m["body"] for m in list_messages(principal, thread_id)]


def test_one_message_can_never_produce_two_notifications():
    """Both ledgers key on the message id, so a retried call returns the existing row."""
    principal, _, _, thread_id, staff_uid = _client_thread()

    client_msg = send_message(principal, thread_id, "once")
    staff_msg = staff_send_message(thread_id=thread_id, user_id=staff_uid, body="once back")

    first_staff = _staff_rows(client_msg)[0]["id"]
    first_client = _client_rows(staff_msg)[0]["id"]

    assert mn.notify_staff_of_client_message(thread_id, client_msg).id == first_staff
    assert mn.notify_client_of_staff_message(thread_id, staff_msg) == first_client
    assert len(_staff_rows(client_msg)) == 1
    assert len(_client_rows(staff_msg)) == 1


def test_a_missing_thread_resolves_to_no_recipient_rather_than_raising():
    assert mn.staff_recipient(None) is None
    assert mn.notify_client_of_staff_message(-1, -1) is None
    assert mn.notify_staff_of_client_message(-1, -1) is None


def test_the_staff_work_queue_unread_signal_is_untouched():
    """The visible staff signal is still the thread's unread state; notifications are additive."""
    principal, _, _, thread_id, staff_uid = _client_thread()
    send_message(principal, thread_id, "unread please")

    staff = _staff_principal(staff_uid)
    row = next(t for t in hub.staff_inbox(staff) if t["id"] == thread_id)
    assert row["unread"] is True
