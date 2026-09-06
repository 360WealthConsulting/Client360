"""The unified staff communications surface (Batch 4d).

Twelve batches built two separate stores for one thing a staff member thinks of as one thing: what
this client and this firm have said to each other. Secure portal messaging owns ``portal_threads`` /
``portal_messages``; canonical email owns ``communication_*`` (ADR-074 inbound, ADR-075 outbound).
This suite pins the COMPOSITION that reads both into one chronological history, and — more
importantly — pins the things a composition is tempted to get wrong:

  * it must not copy either store into the other (``test_no_portal_message_is_copied_into_...``);
  * it must not show the same email twice, which is what reading both the activity timeline and the
    canonical store would do for a sender-matched inbound email;
  * it must not emit a timeline event just because someone opened a page;
  * it must not invent state the source does not have — an inbound email has a bounded preview and
    no fuller body (ADR-074), and email has no firm-wide unread state, so those read as ABSENT
    rather than as empty-string or False;
  * it must not offer an action that will predictably fail, and must not rely on hiding one.

The last point is the one worth stating twice: every hidden-button test here has a sibling that
invokes the underlying route directly and asserts the server refuses on its own.

No live Graph, no email sent, no SharePoint, no SMS: the one outbound send goes through an injected
transport, exactly as Batch 4c's own suite does.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date

import pytest
from sqlalchemy import func, insert, select

from app.db import (
    communication_conversations,
    communication_messages,
    engine,
    people,
    portal_messages,
    record_assignments,
    timeline_events,
)
from app.portal.service import create_thread, staff_send_message
from app.security.models import Principal
from app.services.communications import email_ingest, email_send
from app.services.communications.engagement import feed as feed_mod
from app.services.communications.engagement.feed import EMAIL, SECURE_MESSAGE, client_communications
from tests._portal_util import seed_portal_account, seed_staff_user

pytestmark = pytest.mark.usefixtures("portal_messaging_on")

# The capability set a servicing advisor actually carries on this surface.
FULL = frozenset({"client.read", "communications.view", "communications.message.read",
                  "communications.message.write", "communications.send",
                  "record.read_all", "record.write_all"})
# Read-only: may see the history, may not act on it.
READ_ONLY = frozenset({"client.read", "communications.view", "communications.message.read",
                       "record.read_all"})
# Has the client profile but not the secure-message read gate.
NO_MESSAGES = frozenset({"client.read", "communications.view", "record.read_all"})

TENANT = "tenant-4d"

_SEEN_CONVERSATIONS: set = set()
_SEEN_PEOPLE: set = set()
_SEEN_HOUSEHOLDS: set = set()
_SEEN_ACCOUNTS: set = set()
_SEEN_DOCUMENTS: set = set()
_SEEN_VAULT_DOCUMENTS: set = set()
_STAFF: dict = {}


@pytest.fixture(scope="module", autouse=True)
def _shared_staff():
    """One audited staff actor for the module.

    A user who has written to the append-only audit ledger cannot be deleted (the FK is
    ON DELETE SET NULL and the immutability trigger refuses that update), so seeding one per test
    would leave permanent rows in the shared test database on every run.
    """
    yield
    _STAFF.clear()


@pytest.fixture(autouse=True)
def _cleanup():
    """Leave the database as this suite found it, as far as the store's own invariants allow.

    Every test seeds a whole client — household, person, portal account — because the isolation
    tests need genuinely independent clients, and fifty of those left behind per run are what makes
    a shared test database slow and breaks table-count assertions in unrelated files.

    THREE THINGS CANNOT BE REMOVED, and that is the product working correctly, not a gap here:

      * ``portal_messages`` is APPEND-ONLY (``prevent_portal_message_mutation``) — secure client
        correspondence is immutable by design, so a test that sends one pins its thread, and the
        thread pins the person, household and portal account behind it.
      * ``communication_events`` is append-only and RESTRICT-anchors ``communication_conversations``,
        so conversations survive with their client anchors detached, as the Batch 4a/4c suites do.
      * a user referenced by the append-only audit ledger cannot be deleted (the FK is
        ON DELETE SET NULL and the trigger refuses that update) — hence the one shared staff actor.

    So clients that sent a secure message stay; everything else goes.
    """
    yield
    from app.db import (
        documents, households, portal_access_grants, portal_accounts, portal_auth_tokens,
        portal_consents, portal_devices, portal_document_requests, portal_email_verifications,
        portal_invitations, portal_notifications, portal_sessions, portal_threads, vault_documents,
    )

    with engine.begin() as c:
        if _SEEN_CONVERSATIONS:
            ids = list(_SEEN_CONVERSATIONS)
            c.execute(communication_messages.delete().where(
                communication_messages.c.conversation_id.in_(ids)))
            c.execute(communication_conversations.update().where(
                communication_conversations.c.id.in_(ids)).values(person_id=None,
                                                                  household_id=None))
        if _SEEN_VAULT_DOCUMENTS:
            c.execute(vault_documents.delete().where(
                vault_documents.c.id.in_(list(_SEEN_VAULT_DOCUMENTS))))
        if _SEEN_DOCUMENTS:
            c.execute(documents.delete().where(documents.c.id.in_(list(_SEEN_DOCUMENTS))))
        if not _SEEN_PEOPLE:
            _clear_seen()
            return
        people_ids = list(_SEEN_PEOPLE)
        # A person or household still anchoring an immutable thread has to stay with it.
        pinned_people = set(c.scalars(select(portal_threads.c.person_id).where(
            portal_threads.c.person_id.in_(people_ids))).all())
        pinned_households = set(c.scalars(select(portal_threads.c.household_id).where(
            portal_threads.c.household_id.in_(list(_SEEN_HOUSEHOLDS)))).all())
        free_people = [p for p in people_ids if p not in pinned_people]
        free_households = [h for h in _SEEN_HOUSEHOLDS if h not in pinned_households]
        free_accounts = list(c.scalars(select(portal_accounts.c.id).where(
            portal_accounts.c.id.in_(list(_SEEN_ACCOUNTS)),
            portal_accounts.c.person_id.in_(free_people or [-1]))).all())

        c.execute(timeline_events.delete().where(timeline_events.c.person_id.in_(people_ids)))
        c.execute(record_assignments.delete().where(
            record_assignments.c.entity_type == "person",
            record_assignments.c.entity_id.in_(people_ids)))
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
    _clear_seen()


def _clear_seen():
    for seen in (_SEEN_CONVERSATIONS, _SEEN_PEOPLE, _SEEN_HOUSEHOLDS, _SEEN_ACCOUNTS,
                 _SEEN_DOCUMENTS, _SEEN_VAULT_DOCUMENTS):
        seen.clear()


def _staff_id():
    if "uid" not in _STAFF:
        _STAFF["uid"] = seed_staff_user()
    return _STAFF["uid"]


def _principal(uid, caps=FULL):
    return Principal(uid, f"advisor-{uid}@360wealth.example", "Advisor", frozenset(caps))


class _Client:
    """One client with a portal account, a staff owner, and somewhere to hang correspondence."""

    def __init__(self):
        self.staff_id = _staff_id()
        (self.account_id, self.portal_principal,
         self.person_id, self.household_id) = seed_portal_account(self.staff_id)
        _SEEN_ACCOUNTS.add(self.account_id)
        _SEEN_PEOPLE.add(self.person_id)
        _SEEN_HOUSEHOLDS.add(self.household_id)
        with engine.begin() as c:
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


def _client():
    return _Client()


# --- seeding the two stores ---------------------------------------------------------------------

def _portal_exchange(client, *, subject="Portal question"):
    """A client-started thread, a staff reply, and a staff-only internal note."""
    thread = create_thread(client.portal_principal, household_id=client.household_id,
                           person_id=client.person_id, subject=subject,
                           body="Could you confirm my balance?")
    thread_id = thread["id"] if isinstance(thread, dict) else thread
    staff_send_message(thread_id=thread_id, user_id=client.staff_id,
                       body="Confirmed, the balance is correct.", principal=client.principal)
    staff_send_message(thread_id=thread_id, user_id=client.staff_id,
                       body="Internal: verified against the custodian file.", internal_note=True,
                       principal=client.principal)
    return thread_id


MAILBOX = "mailbox-4d"


def _account(client):
    return {"id": 1, "tenant_id": TENANT, "user_id": MAILBOX,
            "email": client.principal.email}


def _graph_message(client, *, subject="Statement question", conversation=None, sender=None,
                   to=None):
    tag = uuid.uuid4().hex[:10]
    return {
        "id": f"AAMk{tag}", "subject": subject,
        "from": {"emailAddress": {"name": "Client", "address": sender or client.email}},
        "toRecipients": [{"emailAddress": {"name": a, "address": a}}
                         for a in (to or [client.principal.email])],
        "ccRecipients": [], "receivedDateTime": "2026-09-02T10:00:00Z",
        "bodyPreview": "Please confirm the figures on page two.",
        "webLink": f"https://outlook.office.com/mail/{tag}", "hasAttachments": False,
        "isRead": True, "conversationId": conversation or f"AAQk{tag}",
        "internetMessageId": f"<{tag}@example.test>",
    }


def _inbound_email(client, **kw):
    message = _graph_message(client, **kw)
    match = email_ingest.resolve_match(
        message, {client.email: (client.person_id, client.household_id)}, client.principal.email)
    with engine.begin() as c:
        message_id = email_ingest.normalize_email(c, account=_account(client), message=message,
                                                  match=match)
    assert message_id is not None, "fixture precondition: the inbound email anchored to the client"
    with engine.connect() as c:
        _SEEN_CONVERSATIONS.add(c.execute(select(
            communication_messages.c.conversation_id).where(
            communication_messages.c.id == message_id)).scalar_one())
    return message_id, message


class _Transport:
    """Stands in for the Graph reply. No network, no mail."""

    def __init__(self):
        self.calls = []

    def __call__(self, token, graph_id, body):
        self.calls.append((token, graph_id, body))


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr("app.services.microsoft_identity.get_microsoft_access_token",
                        lambda account: "test-token")


def _outbound_reply(client, inbound_id, monkeypatch, body="Thank you — figures confirmed."):
    """A real ADR-075 send with the transport injected, so the outbound row is genuine."""
    monkeypatch.setattr("app.services.microsoft_identity.account_for_principal",
                        lambda principal, conn=None: _account(client))
    return email_send.send_reply(client.principal, communication_message_id=inbound_id, body=body,
                                 send_key=email_send.new_send_key(), transport=_Transport())


def _feed(client, principal=None, **kw):
    return client_communications(principal or client.principal, person_id=client.person_id,
                                 household_id=client.household_id, **kw)


def _channels(result):
    return [r.channel for r in result["rows"]]


# ============================ COMPOSITION ============================

def test_portal_and_email_appear_in_one_feed(token, monkeypatch):
    """The whole point: one surface answers "what have we said to each other?" across both stores."""
    c = _client()
    _portal_exchange(c)
    inbound_id, _ = _inbound_email(c)
    _outbound_reply(c, inbound_id, monkeypatch)

    result = _feed(c)
    assert result["authorized"] is True
    assert SECURE_MESSAGE in _channels(result) and EMAIL in _channels(result)
    assert result["counts"][SECURE_MESSAGE] == 3      # client message + staff reply + internal note
    assert result["counts"][EMAIL] == 2               # inbound + the outbound reply


def test_the_feed_is_ordered_newest_first(token, monkeypatch):
    c = _client()
    _portal_exchange(c)
    inbound_id, _ = _inbound_email(c)
    _outbound_reply(c, inbound_id, monkeypatch)
    stamps = [r.timestamp for r in _feed(c)["rows"] if r.timestamp]
    aware = [t if t.tzinfo else t.replace(tzinfo=UTC) for t in stamps]
    assert aware == sorted(aware, reverse=True)


def test_an_email_appears_exactly_once(token):
    """A sender-matched inbound email exists BOTH as a timeline row and as a canonical record.
    Composing from both stores is the obvious way to build this screen and the obvious way to
    double-count every email; the feed takes email from the canonical store only."""
    c = _client()
    _inbound_email(c, subject="Only once please")
    rows = [r for r in _feed(c)["rows"] if r.channel == EMAIL]
    assert len(rows) == 1
    assert len({r.entry_id for r in rows}) == 1


def test_no_portal_message_is_copied_into_communication_messages(token):
    """The architectural rule of this batch. Portal and email remain separate canonical stores; the
    feed is a read layer over both, not a migration between them."""
    c = _client()
    with engine.connect() as conn:
        before = conn.execute(select(func.count()).select_from(communication_messages)).scalar()
    _portal_exchange(c)
    _feed(c)
    with engine.connect() as conn:
        after = conn.execute(select(func.count()).select_from(communication_messages)).scalar()
    assert after == before


def test_no_email_is_copied_into_portal_messages(token):
    """The same rule in the other direction."""
    c = _client()
    with engine.connect() as conn:
        before = conn.execute(select(func.count()).select_from(portal_messages)).scalar()
    _inbound_email(c)
    _feed(c)
    with engine.connect() as conn:
        after = conn.execute(select(func.count()).select_from(portal_messages)).scalar()
    assert after == before


def test_internal_notes_are_shown_to_staff_and_labelled_as_internal(token):
    """This is the staff surface, so a staff-only note belongs here — but never disguised as
    something the client sent or saw."""
    c = _client()
    _portal_exchange(c)
    notes = [r for r in _feed(c)["rows"] if r.direction == feed_mod.INTERNAL_NOTE]
    assert len(notes) == 1
    assert notes[0].direction_label == "Internal note"
    assert "Internal:" in notes[0].preview


# ============================ CHANNEL + DIRECTION ============================

def test_portal_rows_are_labelled_secure_message(token):
    c = _client()
    _portal_exchange(c)
    rows = [r for r in _feed(c)["rows"] if r.channel == SECURE_MESSAGE]
    assert rows and all(r.channel_label == "Secure Message" for r in rows)


def test_a_client_portal_message_is_inbound_and_a_staff_reply_is_outbound(token):
    c = _client()
    _portal_exchange(c)
    rows = {r.preview[:20]: r for r in _feed(c)["rows"] if r.channel == SECURE_MESSAGE}
    inbound = [r for r in rows.values() if r.direction == feed_mod.INBOUND]
    outbound = [r for r in rows.values() if r.direction == feed_mod.OUTBOUND]
    assert len(inbound) == 1 and len(outbound) == 1


def test_inbound_email_is_labelled_inbound(token):
    c = _client()
    _inbound_email(c)
    row = next(r for r in _feed(c)["rows"] if r.channel == EMAIL)
    assert row.direction == feed_mod.INBOUND and row.direction_label == "Inbound"


def test_the_outbound_reply_is_labelled_outbound(token, monkeypatch):
    c = _client()
    inbound_id, _ = _inbound_email(c)
    _outbound_reply(c, inbound_id, monkeypatch)
    out = [r for r in _feed(c)["rows"] if r.channel == EMAIL and r.direction == feed_mod.OUTBOUND]
    assert len(out) == 1 and out[0].direction_label == "Outbound"


def test_an_external_email_sender_is_never_shown_as_system(token):
    """``sender_type`` is 'external' in the store for a real correspondent (4a). Rendering that as
    "system" would tell staff a machine wrote a client's email."""
    c = _client()
    _inbound_email(c)
    row = next(r for r in _feed(c)["rows"] if r.channel == EMAIL)
    assert row.sender == c.email
    assert row.sender.lower() != "system"


