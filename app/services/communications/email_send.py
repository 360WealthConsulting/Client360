"""Send an Outlook reply from 360Plus using the already-consented ``Mail.Send`` (Batch 4c, ADR-075).

    staff -> authorize -> send intent (durable, BEFORE Graph) -> POST /me/messages/{id}/reply
          -> mark sent -> next Sent Items poll attaches the real provider identity

WHY THIS SHAPE. The draft-first flow (``createReply`` then ``send``) would give us a provider message
id BEFORE the send, which is the safer identity story. It needs ``Mail.ReadWrite``, which this tenant
has not consented: ``GRAPH_DELEGATED_SCOPES`` is read-only plus ``Mail.Send``. Adding a scope makes
``acquire_token_silent`` fail for every cached token, so all connected mailboxes would lose INBOUND
sync until each user reconnected. That trade was made deliberately — see ADR-075 — and this module is
what the decision costs: Graph's direct reply returns ``202 Accepted`` with no body, so there is no
provider identity until the sent copy comes back around on a later poll.

WHAT REPLACES PRE-SEND IDENTITY. A durable LOCAL send intent, written and committed before the Graph
call, keyed by a client-supplied UUID and made unique by the database:
``communication_message_sources`` already carries ``UNIQUE (source_system, source_external_id)``, so
the intent key is race-safe without a migration. A repeated POST finds the intent and never reaches
Graph again.

THE UNCERTAIN WINDOW IS EXPLICIT. Between "Graph accepted" and "we recorded that", a crash leaves the
message in ``sending``. That state is UNCERTAIN, not retryable: only ``failed`` — which is only ever
written when Graph told us it failed — permits another send. An uncertain message is resolved by the
Sent Items poll, not by guessing. Being unable to resend is the correct failure: a duplicate email to
a client cannot be withdrawn, and a missing one is visible and re-sendable by hand.

RECONCILIATION IS DETERMINISTIC, NOT HEURISTIC. Microsoft does not clearly document custom
``internetMessageHeaders`` on the direct reply endpoint, so none is used. The sent copy is matched on
evidence we already hold: the same provider conversation, the same mailbox, an outbound message still
awaiting identity, and the recipient — never on subject alone.

RETENTION. The full outbound body is persisted. ``communication_messages.body`` is unbounded ``TEXT``,
so this needs no schema change and no new retention subsystem; the firm's own outbound correspondence
is the authoritative record of what staff told a client, and truncating it would make the audit trail
weaker than the act it records. Inbound stays preview-only — that asymmetry is deliberate and
recorded in ADR-075, because inbound is third-party content and outbound is the firm's own words.

NOT IN SCOPE: Reply All, arbitrary recipients, a new-message composer, attachments, bulk or marketing
mail, shared mailboxes, any scope or consent change.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
REQUIRED_CAPABILITY = "communications.send"

#: Provenance namespace for the LOCAL pre-send intent. Distinct from ``microsoft_graph`` (which only
#: ever holds a real provider identity), so "has this been reconciled?" is a row's existence.
INTENT_SOURCE_SYSTEM = "client360_send_intent"
PROVIDER_SOURCE_SYSTEM = "microsoft_graph"

#: Repository delivery vocabulary (communication_tables.DELIVERY_STATUSES). ``sent`` means Graph
#: ACCEPTED the message for transport — never that a recipient received or read it.
QUEUED, SENDING, SENT, FAILED = "queued", "sending", "sent", "failed"
#: Only a state Graph explicitly refused may be sent again. ``sending`` is uncertain, not failed.
RESENDABLE = frozenset({FAILED})

MAX_BODY_CHARS = 100_000
PREVIEW_CHARS = 500


class SendError(RuntimeError):
    """A refusal safe to show a staff user verbatim."""


class NotAuthorized(SendError):
    """Capability, record scope, or mailbox identity failed. Never says which."""


def _now():
    return datetime.now(UTC)


def new_send_key() -> str:
    """The per-compose idempotency key. Minted when the form is rendered, returned with the POST."""
    return str(uuid.uuid4())


def body_preview(body: str) -> str:
    text = (body or "").strip()
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 3] + "..."


def _fingerprint(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


# --- authorization ---------------------------------------------------------------------------

def authorize(principal, conversation) -> dict:
    """Capability + record scope + mailbox identity. Returns the staff member's OWN Microsoft account.

    Every check runs again on POST; none is inherited from having rendered the form. Mailbox identity
    fails CLOSED — ``account_for_principal`` matches the signed-in user's own address exactly and
    never falls back to another account, so a reply can only ever leave the mailbox of the person
    sending it.
    """
    from app.security.authorization import organization_in_scope, record_in_scope
    from app.services.microsoft_identity import account_for_principal

    if not principal.can(REQUIRED_CAPABILITY):
        raise NotAuthorized(f"Missing capability: {REQUIRED_CAPABILITY}")

    person_id = conversation.get("person_id")
    household_id = conversation.get("household_id")
    organization_id = conversation.get("organization_id")
    in_scope = (
        (person_id is not None and record_in_scope(principal, "person", person_id, write=True))
        or (household_id is not None
            and record_in_scope(principal, "household", household_id, write=True))
        or (organization_id is not None
            and organization_in_scope(principal, organization_id, write=True)))
    if not in_scope:
        raise NotAuthorized("That conversation is not available to you.")

    account = account_for_principal(principal)
    if account is None:
        raise NotAuthorized(
            "Microsoft 365 is not connected for your account. Connect it before replying.")
    return account


# --- reply target ----------------------------------------------------------------------------

def reply_target(conn, communication_message_id: int) -> dict:
    """The source message to reply to: its conversation, provider ids, and the recipient.

    The recipient is taken ENTIRELY from the stored conversation — Graph's reply semantics address
    the original sender — so a POST body can never introduce one.
    """
    from sqlalchemy import select

    from app.db import communication_conversations as conversations
    from app.db import communication_message_sources as sources
    from app.db import communication_messages as messages

    row = conn.execute(select(messages).where(
        messages.c.id == communication_message_id)).mappings().first()
    if row is None or row["channel"] != "email":
        raise SendError("That message cannot be replied to.")
    conversation = conn.execute(select(conversations).where(
        conversations.c.id == row["conversation_id"])).mappings().first()
    if conversation is None:
        raise SendError("That conversation no longer exists.")
    source = conn.execute(select(sources).where(
        sources.c.message_id == communication_message_id,
        sources.c.source_system == PROVIDER_SOURCE_SYSTEM).order_by(
        sources.c.id.desc())).mappings().first()
    graph_id = (source or {}).get("source_metadata", {}).get("graph_id") if source else None
    if not graph_id:
        raise SendError(
            "This email has no Microsoft message reference, so it cannot be replied to from here. "
            "Reply from Outlook instead.")
    return {"message": dict(row), "conversation": dict(conversation), "graph_id": graph_id,
            "recipient": row["sender_ref"]}


# --- the send ---------------------------------------------------------------------------------

def _find_intent(conn, send_key: str):
    from sqlalchemy import select

    from app.db import communication_message_sources as sources
    from app.db import communication_messages as messages

    message_id = conn.execute(select(sources.c.message_id).where(
        sources.c.source_system == INTENT_SOURCE_SYSTEM,
        sources.c.source_external_id == send_key)).scalar()
    if message_id is None:
        return None
    return conn.execute(select(messages).where(messages.c.id == message_id)).mappings().first()


def _record_delivery(conn, *, message_id, status, provider_ref=None, detail=None, metadata=None):
    from app.db import communication_deliveries as deliveries

    conn.execute(deliveries.insert().values(
        message_id=message_id, channel="email", provider=PROVIDER_SOURCE_SYSTEM,
        provider_ref=provider_ref, status=status, detail=detail,
        delivery_metadata=metadata or {}, occurred_at=_now(), created_at=_now()))


def prepare_send(conn, *, target: dict, account: dict, principal, body: str, send_key: str) -> dict:
    """Create the durable send intent BEFORE any Graph call. Idempotent on ``send_key``.

    Returns ``{"message_id", "existing"}``. ``existing`` is True when this key has been seen — the
    caller must NOT contact Graph again for it.
    """
    from app.db import communication_message_sources as sources
    from app.db import communication_messages as messages
    from app.db import communication_recipients as recipients_table

    existing = _find_intent(conn, send_key)
    if existing is not None:
        return {"message_id": existing["id"], "existing": True, "status": existing["status"]}

    conversation = target["conversation"]
    recipient = target["recipient"]
    now = _now()
    message_id = conn.execute(messages.insert().values(
        conversation_id=conversation["id"], channel="email", direction="outbound",
        priority="normal", category="general",
        subject=f"RE: {target['message']['subject']}" if target["message"]["subject"] else "RE:",
        # FULL body: the firm's own words are the authoritative record of what it told a client.
        body=body,
        sender_type="user", sender_ref=account.get("email"),
        sender_user_id=getattr(principal, "user_id", None),
        created_by_user_id=getattr(principal, "user_id", None),
        status=QUEUED, created_at=now, updated_at=now,
        message_metadata={"send_key": send_key,
                          "mailbox_user_id": account.get("user_id"),
                          "tenant_id": account.get("tenant_id"),
                          "in_reply_to_message_id": target["message"]["id"],
                          "in_reply_to_graph_id": target["graph_id"],
                          "body_preview": body_preview(body),
                          "body_fingerprint": _fingerprint(body),
                          "provider_identity": "pending_reconciliation"},
    ).returning(messages.c.id)).scalar_one()

    if recipient:
        conn.execute(recipients_table.insert().values(
            message_id=message_id, recipient_type="external", recipient_ref=recipient,
            recipient_role="to", display_name=recipient, delivery_status=QUEUED, created_at=now))

    # The idempotency key, made durable and race-safe by UNIQUE (source_system, source_external_id).
    conn.execute(sources.insert().values(
        message_id=message_id, source_system=INTENT_SOURCE_SYSTEM, source_external_id=send_key,
        source_metadata={"mailbox_user_id": account.get("user_id"),
                         "tenant_id": account.get("tenant_id"),
                         "provider_conversation_id":
                             (conversation.get("conversation_metadata") or {}).get("conversation_id"),
                         "recipient": recipient},
        first_seen_at=now))
    _record_delivery(conn, message_id=message_id, status=QUEUED, provider_ref=send_key)
    return {"message_id": message_id, "existing": False, "status": QUEUED}


def _set_status(message_id, status, *, metadata_update=None):
    from sqlalchemy import select

    from app.db import communication_messages as messages
    from app.db import engine

    with engine.begin() as conn:
        current = conn.execute(select(messages.c.message_metadata).where(
            messages.c.id == message_id)).scalar() or {}
        meta = dict(current)
        meta.update(metadata_update or {})
        values = {"status": status, "updated_at": _now(), "message_metadata": meta}
        if status == SENT:
            values["sent_at"] = _now()
        conn.execute(messages.update().where(messages.c.id == message_id).values(**values))


def send_reply(principal, *, communication_message_id: int, body: str, send_key: str,
               transport=None) -> dict:
    """Reply to a normalized inbound email from the staff member's OWN mailbox.

    ``transport(access_token, graph_id, body)`` performs the Graph call; injected so tests never
    reach the network. Returns ``{"message_id", "status", "resent": bool}``.
    """
    from app.db import engine
    from app.security.audit import write_audit_event
    from app.services.microsoft_identity import get_microsoft_access_token

    text = (body or "").strip()
    if not text:
        raise SendError("A reply cannot be empty.")
    if len(text) > MAX_BODY_CHARS:
        raise SendError(f"That reply is too long (limit {MAX_BODY_CHARS:,} characters).")
    if not (send_key or "").strip():
        raise SendError("A send key is required.")

    with engine.connect() as conn:
        target = reply_target(conn, communication_message_id)
    account = authorize(principal, target["conversation"])          # raises before any Graph call

    with engine.begin() as conn:
        intent = prepare_send(conn, target=target, account=account, principal=principal,
                              body=text, send_key=send_key)

    if intent["existing"]:
        # A repeat of a send we have already attempted. Only a Graph-refused attempt may go again;
        # `sending` is UNCERTAIN and is resolved by reconciliation, never by sending a second email.
        if intent["status"] not in RESENDABLE:
            return {"message_id": intent["message_id"], "status": intent["status"],
                    "conversation_id": target["conversation"]["id"], "resent": False}

    _set_status(intent["message_id"], SENDING)
    with engine.begin() as conn:
        _record_delivery(conn, message_id=intent["message_id"], status=SENDING,
                         provider_ref=send_key)

    write_audit_event(
        action="communication.email.reply.attempted", entity_type="communication_message",
        entity_id=intent["message_id"], actor_user_id=getattr(principal, "user_id", None),
        request_id=f"email-reply-{send_key}",
        metadata={"conversation_id": target["conversation"]["id"],
                  "person_id": target["conversation"].get("person_id"),
                  "household_id": target["conversation"].get("household_id"),
                  "mailbox": account.get("email"), "send_key": send_key,
                  "in_reply_to_graph_id": target["graph_id"]})

    try:
        access_token = get_microsoft_access_token(account)
        (transport or graph_reply)(access_token, target["graph_id"], text)
    except Exception as exc:                       # noqa: BLE001 — recorded, never swallowed silently
        _set_status(intent["message_id"], FAILED,
                    metadata_update={"provider_identity": "not_sent"})
        with engine.begin() as conn:
            _record_delivery(conn, message_id=intent["message_id"], status=FAILED,
                             provider_ref=send_key, detail=type(exc).__name__)
        write_audit_event(
            action="communication.email.reply.failed", entity_type="communication_message",
            entity_id=intent["message_id"], actor_user_id=getattr(principal, "user_id", None),
            request_id=f"email-reply-{send_key}",
            metadata={"send_key": send_key, "error": type(exc).__name__})
        raise SendError("That reply could not be sent. It has not gone to the client.") from exc

    # Graph ACCEPTED it. From here the email exists in the world whatever happens locally, which is
    # why `sending` is never resendable: if this update is lost, reconciliation resolves it.
    _set_status(intent["message_id"], SENT,
                metadata_update={"provider_identity": "pending_reconciliation"})
    with engine.begin() as conn:
        _record_delivery(conn, message_id=intent["message_id"], status=SENT, provider_ref=send_key,
                         detail="accepted by Microsoft Graph for transport",
                         metadata={"provider_identity": "pending_reconciliation"})
    write_audit_event(
        action="communication.email.reply.sent", entity_type="communication_message",
        entity_id=intent["message_id"], actor_user_id=getattr(principal, "user_id", None),
        request_id=f"email-reply-{send_key}",
        metadata={"send_key": send_key, "mailbox": account.get("email"),
                  "conversation_id": target["conversation"]["id"]})
    return {"message_id": intent["message_id"], "status": SENT,
            "conversation_id": target["conversation"]["id"], "resent": False}


def graph_reply(access_token: str, graph_message_id: str, body: str) -> None:
    """``POST /me/messages/{id}/reply`` — provider-native threading under the consented Mail.Send.

    No ``createReply``, no draft, no ``Mail.ReadWrite``. Graph replies 202 with no body, so there is
    nothing to capture here; identity arrives with the sent copy on a later poll. No custom internet
    header is set: Microsoft does not clearly document them on this endpoint, and reconciliation is
    deterministic without one.
    """
    import requests

    response = requests.post(
        f"{GRAPH_MESSAGES_URL}/{graph_message_id}/reply",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"comment": body}, timeout=30)
    if response.status_code not in (200, 202):
        raise RuntimeError(f"Graph reply failed with HTTP {response.status_code}")


# --- reconciliation --------------------------------------------------------------------------

def pending_reconciliation(conn, *, provider_conversation_id, mailbox_user_id):
    """An outbound message this firm sent that is still awaiting its provider identity.

    Deterministic evidence only — the provider conversation, the mailbox that sent it, an outbound
    message in a post-send state, and no provider identity yet. Never subject matching.
    """
    from sqlalchemy import select

    from app.db import communication_message_sources as sources
    from app.db import communication_messages as messages

    if not provider_conversation_id:
        return None
    rows = conn.execute(select(messages).where(
        messages.c.direction == "outbound", messages.c.channel == "email",
        messages.c.status.in_((SENDING, SENT))).order_by(messages.c.id.desc()).limit(50)
    ).mappings().all()
    for row in rows:
        meta = row["message_metadata"] or {}
        if meta.get("provider_identity") != "pending_reconciliation":
            continue
        if meta.get("mailbox_user_id") != mailbox_user_id:
            continue
        intent = conn.execute(select(sources).where(
            sources.c.message_id == row["id"],
            sources.c.source_system == INTENT_SOURCE_SYSTEM)).mappings().first()
        if intent is None:
            continue
        if (intent["source_metadata"] or {}).get("provider_conversation_id") != provider_conversation_id:
            continue
        return dict(row)
    return None


def reconcile(conn, *, message_row: dict, graph_message: dict, account: dict) -> int:
    """Attach the real provider identity to an already-sent outbound message.

    Creates the ``microsoft_graph`` source row — after which the ordinary
    ``UNIQUE (source_system, source_external_id)`` path deduplicates every later poll — and closes
    the delivery record. Creates NO second message and NO timeline event.
    """
    from app.db import communication_messages as messages
    from app.services.communications.email_ingest import source_external_id

    external_id = source_external_id(graph_message, account)
    from app.db import communication_message_sources as sources

    conn.execute(sources.insert().values(
        message_id=message_row["id"], source_system=PROVIDER_SOURCE_SYSTEM,
        source_external_id=external_id, source_uri=graph_message.get("webLink"),
        source_metadata={"graph_id": graph_message.get("id"),
                         "mailbox_user_id": account.get("user_id"),
                         "tenant_id": account.get("tenant_id"),
                         "conversation_id": graph_message.get("conversationId")},
        first_seen_at=_now(), last_synced_at=_now()))
    meta = dict(message_row["message_metadata"] or {})
    meta["provider_identity"] = "reconciled"
    conn.execute(messages.update().where(messages.c.id == message_row["id"]).values(
        status=SENT, message_metadata=meta, updated_at=_now()))
    _record_delivery(conn, message_id=message_row["id"], status=SENT, provider_ref=external_id,
                     detail="provider identity reconciled from the sent copy",
                     metadata={"provider_identity": "reconciled"})
    return message_row["id"]
