"""The cross-client staff communications inbox (Batch 4e).

Batch 4d answered "what has THIS client said to us?". This is the question staff actually open the
day with: across every client I service, what is waiting? It is a DERIVED work queue over the two
authoritative stores — no third store, nothing copied between them, and loading it writes nothing at
all (four storage-invariant tests pin that).

The two things a queue like this gets wrong, and what pins them here:

  * **Fabricated urgency.** "Needs reply" has to be a fact, not a guess. Portal uses the store's own
    unread/awaiting semantics; email compares the newest inbound against the newest outbound WITHIN
    one canonical conversation. The ordering tests walk a conversation through inbound → reply →
    inbound again and assert the state flips each time, and a separate test proves a second,
    unrelated conversation does not move the first one's answer.
  * **Scope leaking through a filter.** "Unassigned" is the classic hole: a queue that shows
    unassigned work firm-wide lets anyone enumerate the client base. Every filter here runs on rows
    that were already restricted in SQL by the principal's own record scope, and there is a test for
    exactly that.

No live Graph, no email sent, no SharePoint, no SMS.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select, update

from app.db import (
    communication_conversations,
    communication_messages,
    engine,
    people,
    portal_messages,
    portal_threads,
    record_assignments,
    timeline_events,
)
from app.portal.service import create_thread, staff_send_message
from app.security.models import Principal
from app.services.communications import email_ingest, email_send
from app.services.communications import inbox as inbox_mod
from app.services.communications.engagement.feed import EMAIL, SECURE_MESSAGE
from app.services.communications.inbox import staff_communications_inbox
from tests._portal_util import seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("portal_messaging_on")

# A servicing advisor: sees message content, may reply on both channels, no firm-wide bypass — so
# the record-scope filtering is genuinely exercised rather than short-circuited.
SERVICING = frozenset({"communications.message.read", "communications.message.write",
                       "communications.send"})
READ_ONLY = frozenset({"communications.message.read"})
NO_ACCESS = frozenset({"communications.view"})

TENANT = "tenant-4e"
MAILBOX = "mailbox-4e"

_SEEN_CONVERSATIONS: set = set()
_SEEN_PEOPLE: set = set()
_SEEN_HOUSEHOLDS: set = set()
_SEEN_ACCOUNTS: set = set()
_STAFF: dict = {}


@pytest.fixture(autouse=True)
def _cleanup():
    """Leave the database as this suite found it, as far as the store's own invariants allow.

    ``portal_messages`` is append-only and ``communication_events`` RESTRICT-anchors its
    conversations, so a client that sent a secure message stays; everything else is removed. A staff
    user referenced by the append-only audit ledger also cannot be deleted, which is why this suite
    reuses two shared actors rather than seeding one per test.
    """
    yield
    from app.db import (
        households,
        portal_access_grants,
        portal_accounts,
        portal_auth_tokens,
        portal_consents,
        portal_devices,
        portal_document_requests,
        portal_email_verifications,
        portal_invitations,
        portal_notifications,
        portal_sessions,
    )
    with engine.begin() as c:
        if _SEEN_CONVERSATIONS:
            ids = list(_SEEN_CONVERSATIONS)
            c.execute(communication_messages.delete().where(
                communication_messages.c.conversation_id.in_(ids)))
            c.execute(communication_conversations.update().where(
                communication_conversations.c.id.in_(ids)).values(person_id=None,
                                                                  household_id=None))
        if _SEEN_PEOPLE:
            people_ids = list(_SEEN_PEOPLE)
            pinned_people = set(c.scalars(select(portal_threads.c.person_id).where(
                portal_threads.c.person_id.in_(people_ids))).all())
            pinned_households = set(c.scalars(select(portal_threads.c.household_id).where(
                portal_threads.c.household_id.in_(list(_SEEN_HOUSEHOLDS)))).all())
            free_people = [p for p in people_ids if p not in pinned_people]
            free_households = [h for h in _SEEN_HOUSEHOLDS if h not in pinned_households]
            free_accounts = list(c.scalars(select(portal_accounts.c.id).where(
                portal_accounts.c.id.in_(list(_SEEN_ACCOUNTS)),
                portal_accounts.c.person_id.in_(free_people or [-1]))).all())
            c.execute(timeline_events.delete().where(
                timeline_events.c.person_id.in_(people_ids)))
            c.execute(record_assignments.delete().where(
                record_assignments.c.entity_type == "person",
                record_assignments.c.entity_id.in_(people_ids)))
            if _SEEN_HOUSEHOLDS:
                c.execute(record_assignments.delete().where(
                    record_assignments.c.entity_type == "household",
                    record_assignments.c.entity_id.in_(list(_SEEN_HOUSEHOLDS))))
            if free_accounts:
                for table in (portal_access_grants, portal_auth_tokens, portal_consents,
                              portal_devices, portal_email_verifications, portal_invitations,
                              portal_notifications, portal_sessions):
                    c.execute(table.delete().where(table.c.portal_account_id.in_(free_accounts)))
                c.execute(portal_accounts.delete().where(portal_accounts.c.id.in_(free_accounts)))
            if free_people:
                c.execute(portal_document_requests.delete().where(
                    portal_document_requests.c.person_id.in_(free_people)))
                c.execute(people.delete().where(people.c.id.in_(free_people)))
            if free_households:
                c.execute(households.delete().where(households.c.id.in_(free_households)))
    for seen in (_SEEN_CONVERSATIONS, _SEEN_PEOPLE, _SEEN_HOUSEHOLDS, _SEEN_ACCOUNTS):
        seen.clear()


def _staff(key="advisor"):
    """Two shared, audit-anchored staff actors for the whole module — never one per test."""
    if key not in _STAFF:
        _STAFF[key] = seed_staff_user()
    return _STAFF[key]


def _principal(uid, caps=SERVICING):
    return Principal(uid, f"staff-{uid}@360wealth.example", "Advisor", frozenset(caps))


class _Client:
    """One client, owned by the given staff user through a real record assignment."""

    def __init__(self, owner_uid=None, *, assign=True):
        self.staff_id = owner_uid if owner_uid is not None else _staff()
        (self.account_id, self.portal_principal,
         self.person_id, self.household_id) = seed_portal_account(self.staff_id)
        _SEEN_ACCOUNTS.add(self.account_id)
        _SEEN_PEOPLE.add(self.person_id)
        _SEEN_HOUSEHOLDS.add(self.household_id)
        with engine.begin() as c:
            if assign:
                c.execute(insert(record_assignments).values(
                    user_id=self.staff_id, entity_type="person", entity_id=self.person_id,
                    assignment_type="owner", effective_date=date.today()))
            self.email = c.execute(select(people.c.primary_email).where(
                people.c.id == self.person_id)).scalar()
            if not self.email:
                self.email = f"client-{uuid.uuid4().hex[:8]}@example.test"
                c.execute(people.update().where(people.c.id == self.person_id).values(
                    primary_email=self.email, normalized_email=self.email))
        self.principal = _principal(self.staff_id)


# --- seeding the two stores ---------------------------------------------------------------------

def _portal_thread(client, *, subject="Portal question", reply=False, resolve=False):
    thread = create_thread(client.portal_principal, household_id=client.household_id,
                           person_id=client.person_id, subject=subject,
                           body="Could you confirm my balance?")
    thread_id = thread["id"] if isinstance(thread, dict) else thread
    if reply:
        staff_send_message(thread_id=thread_id, user_id=client.staff_id,
                           body="Confirmed — the balance is correct.", principal=client.principal)
    if resolve:
        with engine.begin() as c:
            c.execute(update(portal_threads).where(portal_threads.c.id == thread_id).values(
                status="resolved", resolved_at=datetime.now(UTC)))
    return thread_id


def _account(client):
    return {"id": 1, "tenant_id": TENANT, "user_id": MAILBOX, "email": client.principal.email}


def _graph_message(client, *, subject, conversation=None):
    tag = uuid.uuid4().hex[:10]
    return {
        "id": f"AAMk{tag}", "subject": subject,
        "from": {"emailAddress": {"name": "Client", "address": client.email}},
        "toRecipients": [{"emailAddress": {"name": client.principal.email,
                                           "address": client.principal.email}}],
        "ccRecipients": [], "receivedDateTime": "2026-09-03T10:00:00Z",
        "bodyPreview": "Please confirm the figures on page two.",
        "webLink": f"https://outlook.office.com/mail/{tag}", "hasAttachments": False,
        "isRead": True, "conversationId": conversation or f"AAQk{tag}",
        "internetMessageId": f"<{tag}@example.test>",
    }


def _inbound_email(client, *, subject="Statement question", conversation=None):
    message = _graph_message(client, subject=subject, conversation=conversation)
    match = email_ingest.resolve_match(
        message, {client.email: (client.person_id, client.household_id)}, client.principal.email)
    with engine.begin() as c:
        message_id = email_ingest.normalize_email(c, account=_account(client), message=message,
                                                  match=match)
    assert message_id is not None, "fixture precondition: the inbound email anchored"
    with engine.connect() as c:
        conversation_id = c.execute(select(communication_messages.c.conversation_id).where(
            communication_messages.c.id == message_id)).scalar_one()
    _SEEN_CONVERSATIONS.add(conversation_id)
    return message_id, conversation_id, message


class _Transport:
    def __init__(self):
        self.calls = []

    def __call__(self, token, graph_id, body):
        self.calls.append((token, graph_id, body))


def _outbound_reply(client, inbound_id, monkeypatch, body="Confirmed."):
    monkeypatch.setattr("app.services.microsoft_identity.get_microsoft_access_token",
                        lambda account: "test-token")
    monkeypatch.setattr("app.services.microsoft_identity.account_for_principal",
                        lambda principal, conn=None: _account(client))
    return email_send.send_reply(client.principal, communication_message_id=inbound_id, body=body,
                                 send_key=email_send.new_send_key(), transport=_Transport())


def _queue(principal, **kw):
    return staff_communications_inbox(principal, **kw)


def _ids(result):
    return [r.item_id for r in result["rows"]]


def _item(result, item_id):
    return next(r for r in result["rows"] if r.item_id == item_id)


# ============================ PORTAL QUEUE ============================

def test_an_unread_client_message_appears_and_asks_for_attention():
    c = _Client()
    thread_id = _portal_thread(c)
    row = _item(_queue(c.principal), f"{SECURE_MESSAGE}:{thread_id}")
    assert row.attention is True
    assert row.unread is True
    assert row.attention_reason == "Unread client message"
    assert row.channel_label == "Secure Message"


def test_a_resolved_thread_stops_asking_for_attention():
    """Resolution is the store's own state; the queue reads it rather than second-guessing it."""
    c = _Client()
    thread_id = _portal_thread(c, resolve=True)
    row = _item(_queue(c.principal), f"{SECURE_MESSAGE}:{thread_id}")
    assert row.attention is False
    assert row.status == "resolved"


