"""Canonical email → unified feed entries (Batch 4d).

READ-ONLY over the ``communication_*`` store that ADR-074 (inbound) and ADR-075 (outbound) made the
canonical record of client email. No writes, no second store, no Graph call — the provider is not
contacted to render a page; everything shown here was normalized at ingestion time.

WHY NOT THE ACTIVITY TIMELINE. See ``feed.py``: the timeline holds one row per sender-matched inbound
email and nothing for outbound, so it can neither show a full exchange nor tell inbound from outbound.
Reading the canonical store is what makes an outbound reply visible at all.

FIXED QUERY COUNT. Five statements regardless of how many messages come back — conversations,
messages, attachments, attachment documents, provider sources. Client profiles with long histories
are exactly where a per-row lookup would hurt, and every section builder runs on every profile load.
"""
from __future__ import annotations

from sqlalchemy import or_, select

from .. import stats
from ..feed import EMAIL, EMAIL_REPLY_CAPABILITY, INBOUND, OUTBOUND, WINDOW, FeedEntry, preview_of

#: The provenance namespace that carries a real Microsoft identity. A message with one of these has a
#: Graph message id, which is what the ADR-075 reply endpoint needs.
PROVIDER_SOURCE_SYSTEM = "microsoft_graph"


def _conversations_in_scope(c, principal, *, person_id, household_id):
    """Email conversations anchored to this client, filtered by the AUTHORITATIVE scope predicate.

    Reuses ``communications.service._visible`` rather than re-deriving record scope, so this surface
    can never be more permissive than the communications service itself.
    """
    from app.db import communication_conversations as conversations
    from app.services.communications.service import _visible

    anchors = []
    if person_id is not None:
        anchors.append(conversations.c.person_id == person_id)
    if household_id is not None:
        # A household-anchored conversation belongs to every member's profile — the same rule the
        # portal thread reader applies. Visibility is NOT widened here to make the feed feel fuller.
        anchors.append(conversations.c.household_id == household_id)
    if not anchors:
        return {}
    rows = c.execute(select(conversations).where(
        conversations.c.channel == "email", or_(*anchors)).order_by(
        conversations.c.last_message_at.desc().nullslast()).limit(WINDOW)).mappings().all()
    return {r["id"]: dict(r) for r in rows if _visible(principal, dict(r), c)}


def email_entries(principal, *, person_id=None, household_id=None) -> list[FeedEntry]:
    """Canonical email for one client as unified feed entries. Fail-closed: never raises upward."""
    try:
        return _email_entries(principal, person_id=person_id, household_id=household_id)
    except Exception:                       # noqa: BLE001 — a store outage degrades, never 500s
        stats.note("adapter_failures", source="communication_messages")
        return []


def _email_entries(principal, *, person_id, household_id) -> list[FeedEntry]:
    from app.db import communication_attachments as attachments
    from app.db import communication_message_sources as sources
    from app.db import communication_messages as messages
    from app.db import documents, engine

    from app.services.communications.service import _can_write

    can_reply = principal.can(EMAIL_REPLY_CAPABILITY)
    with engine.connect() as c:
        conversations = _conversations_in_scope(c, principal, person_id=person_id,
                                                household_id=household_id)
        if not conversations:
            return []
        # Reply needs WRITE scope, which read scope does not imply. Resolved once per conversation
        # rather than per message — a long thread must not become one scope query per row.
        writable = {cid: _can_write(principal, conv, c)
                    for cid, conv in conversations.items()} if can_reply else {}
        rows = c.execute(select(messages).where(
            messages.c.conversation_id.in_(tuple(conversations)),
            messages.c.channel == "email").order_by(
            messages.c.created_at.desc()).limit(WINDOW)).mappings().all()
        if not rows:
            return []
        ids = tuple(r["id"] for r in rows)

        # Attachments, batched. document_id is the CANONICAL document (Batch 4b), served by the
        # existing authorized /documents/{id}/download route — record scope is enforced there, by
        # the middleware, not by this link's existence.
        attachment_rows = c.execute(select(
            attachments.c.message_id, attachments.c.document_id, attachments.c.description).where(
            attachments.c.message_id.in_(ids),
            attachments.c.document_id.is_not(None))).mappings().all()
        doc_ids = tuple({a["document_id"] for a in attachment_rows})
        names = {}
        if doc_ids:
            names = {d["id"]: (d["original_name"] or d["stored_name"] or f"Document {d['id']}")
                     for d in c.execute(select(
                         documents.c.id, documents.c.original_name, documents.c.stored_name).where(
                         documents.c.id.in_(doc_ids))).mappings()}

        # Which messages carry a real provider identity — the precondition for ADR-075's reply.
        repliable = set(c.scalars(select(sources.c.message_id).where(
            sources.c.message_id.in_(ids),
            sources.c.source_system == PROVIDER_SOURCE_SYSTEM)).all())

    by_message: dict[int, list[dict]] = {}
    for a in attachment_rows:
        by_message.setdefault(a["message_id"], []).append({
            "label": a["description"] or names.get(a["document_id"], "Attachment"),
            # The authorized canonical route. A storage path or Graph URL is never rendered.
            "url": f"/documents/{a['document_id']}/download",
        })

    out = []
    for r in rows:
        conversation = conversations[r["conversation_id"]]
        direction = OUTBOUND if r["direction"] == "outbound" else INBOUND
        meta = r["message_metadata"] or {}
        # ADR-074 keeps a bounded preview of INBOUND email; ADR-075 keeps the firm's OWN outbound
        # words in full. Show what was retained and nothing more — an inbound email must not look
        # like the whole message when only a preview exists.
        retained = direction == OUTBOUND and bool(r["body"])
        out.append(FeedEntry(
            entry_id=f"{EMAIL}:{r['id']}",
            channel=EMAIL,
            direction=direction,
            timestamp=r["created_at"],
            # The canonical sender semantics from 4a/4c: an external correspondent is the person who
            # wrote the email, never "system".
            sender=r["sender_ref"] or ("Client" if direction == INBOUND else "360Plus"),
            subject=r["subject"] or "(no subject)",
            preview=preview_of(meta.get("body_preview") or r["body"]),
            thread_key=f"email:{r['conversation_id']}",
            thread_label=conversation.get("subject") or "Email conversation",
            thread_url=f"/communications/{r['conversation_id']}",
            body=r["body"] if retained else None,
            body_retained=retained,
            attachments=tuple(by_message.get(r["id"], ())),
            unread=None,                    # Outlook read-state is per-mailbox, not a firm fact.
            status=r["status"],
            # Only offered when it will actually work: the capability, a stored provider identity,
            # and write scope on the conversation. The POST re-checks all three (ADR-075).
            reply_url=(f"/communications/messages/{r['id']}/reply"
                       if can_reply and direction == INBOUND and r["id"] in repliable
                       and writable.get(r["conversation_id"]) else None),
            reply_label="Reply by email",
        ))
        stats.note("timeline_composed", interaction_type="email")
    return out
