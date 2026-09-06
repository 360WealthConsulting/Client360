"""Secure portal messages → unified feed entries (Batch 4d).

READ-ONLY over ``portal_threads`` / ``portal_messages``, the authoritative D.43 store for secure
client messaging. Nothing here copies a portal message into ``communication_messages``: the two
stores stay separate and this is a second VIEW of one of them, exactly as the Client 360 Messages tab
already is.

SCOPE IS DELEGATED, NOT REIMPLEMENTED. Thread visibility comes from
``communication_hub.person_threads``, which applies the same per-thread ``thread_in_staff_scope``
check the hub and the thread page use — including its household rule (a thread anchored to a named
person appears only on that person's profile; an unanchored thread is a household conversation and
appears for every member). Messages are then read only for threads that reader already allowed, so a
conversation this principal may not service cannot appear here.

INTERNAL NOTES. ``portal_messages.visibility`` separates client-visible messages from staff-only
notes. This is a STAFF surface, so notes are included and labelled as internal notes — never shown as
something the client sent or saw. The client-facing portal composition passes ``include_internal
=False`` and is a different adapter entirely.

FIXED QUERY COUNT. Four statements after the scoped thread read: messages, attachments, attachment
document names, sender names.
"""
from __future__ import annotations

from sqlalchemy import select

from .. import stats
from ..feed import (
    INBOUND,
    INTERNAL_NOTE,
    OUTBOUND,
    PORTAL_REPLY_CAPABILITY,
    SECURE_MESSAGE,
    WINDOW,
    FeedEntry,
    preview_of,
)

INTERNAL_VISIBILITY = "internal"


#: Members whose threads a household view will gather. `person_threads` reads one person at a time,
#: so this is bounded by household size (a handful), never by history length.
MAX_MEMBERS = 12


def portal_entries(principal, *, person_id=None, household_id=None,
                   member_ids=()) -> list[FeedEntry]:
    """Secure portal messages for one client (or a household's members) as feed entries. Fail-closed."""
    try:
        return _portal_entries(principal, person_id=person_id, household_id=household_id,
                               member_ids=member_ids)
    except Exception:                       # noqa: BLE001 — a store outage degrades, never 500s
        stats.note("adapter_failures", source="portal_messages")
        return []


def _portal_entries(principal, *, person_id, household_id, member_ids=()) -> list[FeedEntry]:
    from app.db import documents, engine, portal_accounts, portal_message_attachments
    from app.db import portal_messages, users
    from app.portal import communication_hub as hub

    people_ids = [person_id] if person_id is not None else list(member_ids)[:MAX_MEMBERS]
    if not people_ids:
        return []
    # `person_threads` already applies the per-thread staff scope check and the household rule; the
    # dict de-duplicates the household-level threads every member's read returns.
    by_id = {}
    for pid in people_ids:
        for t in hub.person_threads(principal, person_id=pid, household_id=household_id):
            by_id[t["id"]] = t
    threads = list(by_id.values())
    if not threads:
        return []
    by_thread = {t["id"]: t for t in threads}
    can_reply = principal.can(PORTAL_REPLY_CAPABILITY)

    with engine.connect() as c:
        rows = c.execute(select(portal_messages).where(
            portal_messages.c.thread_id.in_(tuple(by_thread))).order_by(
            portal_messages.c.sent_at.desc()).limit(WINDOW)).mappings().all()
        if not rows:
            return []
        ids = tuple(r["id"] for r in rows)

        attachment_rows = c.execute(select(
            portal_message_attachments.c.message_id,
            portal_message_attachments.c.document_id,
            portal_message_attachments.c.vault_document_id).where(
            portal_message_attachments.c.message_id.in_(ids))).mappings().all()
        doc_ids = tuple({a["document_id"] for a in attachment_rows if a["document_id"]})
        names = {}
        if doc_ids:
            names = {d["id"]: (d["original_name"] or d["stored_name"] or f"Document {d['id']}")
                     for d in c.execute(select(
                         documents.c.id, documents.c.original_name, documents.c.stored_name).where(
                         documents.c.id.in_(doc_ids))).mappings()}

        staff_ids = tuple({r["sender_user_id"] for r in rows if r["sender_user_id"]})
        staff = dict(c.execute(select(users.c.id, users.c.display_name).where(
            users.c.id.in_(staff_ids))).all()) if staff_ids else {}
        account_ids = tuple({r["sender_portal_account_id"] for r in rows
                             if r["sender_portal_account_id"]})
        clients = dict(c.execute(select(
            portal_accounts.c.id, portal_accounts.c.display_name).where(
            portal_accounts.c.id.in_(account_ids))).all()) if account_ids else {}

    by_message: dict[int, list[dict]] = {}
    for a in attachment_rows:
        # Two authorized routes, one word for the reader. A staff-published attachment is a canonical
        # document; a client-uploaded one lives in the Vault (Batch 3b/3c). The user should see
        # "attachment" — which store backs it is not their problem, and authorization is enforced by
        # whichever route serves it, not by this label.
        if a["vault_document_id"]:
            by_message.setdefault(a["message_id"], []).append({
                "label": f"Attachment {a['vault_document_id']}",
                "url": f"/api/vault/documents/{a['vault_document_id']}/download"})
        elif a["document_id"]:
            by_message.setdefault(a["message_id"], []).append({
                "label": names.get(a["document_id"], "Attachment"),
                "url": f"/documents/{a['document_id']}/download"})

    out = []
    for r in rows:
        thread = by_thread[r["thread_id"]]
        internal = r["visibility"] == INTERNAL_VISIBILITY
        if internal:
            direction, sender = INTERNAL_NOTE, (staff.get(r["sender_user_id"]) or "Staff")
        elif r["sender_user_id"]:
            direction, sender = OUTBOUND, (staff.get(r["sender_user_id"]) or "Staff")
        else:
            direction, sender = INBOUND, (clients.get(r["sender_portal_account_id"]) or "Client")
        out.append(FeedEntry(
            entry_id=f"{SECURE_MESSAGE}:{r['id']}",
            channel=SECURE_MESSAGE,
            direction=direction,
            timestamp=r["sent_at"],
            sender=sender,
            subject=thread.get("subject") or "Secure message",
            preview=preview_of(r["body"]),
            thread_key=f"portal:{r['thread_id']}",
            thread_label=thread.get("subject") or "Secure conversation",
            thread_url=f"/admin/client-portal/threads/{r['thread_id']}",
            body=r["body"],                 # the portal store retains the full message
            body_retained=True,
            attachments=tuple(by_message.get(r["id"], ())),
            # Thread-level unread: the client has spoken since the firm last read the thread. Real
            # state from the store, not inferred from timing.
            unread=bool(thread.get("unread")),
            status=thread.get("status"),
            # The existing secure-message reply flow, on the thread page that already enforces it.
            reply_url=(f"/admin/client-portal/threads/{r['thread_id']}" if can_reply else None),
            reply_label="Open thread to reply",
        ))
        stats.note("timeline_composed", interaction_type="secure_message")
    return out