def test_a_replied_thread_that_has_been_read_is_not_attention():
    c = _Client()
    thread_id = _portal_thread(c, reply=True)
    with engine.begin() as conn:            # the firm has now read it, too
        conn.execute(update(portal_threads).where(portal_threads.c.id == thread_id).values(
            staff_last_read_at=datetime.now(UTC) + timedelta(minutes=1)))
    row = _item(_queue(c.principal), f"{SECURE_MESSAGE}:{thread_id}")
    assert row.unread is False
    assert row.attention is False


def test_the_mine_filter_shows_threads_assigned_to_the_caller():
    c = _Client()
    thread_id = _portal_thread(c)
    with engine.begin() as conn:
        conn.execute(update(portal_threads).where(portal_threads.c.id == thread_id).values(
            assigned_user_id=c.staff_id))
    mine = _queue(c.principal, view=inbox_mod.FILTER_MINE)
    assert f"{SECURE_MESSAGE}:{thread_id}" in _ids(mine)
    assert all(r.assigned_user_id == c.staff_id for r in mine["rows"])


def test_the_unassigned_filter_shows_only_unassigned_threads():
    c = _Client()
    unassigned_id = _portal_thread(c, subject="Nobody owns this")
    assigned_id = _portal_thread(c, subject="Owned")
    with engine.begin() as conn:
        conn.execute(update(portal_threads).where(portal_threads.c.id == assigned_id).values(
            assigned_user_id=c.staff_id))
    rows = _ids(_queue(c.principal, view=inbox_mod.FILTER_UNASSIGNED))
    assert f"{SECURE_MESSAGE}:{unassigned_id}" in rows
    assert f"{SECURE_MESSAGE}:{assigned_id}" not in rows


