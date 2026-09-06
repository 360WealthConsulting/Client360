"""Outbound Outlook reply sent from 360Plus under the existing ``Mail.Send`` (Batch 4c, ADR-075).

THE CONSTRAINT THAT SHAPES EVERYTHING HERE: this tenant has consented a read-only Graph scope set
plus ``Mail.Send``. The safer draft-first flow (``createReply`` then send, which yields a provider
message id BEFORE the send) needs ``Mail.ReadWrite``, and adding a scope invalidates every cached
token — all connected mailboxes would lose INBOUND sync until each user reconnected by hand. So the
direct reply endpoint is used, and it answers ``202 Accepted`` with no body: there is no provider
identity at send time. ``test_the_consented_scope_set_is_unchanged`` pins the premise; if it ever
fails, the whole design below should be revisited rather than patched.

WHAT STANDS IN FOR PRE-SEND IDENTITY is a durable LOCAL send intent committed before Graph is
touched, made race-safe by the UNIQUE (source_system, source_external_id) constraint that already
existed. The tests that matter most are the ones asserting a second submit sends NOTHING: a
duplicate email to a client cannot be withdrawn.

THE UNCERTAIN WINDOW IS TESTED AS A REFUSAL, not as a retry. A message left in ``sending`` — Graph
accepted it but we crashed before recording that — must never be resent; it is resolved by the Sent
Items poll. Only ``failed``, which is written solely when Graph told us it failed, may go again.

No live Graph call is made anywhere in this file: every transport is injected or monkeypatched, and
``test_graph_reply_posts_to_the_reply_endpoint`` inspects a fake ``requests`` module.
"""
from __future__ import annotations

