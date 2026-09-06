"""Normalize one inbound Outlook email into canonical ``communication_*`` records (Batch 4a).

Mail is already ingested (``app/jobs/microsoft_mail_sync.py``) into ``timeline_events`` and, for an
unrecognised sender, ``microsoft_unmatched_messages``. Neither is a communication record: the email
has no conversation, no message row, no recipients, and the two identifiers that make it addressable
— ``internetMessageId`` and ``conversationId`` — were read by the preview route and discarded by the
sync. This module writes the canonical rows beside what already exists.

THE RULE THAT SHAPES EVERYTHING: **one email, one timeline row.** The D.44 registry classifies a
timeline row by ``(source, event_type)`` and falls back to ``event_type`` alone, so a
``conversation_opened`` event written for an email that already has an ``email_received`` event would
make it appear TWICE in every engagement timeline. This module therefore writes its rows directly
rather than through ``communications.service.create_conversation``, which publishes such an event.
Nothing here touches the timeline; the caller keeps its existing write, unchanged.

IDENTITY. The Graph ``id`` is mailbox-scoped and CHANGES when a message moves between folders, so it
is never the identity — it is kept in source metadata for traceability. Identity is the RFC 5322
``internetMessageId``, stable across folders, mailboxes and tenants, falling back to a composite
``{tenant}:{mailbox}:{graph id}`` for the few items that carry no Message-ID. The UNIQUE
``(source_system, source_external_id)`` on ``communication_message_sources`` (emailnorm01) makes the
ingest idempotent in the database rather than by a racy read-then-write.

THREADING. ``conversationId`` is scoped to a mailbox — the same real thread carries different values
in different mailboxes — so the conversation key is the composite
``(tenant_id, mailbox_user_id, conversation_id)``, matched out of ``conversation_metadata``.

ANCHORING NEVER GUESSES. One matched person anchors the person and their household; several people
in ONE household anchor the household; several across DIFFERENT households is ambiguous and anchors
NOTHING — the message is left to the existing review queue, the same discipline
``communication_hub.suggest_assignment`` applies. An email with no anchor writes no conversation at
all, so normalization can never manufacture an orphan.

WHAT IS DELIBERATELY NOT STORED. No full body — Communications carries a REGULATORY retention class
and full inbound correspondence is a compliance decision, so this stores the same preview the
timeline already stores. No attachments (the sync receives only ``hasAttachments``). No delivery
rows: ``communication_deliveries`` is an outbound lifecycle ledger and inbound mail has no delivery
intent of ours to record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

SOURCE_SYSTEM = "microsoft_graph"
#: Longest preview persisted — the same bound the timeline summary already uses.
PREVIEW_LIMIT = 500

INBOUND, OUTBOUND = "inbound", "outbound"


def _now():
    return datetime.now(UTC)


def normalize_address(value) -> str:
    return (value or "").strip().lower()


def _address(entry) -> tuple[str, str]:
    """``(display name, address)`` from a Graph recipient entry."""
    email = (entry or {}).get("emailAddress") or {}
    address = normalize_address(email.get("address"))
    return (email.get("name") or address), address


@dataclass(frozen=True)
class EmailMatch:
    """How one Graph message resolves against Client360 people."""
    direction: str
    sender_name: str
    sender_address: str
    #: (role, display name, address, person_id or None) for every To/Cc recipient.
    recipients: tuple = ()
    person_id: int | None = None
    household_id: int | None = None
    matched_person_ids: frozenset = field(default_factory=frozenset)
    ambiguous: bool = False

    @property
    def anchored(self) -> bool:
        """Whether there is a client to file this email against. Ambiguity is never an anchor."""
        return not self.ambiguous and (self.person_id is not None or self.household_id is not None)


def resolve_match(message: dict, people_by_email: dict, owner_address: str) -> EmailMatch:
    """Resolve sender + recipients against ``{normalized email: (person_id, household_id)}``.

    ``owner_address`` is the mailbox this message was read from. A message FROM that address is this
    firm's own outbound copy — Graph's ``/me/messages`` is not folder-scoped, so Sent Items arrive
    here too — and is direction ``outbound`` rather than an unrecognised inbound sender.
    """
    sender_name, sender_address = _address(message.get("from") or message.get("sender"))
    direction = OUTBOUND if (sender_address
                             and sender_address == normalize_address(owner_address)) else INBOUND

    recipients = []
    for role, key in (("to", "toRecipients"), ("cc", "ccRecipients")):
        for entry in message.get(key) or []:
            name, address = _address(entry)
            if address:
                match = people_by_email.get(address)
                recipients.append((role, name, address, match[0] if match else None))

    # Candidates: the counterparty side only. The mailbox owner is staff, never the client anchor.
    candidates = []
    if direction == INBOUND and sender_address in people_by_email:
        candidates.append(people_by_email[sender_address])
    for _role, _name, address, person_id in recipients:
        if person_id is not None and address != normalize_address(owner_address):
            candidates.append(people_by_email[address])

    by_person = {person_id: household_id for person_id, household_id in candidates}
    households = {h for h in by_person.values() if h is not None}

    person_id = household_id = None
    ambiguous = False
    if len(by_person) == 1:
        person_id, household_id = next(iter(by_person.items()))
    elif len(by_person) > 1:
        if len(households) == 1 and None not in by_person.values():
            household_id = next(iter(households))            # one family, several members
        else:
            ambiguous = True                                  # different clients — never guess

    return EmailMatch(direction=direction, sender_name=sender_name, sender_address=sender_address,
                      recipients=tuple(recipients), person_id=person_id, household_id=household_id,
                      matched_person_ids=frozenset(by_person), ambiguous=ambiguous)


def source_external_id(message: dict, account: dict) -> str:
    """Stable identity for one email. Never the Graph id, which changes when a message is moved.

    The fallback, for the few items that carry no Message-ID, is the most specific composite the
    caller can supply — empty parts are dropped rather than written as the string ``None``.
    """
    internet_id = (message.get("internetMessageId") or "").strip()
    if internet_id:
        return internet_id
    parts = [account.get("tenant_id"), account.get("user_id"), message.get("id")]
    return ":".join(str(p) for p in parts if p)


def find_by_graph_id(conn, graph_id) -> int | None:
    """The canonical message a Graph id was ingested as, if any.

    The review queue stores only the Graph id — it predates this module and captures no Message-ID —
    so a manually matched email has to be reconciled against what the scheduled sync may already
    have normalized. Without this the two paths would compute different identities for one email and
    create two message rows.
    """
    from sqlalchemy import select

    from app.db import communication_message_sources as sources

    if not graph_id:
        return None
    return conn.execute(select(sources.c.message_id).where(
        sources.c.source_system == SOURCE_SYSTEM,
        sources.c.source_metadata["graph_id"].astext == str(graph_id))).scalar()


def _preview(message: dict) -> str | None:
    text = (message.get("bodyPreview") or "").strip()
    if not text:
        return None
    return text if len(text) <= PREVIEW_LIMIT else text[: PREVIEW_LIMIT - 3] + "..."


def _parse_datetime(value):
    if not value:
        return _now()
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _conversation_key(message: dict, account: dict) -> dict:
    """``conversationId`` is mailbox-scoped, so the thread key is composite."""
    return {"provider": SOURCE_SYSTEM,
            "tenant_id": account.get("tenant_id"),
            "mailbox_user_id": account.get("user_id"),
            "conversation_id": message.get("conversationId")}


def _find_conversation(conn, key: dict):
    from sqlalchemy import select

    from app.db import communication_conversations as conversations
    if not key.get("conversation_id"):
        return None
    rows = conn.execute(select(conversations.c.id, conversations.c.conversation_metadata).where(
        conversations.c.channel == "email")).mappings().all()
    for row in rows:
        meta = row["conversation_metadata"] or {}
        if all(meta.get(k) == v for k, v in key.items()):
            return row["id"]
    return None


def normalize_email(conn, *, account: dict, message: dict, match: EmailMatch) -> int | None:
    """Write the canonical rows for one email on the CALLER'S transaction. Returns the message id.

    Returns ``None`` when the email has no client anchor: an unanchored or ambiguous message belongs
    in the existing review queue, not in a conversation nobody owns. Idempotent — a message already
    recorded returns its existing id and writes nothing.
    """
    from sqlalchemy import select

    from app.db import communication_conversations as conversations
    from app.db import communication_events as events
    from app.db import communication_message_sources as sources
    from app.db import communication_messages as messages
    from app.db import communication_recipients as recipients_table

    if not match.anchored:
        return None

    external_id = source_external_id(message, account)
    existing = conn.execute(select(sources.c.message_id).where(
        sources.c.source_system == SOURCE_SYSTEM,
        sources.c.source_external_id == external_id)).scalar()
    if existing is not None:
        conn.execute(sources.update().where(
            sources.c.source_system == SOURCE_SYSTEM,
            sources.c.source_external_id == external_id).values(last_synced_at=_now()))
        return existing                                        # already ingested — nothing to write

    # A message this firm SENT from 360Plus comes back around in Sent Items. Graph's direct reply
    # returns no identity (Batch 4c / ADR-075), so the outbound record has been waiting for exactly
    # this sighting: attach the real identity to it rather than creating a second message.
    if match.direction == OUTBOUND:
        from app.services.communications import email_send
        pending = email_send.pending_reconciliation(
            conn, provider_conversation_id=message.get("conversationId"),
            mailbox_user_id=account.get("user_id"))
        if pending is not None:
            return email_send.reconcile(conn, message_row=pending, graph_message=message,
                                        account=account)

    now = _now()
    received_at = _parse_datetime(message.get("receivedDateTime"))
    subject = message.get("subject") or "(No subject)"
    key = _conversation_key(message, account)

    conversation_id = _find_conversation(conn, key)
    if conversation_id is None:
        conversation_id = conn.execute(conversations.insert().values(
            subject=subject, category="general", status="open", priority="normal", channel="email",
            person_id=match.person_id, household_id=match.household_id,
            conversation_metadata=key, last_message_at=received_at,
            created_at=now, updated_at=now).returning(conversations.c.id)).scalar_one()
    else:
        # An existing conversation keeps its FIRST anchor. A later message naming a different client
        # is recorded here and left for review; re-anchoring would silently move a thread's owner.
        conn.execute(conversations.update().where(conversations.c.id == conversation_id).values(
            last_message_at=received_at, updated_at=now))

    message_id = conn.execute(messages.insert().values(
        conversation_id=conversation_id, channel="email", direction=match.direction,
        priority="normal", category="general", subject=subject, body=_preview(message),
        sender_type="external" if match.direction == INBOUND else "user",
        sender_ref=match.sender_address or None,
        status="delivered", sent_at=received_at, delivered_at=received_at,
        message_metadata={"web_link": message.get("webLink"),
                          "has_attachments": bool(message.get("hasAttachments")),
                          "is_read": bool(message.get("isRead"))},
        created_at=now, updated_at=now).returning(messages.c.id)).scalar_one()

    for role, name, address, person_id in match.recipients:
        conn.execute(recipients_table.insert().values(
            message_id=message_id,
            recipient_type="person" if person_id is not None else "external",
            recipient_ref=str(person_id) if person_id is not None else address,
            recipient_role=role, display_name=name, delivery_status="delivered",
            delivered_at=received_at, created_at=now))

    conn.execute(sources.insert().values(
        message_id=message_id, source_system=SOURCE_SYSTEM, source_external_id=external_id,
        source_uri=message.get("webLink"),
        source_metadata={"graph_id": message.get("id"),
                         "mailbox_user_id": account.get("user_id"),
                         "tenant_id": account.get("tenant_id"),
                         "conversation_id": message.get("conversationId")},
        first_seen_at=now, last_synced_at=now))

    # The domain's own append-only ledger. NOT a timeline event: the caller already writes the one
    # timeline row this email gets, and a second would double-count it in every engagement view.
    conn.execute(events.insert().values(
        conversation_id=conversation_id, message_id=message_id, event_type="message_ingested",
        payload={"source_system": SOURCE_SYSTEM, "direction": match.direction,
                 "matched_person_ids": sorted(match.matched_person_ids)},
        occurred_at=now))
    return message_id


def record_sighting(conn, *, account: dict, message: dict, message_id: int) -> None:
    """Record that an ALREADY-normalized message was also seen in this mailbox.

    One canonical message, many source references — the ``document_sources`` model. Used when the
    same email arrives through a second connected mailbox; the unique identity constraint means the
    second sighting can never create a second message.
    """
    from sqlalchemy import select

    from app.db import communication_message_sources as sources

    external_id = source_external_id(message, account)
    already = conn.execute(select(sources.c.id).where(
        sources.c.message_id == message_id, sources.c.source_system == SOURCE_SYSTEM,
        sources.c.source_external_id == external_id)).scalar()
    if already is not None:
        return
    conn.execute(sources.insert().values(
        message_id=message_id, source_system=SOURCE_SYSTEM, source_external_id=external_id,
        source_uri=message.get("webLink"),
        source_metadata={"graph_id": message.get("id"),
                         "mailbox_user_id": account.get("user_id"),
                         "tenant_id": account.get("tenant_id")},
        first_seen_at=_now(), last_synced_at=_now()))