def test_a_thread_outside_record_scope_never_appears():
    """The leak this queue could most easily introduce: another advisor's client."""
    mine = _Client(_staff("advisor"))
    theirs = _Client(_staff("other"), assign=True)
    theirs_thread = _portal_thread(theirs, subject="Someone else's client")
    _portal_thread(mine, subject="My client")
    rows = _ids(_queue(mine.principal))
    assert f"{SECURE_MESSAGE}:{theirs_thread}" not in rows
    assert any(r.person_id == mine.person_id for r in _queue(mine.principal)["rows"])


def test_unassigned_cannot_be_used_to_enumerate_the_firm():
    """An unassigned thread is still record-scope filtered — otherwise "unassigned" becomes a way to
    list every client in the firm."""
    mine = _Client(_staff("advisor"))
    theirs = _Client(_staff("other"))
    theirs_thread = _portal_thread(theirs)          # unassigned AND outside my scope
    _portal_thread(mine)
    assert f"{SECURE_MESSAGE}:{theirs_thread}" not in _ids(
        _queue(mine.principal, view=inbox_mod.FILTER_UNASSIGNED))


def _stranger():
    """A staff actor assigned to no client. Shared, because the audit ledger pins staff users."""
    return _principal(_staff("stranger"))


def test_a_principal_assigned_to_nothing_gets_an_empty_queue():
    c = _Client(assign=False)
    _portal_thread(c)
    assert _queue(_stranger())["rows"] == []