import ast
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import (
    audit_events,
    communication_conversations,
    communication_deliveries,
    communication_message_sources,
    communication_messages,
    communication_recipients,
    engine,
    households,
    microsoft_accounts,
    people,
    record_assignments,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.communications import email_ingest, email_send

TENANT = "tenant-out"
MAILBOX = "mailbox-out-1"
STAFF_EMAIL = "advisor.out@360wealth.example"

SEND_CAPS = frozenset({"communications.view", "communications.send"})

_SEEN_PEOPLE: set = set()
_SEEN_HOUSEHOLDS: set = set()
_SEEN_CONVERSATIONS: set = set()
_STAFF: dict = {}


@pytest.fixture(scope="module", autouse=True)
def _cleanup_shared_staff():
    """The module's one staff user and mailbox, removed at the end of the module."""
    yield
    with engine.begin() as c:
        if _STAFF:
            c.execute(microsoft_accounts.delete().where(
                microsoft_accounts.c.id == _STAFF["account_id"]))
            # A user who has written to the append-only audit ledger cannot be deleted: the FK is
            # ON DELETE SET NULL and the immutability trigger refuses that update. That is the
            # ledger outliving the user record on purpose, so this one row is left behind.
            anchored = c.execute(select(audit_events.c.actor_user_id).where(
                audit_events.c.actor_user_id == _STAFF["uid"]).limit(1)).scalar()
            if anchored is None:
                c.execute(users.delete().where(users.c.id == _STAFF["uid"]))
    _STAFF.clear()


@pytest.fixture(autouse=True)
def _cleanup_seeded_rows():
    """Leave the database as this test found it — see tests/test_email_attachments.py.
    Orphan rows accumulate across runs and break table-count assertions in unrelated files.

    Conversations themselves survive: ``communication_events`` is append-only and RESTRICT-anchors
    them. Their client anchors are detached instead, as the Batch 4a suites do.
    """
    yield
    with engine.begin() as c:
        if _SEEN_CONVERSATIONS:
            # message_id on communication_events is a plain column, so messages are deletable;
            # recipients, deliveries and sources cascade from them.
            c.execute(communication_messages.delete().where(
                communication_messages.c.conversation_id.in_(list(_SEEN_CONVERSATIONS))))
        if _SEEN_PEOPLE:
            ids = list(_SEEN_PEOPLE)
            c.execute(timeline_events.delete().where(timeline_events.c.person_id.in_(ids)))
            c.execute(communication_conversations.update().where(
                communication_conversations.c.person_id.in_(ids)).values(person_id=None))
            c.execute(record_assignments.delete().where(
                record_assignments.c.entity_type == "person",
                record_assignments.c.entity_id.in_(ids)))
            c.execute(people.delete().where(people.c.id.in_(ids)))
        if _SEEN_HOUSEHOLDS:
            hids = list(_SEEN_HOUSEHOLDS)
            c.execute(communication_conversations.update().where(
                communication_conversations.c.household_id.in_(hids)).values(household_id=None))
            c.execute(households.delete().where(households.c.id.in_(hids)))
    for seen in (_SEEN_PEOPLE, _SEEN_HOUSEHOLDS, _SEEN_CONVERSATIONS):
        seen.clear()


# --- fixtures ----------------------------------------------------------------------------------

def _account(user_id=MAILBOX, tenant=TENANT, email=STAFF_EMAIL):
    return {"id": 1, "tenant_id": tenant, "user_id": user_id, "email": email}


def _inbound_message(*, sender, to, conversation=None, graph_id=None, subject="Quarterly review"):
    tag = uuid.uuid4().hex[:10]
    return {
        "id": graph_id or f"AAMk{tag}",
        "subject": subject,
        "from": {"emailAddress": {"name": "Client", "address": sender}},
        "toRecipients": [{"emailAddress": {"name": a, "address": a}} for a in to],
        "ccRecipients": [],
        "receivedDateTime": "2026-09-01T10:00:00Z",
        "bodyPreview": "Can you confirm the numbers?",
        "webLink": f"https://outlook.office.com/mail/{tag}",
        "hasAttachments": False,
        "isRead": True,
        "conversationId": conversation if conversation is not None else f"AAQk{tag}",
        "internetMessageId": f"<{tag}@example.test>",
    }


def _staff():
    """One staff user and one connected mailbox, shared by the whole module.

    A user who has written to the append-only audit ledger can never be deleted (see the teardown
    note), so seeding one per test would leave ~30 permanent rows in the shared test database on
    every run. Nothing here needs a second REAL mailbox: the mailbox-mismatch test polls under a
    mailbox id that has no account at all. ``account_for_principal`` matches the principal's own
    address, so one user implies one address implies one account.
    """
    if not _STAFF:
        tag = uuid.uuid4().hex[:8]
        staff_email = f"advisor-{tag}@360wealth.example"
        with engine.begin() as c:
            uid = c.execute(users.insert().values(
                email=staff_email, normalized_email=staff_email, display_name=f"Advisor {tag}",
                status="active").returning(users.c.id)).scalar_one()
            acct_id = c.execute(microsoft_accounts.insert().values(
                tenant_id=TENANT, user_id=MAILBOX, email=staff_email,
                display_name=f"Advisor {tag}").returning(microsoft_accounts.c.id)).scalar_one()
        _STAFF.update(uid=uid, email=staff_email, account_id=acct_id, mailbox=MAILBOX)
    return _STAFF


def _seed_thread(*, assign=True):
    """An ingested inbound email from a client, owned by the module's staff user."""
    tag = uuid.uuid4().hex[:8]
    client_address = f"client-{tag}@example.test"
    staff = _staff()
    uid, staff_email, mailbox = staff["uid"], staff["email"], staff["mailbox"]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(
            name=f"House {tag}").returning(households.c.id)).scalar_one()
        _SEEN_HOUSEHOLDS.add(hid)
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=client_address,
            normalized_email=client_address, active=True,
            household_id=hid).returning(people.c.id)).scalar_one()
        _SEEN_PEOPLE.add(pid)
        if assign:
            c.execute(record_assignments.insert().values(
                user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
                effective_date=date.today()))

    message = _inbound_message(sender=client_address, to=[staff_email])
    match = email_ingest.resolve_match(message, {client_address: (pid, hid)}, staff_email)
    with engine.begin() as c:
        inbound_id = email_ingest.normalize_email(
            c, account=_account(user_id=mailbox, email=staff_email), message=message, match=match)
    assert inbound_id is not None, "fixture precondition: the inbound email anchored"
    with engine.connect() as c:
        conversation_id = c.execute(select(communication_messages.c.conversation_id).where(
            communication_messages.c.id == inbound_id)).scalar_one()
    _SEEN_CONVERSATIONS.add(conversation_id)
    return {"uid": uid, "pid": pid, "hid": hid, "client": client_address, "tag": tag,
            "conversation_id": conversation_id,
            "inbound_id": inbound_id, "graph_id": message["id"],
            "conversation_graph_id": message["conversationId"], "staff_email": staff_email,
            "mailbox": mailbox,
            "principal": Principal(uid, staff_email, f"Advisor {tag}", SEND_CAPS)}