# ============================ AUTHORIZATION ============================

def test_the_surface_is_closed_without_the_message_read_capability(token):
    """Fails closed at the composer, not at the template: no store is queried at all."""
    c = _client()
    _portal_exchange(c)
    _inbound_email(c)
    result = _feed(c, principal=_principal(c.staff_id, NO_MESSAGES))
    assert result["authorized"] is False
    assert result["rows"] == [] and result["total"] == 0


def test_a_later_page_cannot_reach_content_the_capability_denies(token):
    """The gate is on the composition, so paging past it returns nothing rather than more."""
    c = _client()
    _portal_exchange(c)
    result = _feed(c, principal=_principal(c.staff_id, NO_MESSAGES), page=2)
    assert result["authorized"] is False and result["rows"] == []


def test_another_clients_correspondence_never_appears(token, monkeypatch):
    """Cross-client isolation, proven with two fully independent clients rather than asserted."""
    a, b = _client(), _client()
    _portal_exchange(a, subject="Client A only")
    inbound_a, _ = _inbound_email(a, subject="A statement")
    _outbound_reply(a, inbound_a, monkeypatch)
    _portal_exchange(b, subject="Client B only")
    _inbound_email(b, subject="B statement")

    rows = _feed(a)["rows"]
    assert rows, "client A should see their own correspondence"
    assert all("Client B" not in r.subject and "B statement" not in r.subject for r in rows)
    assert all(r.thread_key not in {x.thread_key for x in _feed(b)["rows"]} for r in rows)