# ============================ EMAIL QUEUE ============================

def test_a_latest_inbound_email_asks_for_attention():
    c = _Client()
    _, conversation_id, _ = _inbound_email(c)
    row = _item(_queue(c.principal), f"{EMAIL}:{conversation_id}")
    assert row.attention is True
    assert row.attention_reason == "Latest message is inbound"
    assert row.direction == "inbound"


def test_a_newer_outbound_reply_marks_the_conversation_answered(monkeypatch):
    c = _Client()
    inbound_id, conversation_id, _ = _inbound_email(c)
    assert _item(_queue(c.principal), f"{EMAIL}:{conversation_id}").attention is True
    _outbound_reply(c, inbound_id, monkeypatch)
    row = _item(_queue(c.principal), f"{EMAIL}:{conversation_id}")
    assert row.attention is False
    assert row.direction == "outbound"


def test_a_later_inbound_makes_it_attention_again(monkeypatch):
    """The full round trip. Attention is a comparison of two timestamps, so it must flip back."""
    c = _Client()
    inbound_id, conversation_id, message = _inbound_email(c, subject="Round trip")
    _outbound_reply(c, inbound_id, monkeypatch)
    assert _item(_queue(c.principal), f"{EMAIL}:{conversation_id}").attention is False

    # The client writes again in the SAME provider conversation.
    _inbound_email(c, subject="Round trip", conversation=message["conversationId"])
    row = _item(_queue(c.principal), f"{EMAIL}:{conversation_id}")
    assert row.attention is True
    assert row.direction == "inbound"


def test_an_unrelated_conversation_does_not_change_another_ones_state(monkeypatch):
    """Attention is decided strictly WITHIN one canonical conversation — never across them, and
    never by subject."""
    c = _Client()
    answered_in, answered_id, _ = _inbound_email(c, subject="Same subject")
    _outbound_reply(c, answered_in, monkeypatch)
    _, waiting_id, _ = _inbound_email(c, subject="Same subject")   # identical subject, new thread

    assert _item(_queue(c.principal), f"{EMAIL}:{answered_id}").attention is False
    assert _item(_queue(c.principal), f"{EMAIL}:{waiting_id}").attention is True


def test_email_unread_is_never_fabricated():
    """Outlook's isRead belongs to one mailbox, not the firm. Absent, not False, so the UI can tell
    "no such concept" from "read"."""
    c = _Client()
    _, conversation_id, _ = _inbound_email(c)
    assert _item(_queue(c.principal), f"{EMAIL}:{conversation_id}").unread is None


def test_email_assignment_is_derived_from_the_record_owner_not_invented():
    """``communication_conversations`` has no assignment column. Rather than build one, the owner is
    read from the authoritative record assignment — and labelled as derived."""
    c = _Client()
    _, conversation_id, _ = _inbound_email(c)
    row = _item(_queue(c.principal), f"{EMAIL}:{conversation_id}")
    assert row.assigned_user_id == c.staff_id
    assert row.assignment_source == "record"