@pytest.fixture
def token(monkeypatch):
    """The MSAL round trip is the one thing that would reach Microsoft. Never let it."""
    monkeypatch.setattr("app.services.microsoft_identity.get_microsoft_access_token",
                        lambda account: "test-access-token")


class Transport:
    """Stands in for ``POST /me/messages/{id}/reply``. Records calls; can be told to fail."""

    def __init__(self, *, fail=False, on_call=None):
        self.calls: list[tuple[str, str, str]] = []
        self.fail = fail
        self.on_call = on_call

    def __call__(self, access_token, graph_id, body):
        self.calls.append((access_token, graph_id, body))
        if self.on_call is not None:
            self.on_call()
        if self.fail:
            raise RuntimeError("Graph reply failed with HTTP 503")


def _message_row(message_id):
    with engine.connect() as c:
        return c.execute(select(communication_messages).where(
            communication_messages.c.id == message_id)).mappings().one()


def _deliveries(message_id):
    with engine.connect() as c:
        return [r["status"] for r in c.execute(select(communication_deliveries).where(
            communication_deliveries.c.message_id == message_id).order_by(
            communication_deliveries.c.id)).mappings().all()]


def _sources(message_id, system=None):
    q = select(communication_message_sources).where(
        communication_message_sources.c.message_id == message_id)
    if system:
        q = q.where(communication_message_sources.c.source_system == system)
    with engine.connect() as c:
        return c.execute(q).mappings().all()


def _send(thread, transport, *, body="Confirmed, the numbers are right.", send_key=None,
          principal=None):
    return email_send.send_reply(
        principal or thread["principal"], communication_message_id=thread["inbound_id"],
        body=body, send_key=send_key or email_send.new_send_key(), transport=transport)


# ============================ THE PREMISE ============================

def test_the_consented_scope_set_is_unchanged():
    """Batch 4c chose the direct reply endpoint precisely so no scope changes. Pin that.

    ``Mail.ReadWrite`` here would invalidate every cached token and break INBOUND sync for all
    connected mailboxes until each user reconnected.
    """
    from app.services.microsoft_identity import GRAPH_DELEGATED_SCOPES

    assert "Mail.Send" in GRAPH_DELEGATED_SCOPES
    assert "Mail.ReadWrite" not in GRAPH_DELEGATED_SCOPES
    assert set(GRAPH_DELEGATED_SCOPES) == {
        "User.Read", "Mail.Read", "Mail.Send", "Calendars.Read", "Files.Read.All", "Sites.Read.All"}