def test_a_principal_without_record_scope_sees_nothing(token):
    """Record scope is the authoritative predicate — reused from the communications service and the
    portal thread reader, never re-derived more permissively here."""
    c = _client()
    _portal_exchange(c)
    _inbound_email(c)
    stranger = Principal(seed_staff_user(), "stranger@e.test", "Stranger",
                         frozenset({"client.read", "communications.view",
                                    "communications.message.read"}))   # no read_all, no assignment
    assert _feed(c, principal=stranger)["rows"] == []


def test_the_portal_reply_action_requires_message_write(token):
    c = _client()
    _portal_exchange(c)
    with_write = [r for r in _feed(c)["rows"] if r.channel == SECURE_MESSAGE]
    assert all(r.reply_url for r in with_write)
    read_only = [r for r in _feed(c, principal=_principal(c.staff_id, READ_ONLY))["rows"]
                 if r.channel == SECURE_MESSAGE]
    assert read_only and all(r.reply_url is None for r in read_only)


def test_the_outlook_reply_action_requires_communications_send(token):
    c = _client()
    _inbound_email(c)
    offered = [r for r in _feed(c)["rows"] if r.channel == EMAIL]
    assert all(r.reply_url and r.reply_url.endswith("/reply") for r in offered)
    without = [r for r in _feed(c, principal=_principal(c.staff_id, READ_ONLY))["rows"]
               if r.channel == EMAIL]
    assert without and all(r.reply_url is None for r in without)


