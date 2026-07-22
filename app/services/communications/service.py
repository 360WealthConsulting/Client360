"""Communications service (Phase D.18) — authoritative for communication metadata.

Owns conversations, threads, messages, recipients, attachment references, and the append-only
audit ledger. It **references** business entities (people/households/organizations, documents,
workflow) and never becomes their source of truth. Record scope is always enforced: a conversation
is visible via its person/household anchor (or ``record.read_all``), its organization anchor
(``organization_in_scope``), or — for firm-wide conversations with no anchor — to
``communications.view`` holders. Delivery reuses the notification ledger (metadata only) and
approved lifecycle events flow to the shared Activity Timeline via
``app/services/communications/delivery.py``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select

from app.database.communication_tables import (
    COMMUNICATION_CATEGORIES,
    COMMUNICATION_CHANNELS,
    COMMUNICATION_DIRECTIONS,
    COMMUNICATION_PRIORITIES,
    RECIPIENT_ROLES,
    RECIPIENT_TYPES,
)
from app.db import (
    communication_attachments as attachments,
)
from app.db import (
    communication_conversations as conversations,
)
from app.db import (
    communication_events as events,
)
from app.db import (
    communication_messages as messages,
)
from app.db import (
    communication_recipients as recipients,
)
from app.db import (
    communication_threads as threads,
)
from app.db import engine, people, record_assignments
from app.security.authorization import (
    accessible_person_ids,
    organization_in_scope,
    record_in_scope,
    team_ids,
)
from app.services.communications import delivery, templates

_CONV_STATUSES = ("open", "closed", "archived")


class CommunicationError(Exception):
    """Validation or lifecycle error."""


class CommunicationNotFound(Exception):
    """Conversation/message not found or out of scope."""


def _now():
    return datetime.now(UTC)


def _json(payload):
    # communication_events.payload is JSON; keep it plain-serializable.
    return json.loads(json.dumps(payload or {}))


# --- scope -------------------------------------------------------------------

def _accessible_org_ids(c, principal) -> set[int]:
    tids = team_ids(c, principal)
    conds = [record_assignments.c.user_id == principal.user_id]
    if tids:
        conds.append(record_assignments.c.team_id.in_(tuple(tids)))
    rows = c.scalars(select(record_assignments.c.entity_id).where(
        record_assignments.c.entity_type == "organization", or_(*conds)))
    return {r for r in rows if r is not None}


def _scope_clause(principal, c):
    if principal.can("record.read_all"):
        return None
    conds = [and_(conversations.c.person_id.is_(None),
                  conversations.c.household_id.is_(None),
                  conversations.c.organization_id.is_(None))]        # firm-wide conversations
    ids = accessible_person_ids(c, principal)
    if ids:
        conds.append(conversations.c.person_id.in_(tuple(ids)))
        hh = set(c.scalars(select(people.c.household_id).where(
            people.c.id.in_(tuple(ids)), people.c.household_id.is_not(None))))
        if hh:
            conds.append(conversations.c.household_id.in_(tuple(hh)))
    orgs = _accessible_org_ids(c, principal)
    if orgs:
        conds.append(conversations.c.organization_id.in_(tuple(orgs)))
    return or_(*conds)


def _visible(principal, conv: dict, c) -> bool:
    if principal.can("record.read_all"):
        return True
    if conv.get("person_id") and record_in_scope(principal, "person", conv["person_id"], connection=c):
        return True
    if conv.get("household_id") and record_in_scope(principal, "household", conv["household_id"],
                                                    connection=c):
        return True
    if conv.get("organization_id") and organization_in_scope(principal, conv["organization_id"],
                                                             connection=c):
        return True
    return not (conv.get("person_id") or conv.get("household_id") or conv.get("organization_id"))


def _can_write(principal, conv: dict, c) -> bool:
    if principal.can("record.write_all") or principal.can("record.read_all"):
        return True
    if conv.get("person_id") and record_in_scope(principal, "person", conv["person_id"],
                                                 write=True, connection=c):
        return True
    if conv.get("household_id") and record_in_scope(principal, "household", conv["household_id"],
                                                    write=True, connection=c):
        return True
    if conv.get("organization_id") and organization_in_scope(principal, conv["organization_id"],
                                                             write=True, connection=c):
        return True
    return not (conv.get("person_id") or conv.get("household_id") or conv.get("organization_id"))


def _load_scoped(c, principal, conversation_id: int, *, write=False) -> dict:
    conv = c.execute(select(conversations).where(
        conversations.c.id == conversation_id)).mappings().first()
    if conv is None or not _visible(principal, dict(conv), c):
        raise CommunicationNotFound(str(conversation_id))
    conv = dict(conv)
    if write and not _can_write(principal, conv, c):
        raise CommunicationError("write not permitted in record scope")
    return conv


def _load_message(c, principal, message_id: int, *, write=False) -> tuple[dict, dict]:
    msg = c.execute(select(messages).where(messages.c.id == message_id)).mappings().first()
    if msg is None:
        raise CommunicationNotFound(f"message {message_id}")
    conv = _load_scoped(c, principal, msg["conversation_id"], write=write)
    return dict(msg), conv


# --- reads -------------------------------------------------------------------

def list_conversations(principal, *, status=None, category=None, channel=None, search=None,
                       page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        conds = []
        if scope is not None:
            conds.append(scope)
        if status:
            conds.append(conversations.c.status == status)
        if category:
            conds.append(conversations.c.category == category)
        if channel:
            conds.append(conversations.c.channel == channel)
        if search:
            conds.append(conversations.c.subject.ilike(f"%{search.strip()}%"))
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(conversations)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(conversations)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(func.coalesce(conversations.c.last_message_at,
                                        conversations.c.created_at).desc(),
                          conversations.c.id.desc())
            .limit(page_size).offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def get_conversation(principal, conversation_id: int) -> dict | None:
    with engine.connect() as c:
        try:
            conv = _load_scoped(c, principal, conversation_id)
        except (CommunicationNotFound, CommunicationError):
            return None
        thread_rows = [dict(t) for t in c.execute(
            select(threads).where(threads.c.conversation_id == conversation_id)
            .order_by(threads.c.id)).mappings()]
        msg_rows = [dict(m) for m in c.execute(
            select(messages).where(messages.c.conversation_id == conversation_id)
            .order_by(messages.c.id)).mappings()]
        msg_ids = tuple(m["id"] for m in msg_rows) or (0,)
        recips = [dict(r) for r in c.execute(
            select(recipients).where(recipients.c.message_id.in_(msg_ids))
            .order_by(recipients.c.id)).mappings()]
        atts = [dict(a) for a in c.execute(
            select(attachments).where(attachments.c.message_id.in_(msg_ids))
            .order_by(attachments.c.id)).mappings()]
    by_msg_recips: dict = {}
    for r in recips:
        by_msg_recips.setdefault(r["message_id"], []).append(r)
    by_msg_atts: dict = {}
    for a in atts:
        by_msg_atts.setdefault(a["message_id"], []).append(a)
    for m in msg_rows:
        m["recipients"] = by_msg_recips.get(m["id"], [])
        m["attachments"] = by_msg_atts.get(m["id"], [])
    return {"conversation": conv, "threads": thread_rows, "messages": msg_rows}


# --- conversations / threads -------------------------------------------------

def create_conversation(principal, *, subject, category="general", priority="normal",
                        channel="email", person_id=None, household_id=None, organization_id=None,
                        tags=None, actor_user_id=None) -> dict:
    subject = (subject or "").strip()
    if not subject:
        raise CommunicationError("subject is required")
    if category not in COMMUNICATION_CATEGORIES:
        raise CommunicationError(f"invalid category {category!r}")
    if priority not in COMMUNICATION_PRIORITIES:
        raise CommunicationError(f"invalid priority {priority!r}")
    if channel not in COMMUNICATION_CHANNELS:
        raise CommunicationError(f"invalid channel {channel!r}")
    # A caller may only anchor to a record they can write.
    if person_id is not None and not record_in_scope(principal, "person", person_id, write=True):
        raise CommunicationError("person not in write scope")
    if household_id is not None and not record_in_scope(principal, "household", household_id, write=True):
        raise CommunicationError("household not in write scope")
    if organization_id is not None and not organization_in_scope(principal, organization_id, write=True):
        raise CommunicationError("organization not in write scope")
    now = _now()
    with engine.begin() as c:
        conv = c.execute(conversations.insert().values(
            subject=subject, category=category, priority=priority, channel=channel,
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            tags=tags, created_by_user_id=actor_user_id, created_at=now,
            updated_at=now).returning(*conversations.c)).mappings().one()
        conv = dict(conv)
        thread = c.execute(threads.insert().values(
            conversation_id=conv["id"], subject=subject).returning(*threads.c)).mappings().one()
        c.execute(events.insert().values(
            conversation_id=conv["id"], event_type="conversation_opened", actor_user_id=actor_user_id,
            payload=_json({"channel": channel, "category": category}), occurred_at=now))
    # Approved lifecycle event: conversation opened (client-anchored only).
    if person_id or household_id:
        try:
            from app.services.timeline import add_timeline_event
            add_timeline_event(source="communication", event_type="conversation_opened",
                               title=subject, summary=category, person_id=person_id,
                               household_id=household_id,
                               external_id=f"communication-conversation-{conv['id']}",
                               event_metadata={"conversation_id": conv["id"]})
        except Exception:
            pass
    conv["default_thread_id"] = dict(thread)["id"]
    return conv


def create_thread(principal, conversation_id: int, *, subject=None, actor_user_id=None) -> dict:
    with engine.begin() as c:
        _load_scoped(c, principal, conversation_id, write=True)
        thread = c.execute(threads.insert().values(
            conversation_id=conversation_id, subject=subject).returning(*threads.c)).mappings().one()
        c.execute(events.insert().values(
            conversation_id=conversation_id, event_type="thread_created", actor_user_id=actor_user_id,
            payload=_json({"thread_id": dict(thread)["id"]}), occurred_at=_now()))
        return dict(thread)


def set_status(principal, conversation_id: int, status: str, *, actor_user_id=None) -> dict:
    if status not in _CONV_STATUSES:
        raise CommunicationError(f"invalid status {status!r}")
    with engine.begin() as c:
        _load_scoped(c, principal, conversation_id, write=True)
        now = _now()
        conv = c.execute(conversations.update().where(conversations.c.id == conversation_id)
                         .values(status=status, updated_at=now).returning(*conversations.c)).mappings().one()
        c.execute(events.insert().values(
            conversation_id=conversation_id, event_type=f"conversation_{status}",
            actor_user_id=actor_user_id, payload=_json({"status": status}), occurred_at=now))
        return dict(conv)


# --- messages ----------------------------------------------------------------

def send_message(principal, conversation_id: int, *, body=None, subject=None, channel=None,
                 direction="outbound", priority=None, category=None, thread_id=None,
                 template_code=None, template_context=None, sender_type="user", sender_ref=None,
                 recipients_in=None, attachment_document_ids=None, mark_sent=True,
                 actor_user_id=None) -> dict:
    """Compose a message in a conversation. Optionally renders a template (deterministic),
    attaches recipients + document references, and — when ``mark_sent`` — records a ``sent``
    delivery transition (metadata only) that logs intent to the notification ledger and emits an
    approved timeline event."""
    if direction not in COMMUNICATION_DIRECTIONS:
        raise CommunicationError(f"invalid direction {direction!r}")
    with engine.begin() as c:
        conv = _load_scoped(c, principal, conversation_id, write=True)
        eff_channel = channel or conv["channel"]
        if eff_channel not in COMMUNICATION_CHANNELS:
            raise CommunicationError(f"invalid channel {eff_channel!r}")
        eff_priority = priority or conv["priority"]
        eff_category = category or conv["category"]
        eff_subject, eff_body = subject, body
        template_id = None
        if template_code:
            tpl = templates.get_template(code=template_code)
            if tpl is None or not tpl.get("active"):
                raise CommunicationError(f"unknown or inactive template {template_code!r}")
            rendered = templates.render(tpl, template_context or {})
            template_id = tpl["id"]
            eff_subject = eff_subject or rendered["subject"]
            eff_body = eff_body or rendered["body"]
            eff_channel = channel or tpl["channel"] or eff_channel
        if not (eff_body or "").strip():
            raise CommunicationError("message body (or a template) is required")
        if thread_id is not None and c.scalar(
                select(threads.c.id).where(threads.c.id == thread_id,
                                           threads.c.conversation_id == conversation_id)) is None:
            raise CommunicationError("thread does not belong to this conversation")
        now = _now()
        msg = c.execute(messages.insert().values(
            conversation_id=conversation_id, thread_id=thread_id, template_id=template_id,
            channel=eff_channel, direction=direction, priority=eff_priority, category=eff_category,
            subject=eff_subject, body=eff_body, sender_type=sender_type,
            sender_user_id=(actor_user_id if sender_type == "user" else None), sender_ref=sender_ref,
            status="queued", created_by_user_id=actor_user_id, created_at=now,
            updated_at=now).returning(*messages.c)).mappings().one()
        msg = dict(msg)

        for r in (recipients_in or []):
            rtype = r.get("recipient_type", "person")
            role = r.get("recipient_role", "to")
            ref = str(r.get("recipient_ref") or "").strip()
            if not ref:
                continue
            if rtype not in RECIPIENT_TYPES:
                raise CommunicationError(f"invalid recipient_type {rtype!r}")
            if role not in RECIPIENT_ROLES:
                raise CommunicationError(f"invalid recipient_role {role!r}")
            c.execute(recipients.insert().values(
                message_id=msg["id"], recipient_type=rtype, recipient_ref=ref, recipient_role=role,
                display_name=r.get("display_name"), delivery_status="queued"))

        for doc_id in (attachment_document_ids or []):
            c.execute(attachments.insert().values(message_id=msg["id"], document_id=int(doc_id)))

        c.execute(conversations.update().where(conversations.c.id == conversation_id)
                  .values(last_message_at=now, updated_at=now))
        c.execute(events.insert().values(
            conversation_id=conversation_id, message_id=msg["id"], event_type="message_created",
            actor_user_id=actor_user_id, payload=_json({"channel": eff_channel, "direction": direction}),
            occurred_at=now))

        if mark_sent and direction != "inbound":
            msg = delivery.record_delivery(c, msg["id"], "sent", conv=conv, channel=eff_channel,
                                           actor_user_id=actor_user_id)
    return msg


def transition_delivery(principal, message_id: int, status: str, *, recipient_id=None,
                        provider=None, provider_ref=None, detail=None, actor_user_id=None) -> dict:
    with engine.begin() as c:
        msg, conv = _load_message(c, principal, message_id, write=True)
        return delivery.record_delivery(c, message_id, status, conv=conv, channel=msg["channel"],
                                        recipient_id=recipient_id, provider=provider,
                                        provider_ref=provider_ref, detail=detail,
                                        actor_user_id=actor_user_id)


def mark_read(principal, message_id: int, *, recipient_id=None, actor_user_id=None) -> dict:
    return transition_delivery(principal, message_id, "read", recipient_id=recipient_id,
                               actor_user_id=actor_user_id)


def cancel_message(principal, message_id: int, *, actor_user_id=None) -> dict:
    return transition_delivery(principal, message_id, "cancelled", actor_user_id=actor_user_id)


def add_attachment(principal, message_id: int, document_id: int, *, description=None,
                   actor_user_id=None) -> dict:
    with engine.begin() as c:
        msg, conv = _load_message(c, principal, message_id, write=True)
        row = c.execute(attachments.insert().values(
            message_id=message_id, document_id=document_id,
            description=description).returning(*attachments.c)).mappings().one()
        c.execute(events.insert().values(
            conversation_id=conv["id"], message_id=message_id, event_type="attachment_added",
            actor_user_id=actor_user_id, payload=_json({"document_id": document_id}),
            occurred_at=_now()))
        return dict(row)


# --- audit + metrics ---------------------------------------------------------

def audit_history(principal, conversation_id: int) -> list[dict]:
    with engine.connect() as c:
        _load_scoped(c, principal, conversation_id)
        return [dict(e) for e in c.execute(
            select(events).where(events.c.conversation_id == conversation_id)
            .order_by(events.c.occurred_at, events.c.id)).mappings()]


def message_delivery_history(principal, message_id: int) -> list[dict]:
    with engine.connect() as c:
        _load_message(c, principal, message_id)   # scope check
    return delivery.delivery_history(message_id)


def metrics(principal) -> dict:
    """Scope-aware summary counts for the overview (never firm-wide unless read_all)."""
    with engine.connect() as c:
        scope = _scope_clause(principal, c)
        def _count(*extra):
            stmt = select(func.count()).select_from(conversations)
            conds = [] if scope is None else [scope]
            conds.extend(extra)
            return c.scalar(stmt.where(and_(*conds)) if conds else stmt) or 0
        open_conversations = _count(conversations.c.status == "open")
        total_conversations = _count()
        # message + delivery counts within scoped conversations
        scoped_ids = select(conversations.c.id)
        if scope is not None:
            scoped_ids = scoped_ids.where(scope)
        msg_total = c.scalar(select(func.count()).select_from(messages)
                             .where(messages.c.conversation_id.in_(scoped_ids))) or 0
        sent_total = c.scalar(select(func.count()).select_from(messages).where(
            messages.c.conversation_id.in_(scoped_ids),
            messages.c.status.in_(("sent", "delivered", "read")))) or 0
    return {"open_conversations": open_conversations, "total_conversations": total_conversations,
            "messages": msg_total, "sent": sent_total}