def test_portal_assignment_is_the_threads_own_and_is_labelled_as_real():
    c = _Client()
    thread_id = _portal_thread(c)
    with engine.begin() as conn:
        conn.execute(update(portal_threads).where(portal_threads.c.id == thread_id).values(
            assigned_user_id=c.staff_id))
    row = _item(_queue(c.principal), f"{SECURE_MESSAGE}:{thread_id}")
    assert row.assignment_source == "thread"


def test_an_email_outside_record_scope_never_appears():
    mine = _Client(_staff("advisor"))
    theirs = _Client(_staff("other"))
    _, theirs_conversation, _ = _inbound_email(theirs)
    _inbound_email(mine)
    assert f"{EMAIL}:{theirs_conversation}" not in _ids(_queue(mine.principal))


# ============================ UNIFIED QUEUE ============================

def test_both_channels_appear_in_one_queue():
    c = _Client()
    thread_id = _portal_thread(c)
    _, conversation_id, _ = _inbound_email(c)
    rows = _ids(_queue(c.principal))
    assert f"{SECURE_MESSAGE}:{thread_id}" in rows
    assert f"{EMAIL}:{conversation_id}" in rows


def test_channel_labels_are_correct():
    c = _Client()
    _portal_thread(c)
    _inbound_email(c)
    labels = {r.channel: r.channel_label for r in _queue(c.principal)["rows"]}
    assert labels == {SECURE_MESSAGE: "Secure Message", EMAIL: "Email"}


def test_attention_items_come_first_and_oldest_waits_first(monkeypatch):
    """THE ORDERING RULE: attention before everything else; within attention, oldest first — the
    client who has waited longest is the most urgent, and newest-first would bury exactly them.
    Outside attention, newest first."""
    c = _Client()
    _, old_waiting, _ = _inbound_email(c, subject="Waiting longest")
    _, new_waiting, _ = _inbound_email(c, subject="Waiting briefly")
    answered_in, answered, _ = _inbound_email(c, subject="Answered")
    _outbound_reply(c, answered_in, monkeypatch)
    with engine.begin() as conn:            # make the wait times unambiguous
        conn.execute(update(communication_messages).where(
            communication_messages.c.conversation_id == old_waiting).values(
            created_at=datetime.now(UTC) - timedelta(days=9)))
        conn.execute(update(communication_messages).where(
            communication_messages.c.conversation_id == new_waiting).values(
            created_at=datetime.now(UTC) - timedelta(days=1)))

    rows = _queue(c.principal)["rows"]
    order = [r.item_id for r in rows]
    assert order.index(f"{EMAIL}:{old_waiting}") < order.index(f"{EMAIL}:{new_waiting}"), \
        "the longest wait must be first"
    assert order.index(f"{EMAIL}:{new_waiting}") < order.index(f"{EMAIL}:{answered}"), \
        "everything needing attention comes before everything that does not"
    assert [r.attention for r in rows] == sorted((r.attention for r in rows), reverse=True)


def test_age_reflects_how_long_the_client_has_waited():
    c = _Client()
    _, conversation_id, _ = _inbound_email(c)
    with engine.begin() as conn:
        conn.execute(update(communication_messages).where(
            communication_messages.c.conversation_id == conversation_id).values(
            created_at=datetime.now(UTC) - timedelta(days=5)))
    row = _item(_queue(c.principal), f"{EMAIL}:{conversation_id}")
    assert row.age_days == 5
    assert row.age_label == "5 days"


def test_each_row_links_to_its_conversation_and_its_client():
    c = _Client()
    thread_id = _portal_thread(c)
    _, conversation_id, _ = _inbound_email(c)
    result = _queue(c.principal)
    portal = _item(result, f"{SECURE_MESSAGE}:{thread_id}")
    email = _item(result, f"{EMAIL}:{conversation_id}")
    assert portal.conversation_url == f"/admin/client-portal/threads/{thread_id}"
    assert email.conversation_url == f"/communications/{conversation_id}"
    assert portal.client_url == f"/client/{c.person_id}"
    assert email.client_url == f"/client/{c.person_id}"
    # The client is taken from the authoritative anchor, never inferred from display text.
    assert portal.person_id == c.person_id and email.person_id == c.person_id