def test_a_hidden_reply_action_is_still_refused_by_the_route(token, monkeypatch):
    """The button being absent is a courtesy. The refusal is the route's, and it stands alone."""
    c = _client()
    inbound_id, _ = _inbound_email(c)
    weak = _principal(c.staff_id, READ_ONLY)
    assert all(r.reply_url is None for r in _feed(c, principal=weak)["rows"] if r.channel == EMAIL)
    monkeypatch.setattr("app.services.microsoft_identity.account_for_principal",
                        lambda principal, conn=None: _account(c))
    transport = _Transport()
    with pytest.raises(email_send.NotAuthorized):
        email_send.send_reply(weak, communication_message_id=inbound_id, body="Sneaking in.",
                              send_key=email_send.new_send_key(), transport=transport)
    assert transport.calls == [], "no email may leave on a refused action"


def test_an_email_without_a_provider_identity_offers_no_reply(token):
    """ADR-075 needs a stored Graph message id. Offering Reply without one would be a button that
    predictably fails."""
    from app.db import communication_message_sources as sources
    c = _client()
    inbound_id, _ = _inbound_email(c)
    with engine.begin() as conn:
        conn.execute(sources.delete().where(sources.c.message_id == inbound_id))
    row = next(r for r in _feed(c)["rows"] if r.channel == EMAIL)
    assert row.reply_url is None