def test_no_draft_or_readwrite_api_is_referenced_in_the_send_path():
    """A future edit reaching for ``createReply`` would silently require a scope we do not have."""
    tree = ast.parse(Path("app/services/communications/email_send.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):                # drop docstrings; they discuss these APIs on purpose
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body.pop(0)
    code = ast.unparse(tree)                   # comments are not in the AST at all
    assert "createReply" not in code
    assert "Mail.ReadWrite" not in code
    assert "/drafts" not in code


# ============================ AUTHORIZATION ============================

def test_sending_requires_the_send_capability(token):
    thread = _seed_thread()
    weak = Principal(thread["uid"], thread["staff_email"], "A", frozenset({"communications.view"}))
    transport = Transport()
    with pytest.raises(email_send.NotAuthorized):
        _send(thread, transport, principal=weak)
    assert transport.calls == [], "authorization must fail before Graph is contacted"


def test_sending_requires_record_scope_over_the_client(token):
    """A staff user with the capability but no claim on this client cannot email them."""
    thread = _seed_thread(assign=False)
    transport = Transport()
    with pytest.raises(email_send.NotAuthorized):
        _send(thread, transport)
    assert transport.calls == []


def test_sending_requires_a_connected_mailbox_for_the_signed_in_user(token, monkeypatch):
    thread = _seed_thread()
    monkeypatch.setattr("app.services.microsoft_identity.account_for_principal",
                        lambda principal, conn=None: None)
    transport = Transport()
    with pytest.raises(email_send.NotAuthorized):
        _send(thread, transport)
    assert transport.calls == []


def test_the_reply_leaves_the_signed_in_users_own_mailbox(token):
    """``account_for_principal`` matches the principal's own address and never falls back, so a
    reply cannot be sent from a colleague's mailbox."""
    thread = _seed_thread()
    result = _send(thread, Transport())
    assert _message_row(result["message_id"])["sender_ref"] == thread["staff_email"]


def test_a_reply_records_the_sending_staff_user(token):
    thread = _seed_thread()
    row = _message_row(_send(thread, Transport())["message_id"])
    assert row["sender_user_id"] == thread["uid"]
    assert row["sender_type"] == "user"
    assert row["direction"] == "outbound"


# ============================ THE REPLY TARGET ============================

def test_the_recipient_comes_from_the_stored_thread_not_from_input(token):
    """There is no recipient parameter at all — Graph's reply addresses the original sender."""
    thread = _seed_thread()
    result = _send(thread, Transport())
    with engine.connect() as c:
        recipients = c.execute(select(communication_recipients).where(
            communication_recipients.c.message_id == result["message_id"])).mappings().all()
    assert [r["recipient_ref"] for r in recipients] == [thread["client"]]
    assert [r["recipient_role"] for r in recipients] == ["to"]


def test_a_message_without_a_graph_reference_cannot_be_replied_to(token):
    """A portal or manually logged message has no Microsoft identity to reply against."""
    thread = _seed_thread()
    with engine.begin() as c:
        c.execute(communication_message_sources.delete().where(
            communication_message_sources.c.message_id == thread["inbound_id"]))
    transport = Transport()
    with pytest.raises(email_send.SendError):
        _send(thread, transport)
    assert transport.calls == []


def test_an_empty_reply_is_refused(token):
    thread = _seed_thread()
    transport = Transport()
    with pytest.raises(email_send.SendError):
        _send(thread, transport, body="   ")
    assert transport.calls == []


# ============================ SEND INTENT (pre-send identity) ============================

def test_the_send_intent_is_durable_before_graph_is_called(token):
    """The whole design rests on this ordering: if Graph is reached, the intent is already committed
    and visible to another connection, so a crash mid-send cannot lose the record of the attempt."""
    thread = _seed_thread()
    key = email_send.new_send_key()
    seen = {}

    def _check_intent_exists():
        # A SEPARATE connection: only a committed row is visible here.
        with engine.connect() as c:
            seen["message_id"] = c.execute(select(
                communication_message_sources.c.message_id).where(
                communication_message_sources.c.source_system == email_send.INTENT_SOURCE_SYSTEM,
                communication_message_sources.c.source_external_id == key)).scalar()

    _send(thread, Transport(on_call=_check_intent_exists), send_key=key)
    assert seen["message_id"] is not None


def test_the_intent_key_is_unique_across_messages(token):
    """The idempotency guarantee is a database constraint, not application timing."""
    thread = _seed_thread()
    key = email_send.new_send_key()
    _send(thread, Transport(), send_key=key)
    with engine.connect() as c:
        count = c.execute(select(func.count()).select_from(
            communication_message_sources).where(
            communication_message_sources.c.source_system == email_send.INTENT_SOURCE_SYSTEM,
            communication_message_sources.c.source_external_id == key)).scalar()
    assert count == 1


def test_resubmitting_the_same_send_key_sends_no_second_email(token):
    """A double-clicked Send button, or a browser retry. A duplicate email cannot be withdrawn."""
    thread = _seed_thread()
    key = email_send.new_send_key()
    transport = Transport()
    first = _send(thread, transport, send_key=key)
    second = _send(thread, transport, send_key=key)
    assert len(transport.calls) == 1, "Graph must be contacted exactly once for one send key"
    assert first["message_id"] == second["message_id"]
    assert second["resent"] is False


def test_resubmitting_creates_no_second_communication_message(token):
    thread = _seed_thread()
    key = email_send.new_send_key()
    transport = Transport()
    _send(thread, transport, send_key=key)
    _send(thread, transport, send_key=key)
    with engine.connect() as c:
        count = c.execute(select(func.count()).select_from(communication_messages).where(
            communication_messages.c.direction == "outbound",
            communication_messages.c.message_metadata["send_key"].astext == key)).scalar()
    assert count == 1


def test_two_deliberate_replies_use_two_keys_and_both_send(token):
    """Idempotency must not become a lockout: a genuine second reply is a different key."""
    thread = _seed_thread()
    transport = Transport()
    a = _send(thread, transport, body="First reply.")
    b = _send(thread, transport, body="Second reply.")
    assert a["message_id"] != b["message_id"]
    assert len(transport.calls) == 2


# ============================ LIFECYCLE ============================

def test_a_successful_send_is_recorded_as_sent(token):
    thread = _seed_thread()
    result = _send(thread, Transport())
    row = _message_row(result["message_id"])
    assert result["status"] == email_send.SENT
    assert row["status"] == email_send.SENT
    assert row["sent_at"] is not None


def test_the_delivery_ledger_shows_the_transport_transitions(token):
    thread = _seed_thread()
    result = _send(thread, Transport())
    assert _deliveries(result["message_id"]) == [
        email_send.QUEUED, email_send.SENDING, email_send.SENT]


def test_sent_never_means_delivered_or_read(token):
    """Graph's 202 says ACCEPTED FOR TRANSPORT. Claiming delivery or a read receipt from that would
    put a fact in the client record that nobody observed."""
    thread = _seed_thread()
    result = _send(thread, Transport())
    row = _message_row(result["message_id"])
    assert row["delivered_at"] is None and row["read_at"] is None
    assert "delivered" not in _deliveries(result["message_id"])
    assert "read" not in _deliveries(result["message_id"])


def test_a_graph_failure_is_recorded_as_failed_and_reported(token):
    thread = _seed_thread()
    with pytest.raises(email_send.SendError):
        _send(thread, Transport(fail=True))
    with engine.connect() as c:
        row = c.execute(select(communication_messages).where(
            communication_messages.c.conversation_id == thread["conversation_id"],
            communication_messages.c.direction == "outbound").order_by(
            communication_messages.c.id.desc())).mappings().first()
    assert row["status"] == email_send.FAILED
    assert _deliveries(row["id"]) == [email_send.QUEUED, email_send.SENDING, email_send.FAILED]
    assert row["message_metadata"]["provider_identity"] == "not_sent"


def test_a_failed_send_may_be_retried_with_the_same_key(token):
    """Graph explicitly refused, so nothing left the building: retrying is safe and correct."""
    thread = _seed_thread()
    key = email_send.new_send_key()
    with pytest.raises(email_send.SendError):
        _send(thread, Transport(fail=True), send_key=key)
    good = Transport()
    result = _send(thread, good, send_key=key)
    assert len(good.calls) == 1
    assert _message_row(result["message_id"])["status"] == email_send.SENT


def test_a_send_left_uncertain_is_never_resent(token):
    """Graph accepted but the local finalization was lost — the message sits in ``sending``.

    That state is UNCERTAIN, not failed. Resending risks a duplicate the firm cannot recall, so it
    is refused and left for the Sent Items poll to resolve. Being unable to resend is the correct
    failure here: a missing reply is visible and can be sent by hand.
    """
    thread = _seed_thread()
    key = email_send.new_send_key()
    result = _send(thread, Transport(), send_key=key)
    with engine.begin() as c:                       # simulate the crash window
        c.execute(communication_messages.update().where(
            communication_messages.c.id == result["message_id"]).values(
            status=email_send.SENDING))
    transport = Transport()
    again = _send(thread, transport, send_key=key)
    assert transport.calls == [], "an uncertain send must not be repeated"
    assert again["status"] == email_send.SENDING
    assert again["resent"] is False


def test_an_already_sent_key_is_never_resent(token):
    thread = _seed_thread()
    key = email_send.new_send_key()
    _send(thread, Transport(), send_key=key)
    transport = Transport()
    _send(thread, transport, send_key=key)
    assert transport.calls == []


# ============================ BODY RETENTION ============================

def test_the_full_outbound_body_is_retained(token):
    """Inbound is stored as a 500-character preview because it is third-party content we mirror.
    Outbound is the firm's OWN words to a client — the authoritative record of what it said — so it
    is retained in full. ``communication_messages.body`` is unbounded TEXT; no new subsystem, no
    migration, and no silent truncation of correspondence."""
    thread = _seed_thread()
    body = "We reviewed the allocation. " * 200                 # ~5,400 chars, well past any preview
    result = _send(thread, Transport(), body=body)
    stored = _message_row(result["message_id"])["body"]
    assert stored == body.strip()
    assert len(stored) > email_send.PREVIEW_CHARS


def test_a_preview_is_derived_without_replacing_the_body(token):
    thread = _seed_thread()
    body = "x" * 2000
    result = _message_row(_send(thread, Transport(), body=body)["message_id"])
    preview = result["message_metadata"]["body_preview"]
    assert len(preview) == email_send.PREVIEW_CHARS
    assert preview.endswith("...")
    assert len(result["body"]) == 2000


def test_an_unreasonably_large_body_is_refused_rather_than_truncated(token):
    thread = _seed_thread()
    transport = Transport()
    with pytest.raises(email_send.SendError):
        _send(thread, transport, body="x" * (email_send.MAX_BODY_CHARS + 1))
    assert transport.calls == []


def test_the_body_sent_to_graph_is_the_body_stored(token):
    thread = _seed_thread()
    body = "Please see my notes below.\n\nRegards,\nAdvisor"
    transport = Transport()
    result = _send(thread, transport, body=body)
    assert transport.calls[0][2] == body
    assert _message_row(result["message_id"])["body"] == body


# ============================ THE GRAPH CALL ============================

def test_the_reply_targets_the_source_graph_message(token):
    thread = _seed_thread()
    transport = Transport()
    _send(thread, transport)
    assert transport.calls[0][1] == thread["graph_id"]


def test_graph_reply_posts_to_the_reply_endpoint(monkeypatch):
    """The provider-native reply: no draft, no ``createReply``, and no custom internet header —
    Microsoft does not clearly document headers on this endpoint, so reconciliation does not use one.
    """
    captured = {}

    class _Response:
        status_code = 202

    class _Requests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, json=json)
            return _Response()

    monkeypatch.setitem(__import__("sys").modules, "requests", _Requests)
    email_send.graph_reply("tok", "AAMkABC", "Hello.")
    assert captured["url"] == "https://graph.microsoft.com/v1.0/me/messages/AAMkABC/reply"
    assert captured["json"] == {"comment": "Hello."}
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert "internetMessageHeaders" not in captured["json"]
    assert "message" not in captured["json"]


def test_a_non_success_status_from_graph_raises(monkeypatch):
    class _Response:
        status_code = 403

    class _Requests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            return _Response()

    monkeypatch.setitem(__import__("sys").modules, "requests", _Requests)
    with pytest.raises(RuntimeError):
        email_send.graph_reply("tok", "AAMkABC", "Hello.")


# ============================ RECONCILIATION ============================

def _sent_copy(thread, *, subject="RE: Quarterly review", conversation=None):
    """The reply as it comes back from the mailbox's Sent Items on the next poll."""
    return _inbound_message(
        sender=thread["staff_email"], to=[thread["client"]], subject=subject,
        conversation=conversation or thread["conversation_graph_id"])


def _poll(thread, message, *, mailbox=None):
    account = _account(user_id=mailbox or thread["mailbox"], email=thread["staff_email"])
    match = email_ingest.resolve_match(
        message, {thread["client"]: (thread["pid"], thread["hid"])}, thread["staff_email"])
    with engine.begin() as c:
        message_id = email_ingest.normalize_email(c, account=account, message=message, match=match)
    if message_id is not None:
        # A deliberately-mismatched poll opens a NEW conversation; register it so teardown reaches it.
        with engine.connect() as c:
            _SEEN_CONVERSATIONS.add(c.execute(select(
                communication_messages.c.conversation_id).where(
                communication_messages.c.id == message_id)).scalar_one())
    return message_id


def test_the_sent_copy_attaches_provider_identity_to_the_existing_message(token):
    """This is what replaces the identity Graph refused to return at send time."""
    thread = _seed_thread()
    result = _send(thread, Transport())
    assert _message_row(result["message_id"])["message_metadata"]["provider_identity"] == \
        "pending_reconciliation"

    copy = _sent_copy(thread)
    reconciled_id = _poll(thread, copy)

    assert reconciled_id == result["message_id"], "no second message may be created"
    row = _message_row(result["message_id"])
    assert row["message_metadata"]["provider_identity"] == "reconciled"
    graph_sources = _sources(result["message_id"], email_send.PROVIDER_SOURCE_SYSTEM)
    assert len(graph_sources) == 1
    assert graph_sources[0]["source_external_id"] == copy["internetMessageId"]
    assert graph_sources[0]["source_metadata"]["graph_id"] == copy["id"]


def test_reconciliation_creates_no_duplicate_outbound_message(token):
    thread = _seed_thread()
    _send(thread, Transport())
    _poll(thread, _sent_copy(thread))
    with engine.connect() as c:
        count = c.execute(select(func.count()).select_from(communication_messages).where(
            communication_messages.c.conversation_id == thread["conversation_id"],
            communication_messages.c.direction == "outbound")).scalar()
    assert count == 1


def test_polling_the_sent_copy_twice_is_idempotent(token):
    """After reconciliation the ordinary UNIQUE (source_system, source_external_id) path takes over."""
    thread = _seed_thread()
    result = _send(thread, Transport())
    copy = _sent_copy(thread)
    first = _poll(thread, copy)
    second = _poll(thread, copy)
    assert first == second == result["message_id"]
    assert len(_sources(result["message_id"], email_send.PROVIDER_SOURCE_SYSTEM)) == 1


def test_reconciliation_writes_no_timeline_event(token):
    """ONE EMAIL, ONE TIMELINE ROW (ADR-074). The D.44 registry falls back to ``event_type`` alone,
    so an extra row here would double-count the exchange in every engagement timeline."""
    thread = _seed_thread()
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == thread["pid"])).scalar()
    _send(thread, Transport())
    _poll(thread, _sent_copy(thread))
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == thread["pid"])).scalar()
    assert after == before