def test_the_attachment_count_is_shown_but_no_content_is(monkeypatch):
    from app.db import communication_attachments, documents
    c = _Client()
    inbound_id, conversation_id, _ = _inbound_email(c)
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        doc_id = conn.execute(insert(documents).values(
            original_name="statement.pdf", stored_name=f"st-{tag}.pdf",
            storage_path=f"/tmp/client360-test/st-{tag}.pdf", size_bytes=12,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            person_id=c.person_id).returning(documents.c.id)).scalar_one()
        conn.execute(insert(communication_attachments).values(
            message_id=inbound_id, document_id=doc_id))
    row = _item(_queue(c.principal), f"{EMAIL}:{conversation_id}")
    assert row.attachment_count == 1
    # The queue is a triage list: an indicator, never a download link or file content.
    assert not hasattr(row, "attachments")
    with engine.begin() as conn:
        conn.execute(communication_attachments.delete().where(
            communication_attachments.c.document_id == doc_id))
        conn.execute(documents.delete().where(documents.c.id == doc_id))


# ============================ AUTHORIZATION ============================

def test_without_message_read_there_is_no_inbox():
    c = _Client()
    _portal_thread(c)
    _inbound_email(c)
    result = _queue(_principal(c.staff_id, NO_ACCESS))
    assert result["authorized"] is False
    assert result["rows"] == [] and result["total"] == 0


def test_a_later_page_cannot_reach_content_the_capability_denies():
    c = _Client()
    _portal_thread(c)
    assert _queue(_principal(c.staff_id, NO_ACCESS), page=3)["rows"] == []


def test_the_portal_reply_action_requires_message_write():
    c = _Client()
    thread_id = _portal_thread(c)
    assert _item(_queue(c.principal), f"{SECURE_MESSAGE}:{thread_id}").reply_url
    weak = _principal(c.staff_id, READ_ONLY)
    assert _item(_queue(weak), f"{SECURE_MESSAGE}:{thread_id}").reply_url is None


def test_the_outlook_reply_action_requires_communications_send():
    c = _Client()
    _, conversation_id, _ = _inbound_email(c)
    assert _item(_queue(c.principal), f"{EMAIL}:{conversation_id}").reply_url
    weak = _principal(c.staff_id, READ_ONLY)
    assert _item(_queue(weak), f"{EMAIL}:{conversation_id}").reply_url is None


def test_a_hidden_reply_action_is_still_refused_by_the_route(monkeypatch):
    """The absent button is a courtesy. The refusal is the route's, and it stands alone."""
    c = _Client()
    inbound_id, conversation_id, _ = _inbound_email(c)
    weak = _principal(c.staff_id, READ_ONLY)
    assert _item(_queue(weak), f"{EMAIL}:{conversation_id}").reply_url is None
    monkeypatch.setattr("app.services.microsoft_identity.account_for_principal",
                        lambda principal, conn=None: _account(c))
    transport = _Transport()
    with pytest.raises(email_send.NotAuthorized):
        email_send.send_reply(weak, communication_message_id=inbound_id, body="Sneaking in.",
                              send_key=email_send.new_send_key(), transport=transport)
    assert transport.calls == []


def test_an_email_without_a_provider_identity_offers_no_reply():
    from app.db import communication_message_sources as sources
    c = _Client()
    inbound_id, conversation_id, _ = _inbound_email(c)
    with engine.begin() as conn:
        conn.execute(sources.delete().where(sources.c.message_id == inbound_id))
    assert _item(_queue(c.principal), f"{EMAIL}:{conversation_id}").reply_url is None


# ============================ FILTERS ============================

def test_the_all_filter_shows_both_channels():
    c = _Client()
    _portal_thread(c)
    _inbound_email(c)
    assert {r.channel for r in _queue(c.principal, view=inbox_mod.FILTER_ALL)["rows"]} == \
        {SECURE_MESSAGE, EMAIL}


def test_the_secure_message_channel_filter_excludes_email():
    c = _Client()
    _portal_thread(c)
    _inbound_email(c)
    rows = _queue(c.principal, channel=SECURE_MESSAGE)["rows"]
    assert rows and {r.channel for r in rows} == {SECURE_MESSAGE}


def test_the_email_channel_filter_excludes_portal():
    c = _Client()
    _portal_thread(c)
    _inbound_email(c)
    rows = _queue(c.principal, channel=EMAIL)["rows"]
    assert rows and {r.channel for r in rows} == {EMAIL}