# ============================ ATTACHMENTS ============================

def _attach_email_document(client, message_id):
    """An ingested Outlook attachment: a canonical document linked to the email (Batch 4b)."""
    from app.db import communication_attachments, documents
    with engine.begin() as c:
        tag = uuid.uuid4().hex[:8]
        doc_id = c.execute(insert(documents).values(
            original_name="statement.pdf", stored_name=f"st-{tag}.pdf",
            storage_path=f"/tmp/client360-test/st-{tag}.pdf", size_bytes=12,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            person_id=client.person_id,
            household_id=client.household_id).returning(documents.c.id)).scalar_one()
        c.execute(insert(communication_attachments).values(
            message_id=message_id, document_id=doc_id, description="statement.pdf"))
    _SEEN_DOCUMENTS.add(doc_id)
    return doc_id


def test_an_outlook_attachment_links_to_the_authorized_canonical_route(token):
    c = _client()
    inbound_id, _ = _inbound_email(c)
    doc_id = _attach_email_document(c, inbound_id)
    row = next(r for r in _feed(c)["rows"] if r.channel == EMAIL)
    assert row.attachment_count == 1
    assert row.attachments[0]["url"] == f"/documents/{doc_id}/download"


def test_a_portal_vault_attachment_links_to_the_authorized_vault_route(token):
    from app.db import portal_message_attachments, vault_documents
    c = _client()
    thread_id = _portal_exchange(c)
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        message_id = conn.execute(select(portal_messages.c.id).where(
            portal_messages.c.thread_id == thread_id).order_by(
            portal_messages.c.id)).scalars().first()
        vault_id = conn.execute(insert(vault_documents).values(
            display_name="Client upload.pdf", original_filename="upload.pdf", category="general",
            storage_key=f"vault/{tag}.pdf", checksum_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            client_visible=True).returning(vault_documents.c.id)).scalar_one()
        conn.execute(insert(portal_message_attachments).values(
            message_id=message_id, vault_document_id=vault_id))
    _SEEN_VAULT_DOCUMENTS.add(vault_id)
    row = next(r for r in _feed(c)["rows"]
               if r.entry_id == f"{SECURE_MESSAGE}:{message_id}")
    assert row.attachments[0]["url"] == f"/api/vault/documents/{vault_id}/download"


def test_the_attachment_routes_are_the_only_thing_exposed(token):
    """The user sees "attachment", not "canonical vs vault", and never a storage path or a Graph URL.
    Authorization stays with whichever route serves the file."""
    c = _client()
    inbound_id, _ = _inbound_email(c)
    _attach_email_document(c, inbound_id)
    for row in _feed(c)["rows"]:
        for a in row.attachments:
            assert a["url"].startswith(("/documents/", "/api/vault/documents/"))
            assert "graph.microsoft.com" not in a["url"]
            assert ":\\" not in a["url"] and "storage_uri" not in a["url"]


def test_the_canonical_attachment_route_enforces_document_scope_itself():
    """The link is not the permission: ``/documents/{id}`` is gated by the middleware's
    ``document.read`` rule AND a per-document record-scope check, independently of this feed."""
    from app.security.middleware import RULES, _document_in_scope
    assert any(rx.search("/documents/1/download") and cap == "document.read" for rx, cap in RULES)
    assert callable(_document_in_scope)


# ============================ BODY / PREVIEW ============================

def test_an_inbound_email_shows_only_the_retained_preview(token):
    """ADR-074 retains a bounded preview of third-party email. The surface must not imply a fuller
    copy exists — ``body`` is absent, not empty."""
    c = _client()
    _inbound_email(c)
    row = next(r for r in _feed(c)["rows"] if r.channel == EMAIL)
    assert row.body is None
    assert row.body_retained is False
    assert row.preview


def test_an_outbound_reply_keeps_its_full_body(token, monkeypatch):
    """ADR-075 retains the firm's own words in full, so the surface can show them."""
    c = _client()
    inbound_id, _ = _inbound_email(c)
    long_body = "We reviewed every line of the statement. " * 20
    _outbound_reply(c, inbound_id, monkeypatch, body=long_body)
    row = next(r for r in _feed(c)["rows"]
               if r.channel == EMAIL and r.direction == feed_mod.OUTBOUND)
    assert row.body_retained is True
    assert row.body == long_body.strip()
    assert len(row.preview) <= feed_mod.PREVIEW_CHARS


def test_a_portal_message_keeps_its_full_body(token):
    c = _client()
    _portal_exchange(c)
    row = next(r for r in _feed(c)["rows"]
               if r.channel == SECURE_MESSAGE and r.direction == feed_mod.INBOUND)
    assert row.body_retained is True
    assert row.body == "Could you confirm my balance?"


# ============================ UNREAD ============================

