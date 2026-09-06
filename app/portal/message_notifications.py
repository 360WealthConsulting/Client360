"""Notify the OTHER party when a secure portal message is created.

Portal messaging has been two-way since D.43, but nothing told anyone a message had arrived: a
client learned about a staff reply only by signing in, and staff only by opening the Messages work
queue. This module closes that gap using the notification infrastructure that already exists — it
introduces no table, no channel, no provider, and no transport.

TWO LEDGERS, BECAUSE THE PLATFORM HAS TWO AUDIENCES.

* **Client recipients** go to ``portal_notifications`` through ``app.portal.service.notify`` — the
  ledger the client portal actually renders (``/portal/notifications``). Delivery goes through the
  existing provider registry, where ``in_app`` is enabled and email/SMS/push are disabled hooks that
  record an honest ``disabled`` outcome. Nothing here enables them.
* **Staff recipients** go to the canonical ledger through
  ``app.services.notifications.record_notification`` — intent only, exactly as
  ``communications.delivery`` and ``scheduling.service`` already use it. NOTE: there is no
  staff-facing notification surface in this release, so a staff row is durable, deduplicated intent
  and a hook for a future inbox — it is not yet something staff see. The visible staff signal remains
  the work queue's per-thread unread state (``last_client_message_at`` vs ``staff_last_read_at``),
  which is unchanged.

RECIPIENTS ARE RESOLVED FROM THE EXISTING OWNERSHIP MODEL, NEVER INVENTED.

Staff side, in order: the thread's ``assigned_user_id``, then its ``assigned_team_id``, then
``communication_hub.suggest_assignment`` — the same read-only derivation over ``record_assignments``
that ``route_thread`` uses. When none of those resolves, the thread is in the UNASSIGNED review state
and **no notification is created**; the Hub's rule is that an assignee is never guessed, and this
module does not get to guess either. Resolution is read-only: it never assigns the thread.

Client side: the thread's person must have an ACTIVE portal account, decided by the existing
``communication_hub.client_portal_status``. An invited-but-not-activated or revoked account gets
nothing — there is nowhere for them to read it.

CONTENT. Neither ledger receives the message body. Titles name the counterparty (identity, which both
audiences already see) and metadata carries references only — thread id, message id, and the deep
link to the authoritative surface. A client-authored subject is deliberately NOT copied into a staff
notification either: the work queue shows it, the notification does not need to.

IDEMPOTENCY. One notification per message, keyed on the message id, so a retried call returns the
existing row rather than creating a second. The portal ledger's unique ``idempotency_key`` and the
canonical ledger's unique ``dedupe_key`` are the durable backstops.

TRANSACTIONS. Every entry point here runs AFTER the message transaction has committed, exactly like
the ``add_timeline_event`` / ``write_audit_event`` calls it sits beside. The message is authoritative
and already durable; a notification failure therefore cannot leave a half-written message. Errors are
NOT swallowed — they propagate the same way a timeline write does, because a silently missing
notification is worse than a loud one. A disabled channel is not an error: the provider records a
``disabled`` outcome and the row is still written.
"""
from __future__ import annotations

#: Portal-ledger type (snake_case, matching the other portal notification types).
CLIENT_NOTIFICATION_TYPE = "secure_message"
#: Canonical-ledger type (dotted, matching communication.message / scheduling.reminder).
STAFF_NOTIFICATION_TYPE = "portal.secure_message"

STAFF_THREAD_LINK = "/admin/client-portal/threads/{thread_id}"
CLIENT_THREAD_LINK = "/portal/messages/{thread_id}"

#: One notification per message, in both ledgers.
_KEY = "portal-message:{message_id}"


def _thread(thread_id):
    from sqlalchemy import select

    from app.db import engine, portal_threads
    with engine.connect() as connection:
        return connection.execute(select(portal_threads).where(
            portal_threads.c.id == thread_id)).mappings().one_or_none()


def _person_name(person_id):
    if person_id is None:
        return None
    from sqlalchemy import select

    from app.db import engine, people
    with engine.connect() as connection:
        return connection.scalar(select(people.c.full_name).where(people.c.id == person_id))


def staff_recipient(thread):
    """``(recipient_type, recipient_ref)`` for a thread, or ``None`` when it is unassigned.

    Reuses the Hub's ownership model in its own order of precedence and never assigns anything.
    ``None`` is the UNASSIGNED review state, not a failure: the conversation still appears unread in
    the work queue, which is where an unrouted conversation is meant to be picked up.
    """
    if thread is None:
        return None
    if thread["assigned_user_id"] is not None:
        return ("user", str(thread["assigned_user_id"]))
    if thread["assigned_team_id"] is not None:
        return ("team", str(thread["assigned_team_id"]))
    from app.portal import communication_hub as hub
    user_id, team_id = hub.suggest_assignment(
        person_id=thread["person_id"], household_id=thread["household_id"],
        organization_id=thread["organization_id"], topic=thread["topic"])
    if user_id is not None:
        return ("user", str(user_id))
    if team_id is not None:
        return ("team", str(team_id))
    return None


def notify_staff_of_client_message(thread_id, message_id):
    """A client wrote into a thread — record intent for the staff who own it.

    Returns the ledger record, or ``None`` when the thread is unassigned and no owner can be
    derived. The sending client is never notified of their own message.
    """
    thread = _thread(thread_id)
    recipient = staff_recipient(thread)
    if recipient is None:
        return None
    recipient_type, recipient_ref = recipient
    name = _person_name(thread["person_id"])

    from app.services.notifications import record_notification
    return record_notification(
        notification_type=STAFF_NOTIFICATION_TYPE,
        recipient_type=recipient_type, recipient_ref=recipient_ref, channel="in_app",
        title=f"New secure message from {name}" if name else "New secure message",
        source_ref=_KEY.format(message_id=message_id),
        metadata={"thread_id": thread_id, "message_id": message_id,
                  "person_id": thread["person_id"], "household_id": thread["household_id"],
                  "link": STAFF_THREAD_LINK.format(thread_id=thread_id)},
    )


def notify_client_of_staff_message(thread_id, message_id):
    """Staff wrote a client-visible message — notify the client who can read it.

    Returns the portal notification id, or ``None`` when the thread's person has no ACTIVE portal
    account. Internal staff notes never reach here: the caller skips them, because the client can
    neither see the note nor act on it.
    """
    thread = _thread(thread_id)
    if thread is None or thread["person_id"] is None:
        return None
    from app.portal.communication_hub import client_portal_status
    account_id, _reason = client_portal_status(thread["person_id"])
    if account_id is None:
        return None

    from app.portal.service import notify
    return notify(
        account_id, CLIENT_NOTIFICATION_TYPE, "New secure message from your team",
        entity_type="portal_thread", entity_id=thread_id,
        idempotency_key=_KEY.format(message_id=message_id),
    )