def test_the_attention_filter_shows_only_what_is_waiting(monkeypatch):
    c = _Client()
    inbound_id, answered, _ = _inbound_email(c, subject="Answered")
    _outbound_reply(c, inbound_id, monkeypatch)
    _, waiting, _ = _inbound_email(c, subject="Waiting")
    rows = _ids(_queue(c.principal, view=inbox_mod.FILTER_ATTENTION))
    assert f"{EMAIL}:{waiting}" in rows
    assert f"{EMAIL}:{answered}" not in rows


def test_the_unread_filter_is_portal_only_because_email_has_no_unread():
    c = _Client()
    thread_id = _portal_thread(c)
    _inbound_email(c)
    rows = _queue(c.principal, view=inbox_mod.FILTER_UNREAD)["rows"]
    assert [r.item_id for r in rows] == [f"{SECURE_MESSAGE}:{thread_id}"]


def test_the_resolved_filter_shows_resolved_threads():
    c = _Client()
    resolved_id = _portal_thread(c, resolve=True)
    open_id = _portal_thread(c)
    rows = _ids(_queue(c.principal, view=inbox_mod.FILTER_RESOLVED))
    assert f"{SECURE_MESSAGE}:{resolved_id}" in rows
    assert f"{SECURE_MESSAGE}:{open_id}" not in rows


def test_an_unknown_filter_falls_back_to_all_rather_than_emptying_the_queue():
    c = _Client()
    _portal_thread(c)
    result = _queue(c.principal, view="nonsense")
    assert result["filters"]["view"] == inbox_mod.FILTER_ALL
    assert result["rows"]


def test_the_counts_describe_the_whole_queue_not_the_filtered_page():
    """So the filter chips can say what is being filtered OUT."""
    c = _Client()
    _portal_thread(c)
    _inbound_email(c)
    result = _queue(c.principal, channel=EMAIL)
    assert result["counts"][SECURE_MESSAGE] == 1
    assert result["counts"][EMAIL] == 1
    assert result["total"] == 1


# ============================ PAGINATION ============================

def test_the_first_page_is_bounded():
    c = _Client()
    for i in range(4):
        _inbound_email(c, subject=f"Message {i}")
    result = _queue(c.principal, page_size=2)
    assert len(result["rows"]) == 2
    assert result["total"] == 4 and result["pages"] == 2


def test_the_second_page_is_deterministic_and_disjoint():
    c = _Client()
    for i in range(4):
        _inbound_email(c, subject=f"Message {i}")
    first = _queue(c.principal, page_size=2)
    second = _queue(c.principal, page_size=2, page=2)
    assert _queue(c.principal, page_size=2, page=2)["rows"] == second["rows"]
    assert set(_ids(first)) & set(_ids(second)) == set()
    assert len(set(_ids(first)) | set(_ids(second))) == 4


def test_filters_persist_across_pages():
    c = _Client()
    for i in range(4):
        _inbound_email(c, subject=f"Message {i}")
    _portal_thread(c)
    page2 = _queue(c.principal, channel=EMAIL, page_size=2, page=2)
    assert page2["filters"]["channel"] == EMAIL
    assert all(r.channel == EMAIL for r in page2["rows"])


def test_the_page_size_is_capped():
    c = _Client()
    _portal_thread(c)
    assert _queue(c.principal, page_size=10_000)["page_size"] == inbox_mod.MAX_PAGE_SIZE


# ============================ PERFORMANCE ============================

def test_the_query_count_does_not_grow_with_the_queue_size():
    """This page is cross-client and firm-wide: a query per thread, per conversation or per client
    is what would make it unusable at real volume. Record scope is resolved ONCE into id sets and
    applied in SQL, and every per-store lookup is batched."""
    from sqlalchemy import event

    c = _Client()
    _portal_thread(c)
    _inbound_email(c)

    statements = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        _queue(c.principal)
        small = len(statements)
        for i in range(8):
            _inbound_email(c, subject=f"Bulk {i}")
        _portal_thread(c, subject="Bulk thread")
        statements.clear()
        _queue(c.principal)
        large = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", _listener)

    assert large <= small + 1, (
        f"a queue of 11 cost {large} statements vs {small} for 2 — the lookups must stay batched")


# ============================ STORAGE INVARIANTS ============================

def _counts():
    from app.db import metadata
    notifications = metadata.tables["notifications"]      # the staff notification ledger
    with engine.connect() as conn:
        return {t.name: conn.execute(select(func.count()).select_from(t)).scalar()
                for t in (portal_messages, communication_messages, timeline_events, notifications)}