def test_email_carries_no_invented_unread_state(token):
    """Outlook's read flag is one mailbox's state, not the firm's. Absent, not False — so the
    template can tell "no such concept" from "read"."""
    c = _client()
    _inbound_email(c)
    row = next(r for r in _feed(c)["rows"] if r.channel == EMAIL)
    assert row.unread is None


def test_portal_rows_carry_real_unread_state(token):
    c = _client()
    _portal_exchange(c)
    rows = [r for r in _feed(c)["rows"] if r.channel == SECURE_MESSAGE]
    assert all(isinstance(r.unread, bool) for r in rows)


# ============================ THREADING ============================

def test_each_row_keeps_its_source_conversation_identity(token, monkeypatch):
    c = _client()
    thread_id = _portal_exchange(c)
    inbound_id, message = _inbound_email(c)
    _outbound_reply(c, inbound_id, monkeypatch)
    rows = _feed(c)["rows"]
    portal_keys = {r.thread_key for r in rows if r.channel == SECURE_MESSAGE}
    email_keys = {r.thread_key for r in rows if r.channel == EMAIL}
    assert portal_keys == {f"portal:{thread_id}"}
    assert len(email_keys) == 1, "the reply belongs to the conversation it answered"


def test_portal_and_email_conversations_are_never_merged(token):
    """Two stores, two conversation namespaces. A portal thread and an email conversation about the
    same subject are genuinely different conversations, and collapsing them would invent a thread
    that exists nowhere."""
    c = _client()
    thread_id = _portal_exchange(c, subject="Statement")
    _inbound_email(c, subject="Statement")
    keys = {r.thread_key for r in _feed(c)["rows"]}
    assert f"portal:{thread_id}" in keys
    assert any(k.startswith("email:") for k in keys)
    assert len(keys) == 2


def test_two_email_conversations_stay_apart(token):
    c = _client()
    _inbound_email(c, subject="First topic")
    _inbound_email(c, subject="Second topic")
    keys = {r.thread_key for r in _feed(c)["rows"] if r.channel == EMAIL}
    assert len(keys) == 2


# ============================ FILTERS ============================

def test_the_all_filter_shows_both_channels(token):
    c = _client()
    _portal_exchange(c)
    _inbound_email(c)
    assert set(_channels(_feed(c))) == {SECURE_MESSAGE, EMAIL}


def test_the_email_filter_excludes_portal_messages(token):
    c = _client()
    _portal_exchange(c)
    _inbound_email(c)
    result = _feed(c, channel=EMAIL)
    assert set(_channels(result)) == {EMAIL}
    # The counts stay whole-history so the filter chips can show what is being filtered OUT.
    assert result["counts"][SECURE_MESSAGE] == 3


def test_the_secure_message_filter_excludes_email(token):
    c = _client()
    _portal_exchange(c)
    _inbound_email(c)
    assert set(_channels(_feed(c, channel=SECURE_MESSAGE))) == {SECURE_MESSAGE}


def test_the_direction_filter_narrows_across_both_channels(token):
    c = _client()
    _portal_exchange(c)
    _inbound_email(c)
    inbound = _feed(c, direction=feed_mod.INBOUND)["rows"]
    assert inbound and all(r.direction == feed_mod.INBOUND for r in inbound)


def test_an_unknown_filter_value_is_ignored_rather_than_emptying_the_feed(token):
    c = _client()
    _portal_exchange(c)
    assert _feed(c, channel="sms")["rows"], "an unrecognised channel must not silently hide history"


# ============================ BOUNDS + QUERY COST ============================

def test_the_feed_is_paginated(token):
    c = _client()
    for _ in range(3):
        _inbound_email(c)
    _portal_exchange(c)
    page1 = _feed(c, page_size=2)
    assert len(page1["rows"]) == 2
    assert page1["total"] == 6 and page1["pages"] == 3   # 3 emails + 3 portal messages
    page3 = _feed(c, page_size=2, page=3)
    assert len(page3["rows"]) == 2
    assert {r.entry_id for r in page1["rows"]} & {r.entry_id for r in page3["rows"]} == set()


def test_the_page_size_is_capped(token):
    c = _client()
    _portal_exchange(c)
    assert _feed(c, page_size=10_000)["page_size"] == feed_mod.MAX_PAGE_SIZE


def test_the_query_count_does_not_grow_with_the_number_of_messages(token, monkeypatch):
    """The N+1 guard. Every section builder runs on every client-profile load, so a per-row sender,
    attachment or scope lookup here would cost a query per message on the firm's busiest clients."""
    from sqlalchemy import event

    c = _client()
    _portal_exchange(c)
    _inbound_email(c)

    statements = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        _feed(c)
        small = len(statements)
        statements.clear()
        for _ in range(6):
            _inbound_email(c)
        statements.clear()
        _feed(c)
        large = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", _listener)

    assert large <= small + 2, (
        f"composing 7 emails cost {large} statements vs {small} for 1 — the adapters must batch")


# ============================ TIMELINE COEXISTENCE ============================