def test_reconciliation_will_not_match_a_different_provider_conversation(token):
    """Matching is deterministic evidence, never subject text: a different thread is a different
    email, even with an identical ``RE:`` subject."""
    thread = _seed_thread()
    result = _send(thread, Transport())
    stranger = _sent_copy(thread, conversation=f"AAQk{uuid.uuid4().hex[:10]}")
    new_id = _poll(thread, stranger)
    assert new_id != result["message_id"]
    assert _message_row(result["message_id"])["message_metadata"]["provider_identity"] == \
        "pending_reconciliation"


def test_reconciliation_will_not_match_a_different_mailbox(token):
    """``conversationId`` is mailbox-scoped, so the mailbox is part of the identity."""
    thread = _seed_thread()
    result = _send(thread, Transport())
    other = _poll(thread, _sent_copy(thread), mailbox="mailbox-someone-else")
    assert other != result["message_id"]
    assert _message_row(result["message_id"])["message_metadata"]["provider_identity"] == \
        "pending_reconciliation"


def test_an_already_reconciled_message_is_not_matched_again(token):
    """A later reply in the same thread must not steal the first reply's identity."""
    thread = _seed_thread()
    first = _send(thread, Transport())
    _poll(thread, _sent_copy(thread))
    second = _send(thread, Transport(), body="A later reply.")
    copy2 = _sent_copy(thread, subject="RE: Quarterly review (2)")
    assert _poll(thread, copy2) == second["message_id"]
    assert len(_sources(first["message_id"], email_send.PROVIDER_SOURCE_SYSTEM)) == 1


def test_an_uncertain_send_is_resolved_by_the_sent_copy(token):
    """The crash-window recovery path: ``sending`` + a sent copy becomes a reconciled ``sent``."""
    thread = _seed_thread()
    result = _send(thread, Transport())
    with engine.begin() as c:
        c.execute(communication_messages.update().where(
            communication_messages.c.id == result["message_id"]).values(status=email_send.SENDING))
    assert _poll(thread, _sent_copy(thread)) == result["message_id"]
    row = _message_row(result["message_id"])
    assert row["status"] == email_send.SENT
    assert row["message_metadata"]["provider_identity"] == "reconciled"


def test_an_ordinary_inbound_email_still_normalizes_normally(token):
    """Batch 4a behaviour is untouched by the outbound path."""
    thread = _seed_thread()
    second = _inbound_message(sender=thread["client"], to=[thread["staff_email"]],
                              subject="One more thing")
    new_id = _poll(thread, second)
    assert new_id is not None and new_id != thread["inbound_id"]
    assert _message_row(new_id)["direction"] == "inbound"