def test_loading_the_inbox_writes_nothing_at_all(monkeypatch):
    """The queue is DERIVED. It creates no portal message, no communication message, no timeline
    event and no notification — it is not a third store and not another event source."""
    c = _Client()
    _portal_thread(c)
    inbound_id, _, _ = _inbound_email(c)
    _outbound_reply(c, inbound_id, monkeypatch)

    before = _counts()
    for view in inbox_mod.FILTERS:
        _queue(c.principal, view=view)
        _queue(c.principal, view=view, channel=EMAIL)
        _queue(c.principal, view=view, channel=SECURE_MESSAGE)
    assert _counts() == before


def test_the_inbox_does_not_duplicate_the_notification_ledger():
    """/notifications stays the event-alert inbox; this is the client-message work queue. A queue row
    is derived from a conversation, never copied from a notification."""
    c = _Client()
    thread_id = _portal_thread(c)
    rows = _queue(c.principal)["rows"]
    assert all(r.item_id.startswith((f"{SECURE_MESSAGE}:", f"{EMAIL}:")) for r in rows)
    # Navigation stays consistent: the queue points at the same thread page a notification links to.
    assert _item(_queue(c.principal), f"{SECURE_MESSAGE}:{thread_id}").conversation_url == \
        f"/admin/client-portal/threads/{thread_id}"


# ============================ THE RENDERED PAGE ============================

def _request(qs=b""):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": "/communications/inbox",
                    "headers": [], "query_string": qs})


def _render(principal, **kw):
    from app.routes.communications import inbox
    return inbox(_request(), principal=principal, **kw).body.decode("utf-8")


def test_the_page_renders_both_channels():
    c = _Client()
    _portal_thread(c, subject="Portal subject here")
    _inbound_email(c, subject="Email subject here")
    html = _render(c.principal)
    assert "Secure Message" in html and "Email" in html
    assert "Portal subject here" in html and "Email subject here" in html


def test_the_page_says_so_when_the_capability_is_missing():
    c = _Client()
    _portal_thread(c)
    html = _render(_principal(c.staff_id, NO_ACCESS))
    assert "Not available" in html
    assert "Could you confirm my balance?" not in html


def test_the_page_offers_an_empty_state_rather_than_a_blank_screen():
    assert "Nothing waiting" in _render(_stranger())


def test_a_filter_with_no_matches_says_so_distinctly():
    """"Nothing waiting" and "nothing matches this filter" are different facts for the reader."""
    c = _Client()
    _portal_thread(c, resolve=True)
    html = _render(c.principal, view=inbox_mod.FILTER_ATTENTION)
    assert "No items match this filter" in html


def test_the_page_is_reachable_from_the_primary_staff_navigation():
    from pathlib import Path
    base = Path("app/templates/base.html").read_text(encoding="utf-8")
    assert '"href": "/communications/inbox", "label": "Communications"' in base
    assert '"show": can_messages' in base


def test_the_inbox_route_is_declared_before_the_conversation_route():
    """FastAPI matches in declaration order: after ``/{conversation_id}``, "inbox" would be parsed
    as a conversation id and 422."""
    from app.main import app
    paths = [getattr(r, "path", "") for r in app.routes]
    assert paths.index("/communications/inbox") < paths.index("/communications/{conversation_id}")


def test_the_route_requires_the_message_read_capability():
    """Gated on the capability that guards message CONTENT, and NOT placed under /admin — which
    would drag in identity.manage for what is daily client work, the same reasoning /notifications
    used."""
    from app.security.dependencies import CAPABILITY_DEP_ATTR
    from app.services.communications.inbox import READ_CAPABILITY

    assert READ_CAPABILITY == "communications.message.read"
    # The composer itself fails closed on the same capability, so the route gate is not the only
    # thing standing between an unauthorized caller and message content.
    c = _Client()
    _portal_thread(c)
    assert _queue(_principal(c.staff_id, NO_ACCESS))["authorized"] is False
    # And the route asks for it by name.
    from app.routes.communications import router
    route = next(r for r in router.routes
                 if getattr(r, "path", "") == "/communications/inbox")
    caps = [getattr(d.call, CAPABILITY_DEP_ATTR, None) for d in route.dependant.dependencies]
    assert ("communications.message.read",) in caps
    assert not route.path.startswith("/admin"), "daily client work does not live under /admin"