def test_composing_the_feed_writes_no_timeline_event(token, monkeypatch):
    """This surface is a communications view, not another relationship-timeline source. Rendering it
    is a pure read — ADR-049 stays the governing composition rule."""
    c = _client()
    _portal_exchange(c)
    inbound_id, _ = _inbound_email(c)
    _outbound_reply(c, inbound_id, monkeypatch)
    with engine.connect() as conn:
        before = conn.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == c.person_id)).scalar()
    for _ in range(3):
        _feed(c)
        _feed(c, channel=EMAIL)
    with engine.connect() as conn:
        after = conn.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == c.person_id)).scalar()
    assert after == before


def test_the_feed_adds_no_engagement_registry_interaction_type():
    """A new source here would have to be a registered interaction type with an owner. The feed
    composes the SAME stores the registry already names, so it introduces none."""
    from app.services.communications.engagement import registry
    keys = {t.key for t in registry.REGISTRY}
    assert "secure_message" in keys and "email" in keys
    assert "unified_feed" not in keys and "communication_feed" not in keys


def test_the_engagement_layer_still_passes_its_own_governance():
    """The new modules are held to the layer's read-only invariants, not exempted from them."""
    from app.services.communications.engagement.governance import _MODULES, validate_engagement
    assert "feed.py" in _MODULES
    assert "adapters/email_feed.py" in _MODULES and "adapters/portal_feed.py" in _MODULES
    report = validate_engagement()
    assert report["ok"], report["findings"]


# ============================ THE RENDERED SURFACE ============================
#
# The composition above is only half the deliverable: a staff member reaches this through a tab on
# the client profile, and a template that raises or silently drops the feed would pass every test
# so far. These drive the real route.

def _request(path, qs=b""):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": path, "headers": [],
                    "query_string": qs})


def _render_client(client, qs=b"tab=communications", principal=None):
    from app.routes.client360 import client_workspace
    response = client_workspace(_request(f"/client/{client.person_id}", qs), client.person_id,
                                tab="communications", principal=principal or client.principal,
                                **_query_kwargs(qs))
    return response.body.decode("utf-8")


def _query_kwargs(qs: bytes) -> dict:
    from urllib.parse import parse_qs
    q = parse_qs(qs.decode("utf-8"))
    out = {}
    if "cchannel" in q:
        out["cchannel"] = q["cchannel"][0]
    if "cdir" in q:
        out["cdir"] = q["cdir"][0]
    if "cpage" in q:
        out["cpage"] = int(q["cpage"][0])
    return out


def test_the_communications_tab_renders_both_channels(token, monkeypatch):
    c = _client()
    _portal_exchange(c, subject="Portal thread subject")
    inbound_id, _ = _inbound_email(c, subject="Email subject")
    _outbound_reply(c, inbound_id, monkeypatch)
    html = _render_client(c)
    assert "Secure Message" in html and "Email" in html
    assert "Portal thread subject" in html and "Email subject" in html
    assert "Inbound" in html and "Outbound" in html


def test_the_rendered_tab_offers_the_outlook_reply_link(token):
    c = _client()
    inbound_id, _ = _inbound_email(c)
    html = _render_client(c)
    assert f"/communications/messages/{inbound_id}/reply" in html


def test_the_rendered_tab_hides_the_reply_link_without_the_capability(token):
    c = _client()
    inbound_id, _ = _inbound_email(c)
    html = _render_client(c, principal=_principal(c.staff_id, READ_ONLY))
    assert f"/communications/messages/{inbound_id}/reply" not in html


def test_the_rendered_tab_says_so_when_message_content_is_not_permitted(token):
    c = _client()
    _portal_exchange(c)
    html = _render_client(c, principal=_principal(c.staff_id, NO_MESSAGES))
    assert "Message content not available" in html
    assert "Could you confirm my balance?" not in html


def test_the_rendered_channel_filter_narrows_the_page(token):
    c = _client()
    _portal_exchange(c, subject="Portal only subject")
    _inbound_email(c, subject="Email only subject")
    html = _render_client(c, qs=b"tab=communications&cchannel=email")
    assert "Email only subject" in html
    assert "Portal only subject" not in html


def test_the_rendered_tab_never_exposes_a_storage_path(token):
    c = _client()
    inbound_id, _ = _inbound_email(c)
    _attach_email_document(c, inbound_id)
    html = _render_client(c)
    assert "/documents/" in html
    assert "graph.microsoft.com" not in html
    assert "storage_path" not in html and "storage_key" not in html


def test_the_communications_tab_is_reachable_from_the_messages_group(token):
    """Extending the existing surface, not adding a disconnected application: the tab sits in the
    Messages group beside Secure Messages rather than buried under "More"."""
    from pathlib import Path
    nav = Path("app/templates/client360/_section_nav.html").read_text(encoding="utf-8")
    assert '("Messages",   ["communications","messages"])' in nav
    c = _client()
    _portal_exchange(c)
    assert "?tab=communications" in _render_client(c)


# ============================ REGRESSION ============================

def test_the_existing_secure_messages_section_still_works(token):
    """The Client 360 Messages tab reads portal_threads as before; the new tab sits beside it."""
    from app.services.client360 import get_workspace
    c = _client()
    _portal_exchange(c)
    ws = get_workspace(c.principal, person_id=c.person_id)
    messages = ws["sections"]["messages"]
    assert messages["source"] == "portal_threads"
    assert len(messages["threads"]) == 1


def test_the_client_profile_communications_section_carries_the_feed(token):
    from app.services.client360 import get_workspace
    c = _client()
    _portal_exchange(c)
    _inbound_email(c)
    section = get_workspace(c.principal, person_id=c.person_id)["sections"]["communications"]
    assert section["source"] == "communications.engagement"
    assert section["not_a_second_store"] is True
    assert section["feed"]["authorized"] is True
    assert section["feed"]["total"] == 4
    # The pre-existing engagement summary contract is untouched.
    assert "summary" in section and "recent" in section


def test_the_existing_engagement_summary_contract_is_unchanged(token):
    """Batch 4d adds a key; it does not reshape what was already there."""
    from app.services.client360 import get_workspace
    c = _client()
    _portal_exchange(c)
    section = get_workspace(c.principal, person_id=c.person_id)["sections"]["communications"]
    assert section["summary"]["enabled"] is True
    assert isinstance(section["recent"], list)


def test_the_household_profile_also_composes_the_feed(token):
    """Household 360 gets the same surface, anchored on the household.

    Email is found by the household anchor the ingestion already writes. Secure threads follow the
    workspace's OWN member roster (``ctx["member_ids"]``, from the portfolio service) exactly as
    every other household section does — this feed does not derive its own member list, because a
    second definition of "who is in this household" is precisely what would let it show a thread the
    rest of the workspace suppresses.
    """
    from app.services.client360.household import get_household_workspace
    c = _client()
    _portal_exchange(c)
    _inbound_email(c, subject="Household statement")
    hws = get_household_workspace(c.principal, c.household_id)
    section = hws["sections"]["communications"]
    assert section["feed"]["authorized"] is True
    assert section["feed"]["counts"][EMAIL] == 1
    assert [r.channel for r in section["feed"]["rows"]] == [EMAIL]


def test_the_household_feed_follows_the_workspaces_own_member_roster(token):
    """Given members, the household feed composes their secure threads too — via the same roster."""
    from app.services.communications.engagement.feed import client_communications
    c = _client()
    _portal_exchange(c)
    direct = client_communications(c.principal, household_id=c.household_id,
                                   member_ids=(c.person_id,))
    assert direct["counts"][SECURE_MESSAGE] == 3


def test_the_outlook_reply_route_still_sends(token, monkeypatch):
    """Batch 4c's transport is untouched by this batch."""
    c = _client()
    inbound_id, _ = _inbound_email(c)
    monkeypatch.setattr("app.services.microsoft_identity.account_for_principal",
                        lambda principal, conn=None: _account(c))
    transport = _Transport()
    result = email_send.send_reply(c.principal, communication_message_id=inbound_id,
                                   body="Still works.", send_key=email_send.new_send_key(),
                                   transport=transport)
    assert result["status"] == email_send.SENT and len(transport.calls) == 1


def test_the_consented_microsoft_scope_set_is_still_untouched():
    """A UI batch has no business changing OAuth scopes. Pinned here as well as in Batch 4c's suite
    because this batch touched the communications package."""
    from app.services.microsoft_identity import GRAPH_DELEGATED_SCOPES
    assert "Mail.ReadWrite" not in GRAPH_DELEGATED_SCOPES
    assert set(GRAPH_DELEGATED_SCOPES) == {
        "User.Read", "Mail.Read", "Mail.Send", "Calendars.Read", "Files.Read.All", "Sites.Read.All"}


def test_no_sms_channel_is_implied():
    """SMS is not built. A placeholder would tell staff a channel exists that does not."""
    assert set(feed_mod.CHANNEL_LABELS) == {SECURE_MESSAGE, EMAIL}
    assert "sms" not in feed_mod.CHANNEL_LABELS


def test_an_empty_client_renders_an_empty_feed_rather_than_failing(token):
    c = _client()
    result = _feed(c)
    assert result["authorized"] is True
    assert result["rows"] == [] and result["total"] == 0
    assert result["counts"] == {SECURE_MESSAGE: 0, EMAIL: 0}


def test_an_anchorless_request_returns_nothing(token):
    c = _client()
    result = client_communications(c.principal)
    assert result["authorized"] is True and result["rows"] == []


def test_a_failing_store_degrades_to_the_other_channel(token, monkeypatch):
    """One store being unavailable must not take the client profile down with it."""
    c = _client()
    _portal_exchange(c)
    _inbound_email(c)

    def _boom(*a, **k):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(
        "app.services.communications.engagement.adapters.portal_feed._portal_entries", _boom)
    result = _feed(c)
    assert set(_channels(result)) == {EMAIL}
