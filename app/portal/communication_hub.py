"""360Plus Communication Hub — relationship-owned conversation service over the portal messaging
foundation.

Extends the existing portal secure-messaging services (``app.portal.service``) with the hub concepts:
topic/team routing, staff/client read state, a staff work-queue view, request/document linkage, and a
unified relationship communication timeline. It does NOT rebuild messaging — client sends still go
through ``send_message`` / ``create_thread`` and staff replies through ``staff_send_message`` (which now
also maintain the thread activity markers). Every mutation is record-scoped (staff) or portal-scoped
(client), feature-gated where client-facing, and audited through the existing infrastructure.

Routing reuses the existing ``record_assignments`` (users/teams) — no parallel employee directory. When
an assignee cannot be determined the thread is left UNASSIGNED (a review state), never guessed.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select

from app.db import (
    engine,
    people,
    portal_accounts,
    portal_document_requests,
    portal_message_attachments,
    portal_messages,
    portal_notifications,
    portal_thread_participants,
    portal_threads,
    record_assignments,
    teams,
    users,
)
from app.portal.service import create_document_request, portal_scope
from app.security.audit import write_audit_event
from app.security.authorization import organization_in_scope, record_in_scope

# Client-selectable / staff-assignable topics. ``None`` = unset (routes to the unassigned review state).
TOPICS: tuple[str, ...] = ("tax", "wealth", "bookkeeping", "payroll", "business", "documents", "general")

# topic → the record-assignment service line consulted for routing (best-effort; unmatched → any assignee).
_TOPIC_SERVICE = {"tax": "tax", "wealth": "wealth", "bookkeeping": "accounting", "payroll": "payroll",
                  "business": "benefits", "documents": None, "general": None}


def _now():
    return datetime.now(UTC)


def _audit(action, entity_id, *, actor_user_id, request_id, metadata):
    write_audit_event(action=action, entity_type="portal_thread", entity_id=entity_id,
                      actor_user_id=actor_user_id, request_id=request_id or f"commhub-{uuid.uuid4()}",
                      metadata=metadata)


# --- staff record-scope over a thread ----------------------------------------

def load_thread(thread_id):
    with engine.connect() as c:
        return c.execute(select(portal_threads).where(portal_threads.c.id == thread_id)).mappings().one_or_none()


def thread_in_staff_scope(principal, thread, *, write=False) -> bool:
    """A thread is serviceable when its person OR household OR organization is in the staff record scope."""
    if thread is None:
        return False
    for entity_type, entity_id in (("person", thread["person_id"]), ("household", thread["household_id"])):
        if entity_id is not None and record_in_scope(principal, entity_type, entity_id, write=write):
            return True
    if thread["organization_id"] is not None and organization_in_scope(
            principal, thread["organization_id"], write=write):
        return True
    return False


# --- topic / team routing (reuses record_assignments; never guesses) ---------

def suggest_assignment(*, person_id, household_id, organization_id, topic) -> tuple[int | None, int | None]:
    """Best-effort (user_id, team_id) from EXISTING record assignments on the thread's entities. Returns
    (None, None) — the unassigned review state — when it cannot be determined unambiguously."""
    anchors = [("person", person_id), ("household", household_id), ("organization", organization_id)]
    with engine.connect() as c:
        for entity_type, entity_id in anchors:
            if entity_id is None:
                continue
            rows = c.execute(select(record_assignments.c.user_id, record_assignments.c.team_id).where(
                record_assignments.c.entity_type == entity_type,
                record_assignments.c.entity_id == entity_id,
                record_assignments.c.inactive_date.is_(None))).mappings().all()
            users_ = {r["user_id"] for r in rows if r["user_id"]}
            teams_ = {r["team_id"] for r in rows if r["team_id"]}
            if len(users_) == 1:
                return (next(iter(users_)), next(iter(teams_)) if len(teams_) == 1 else None)
            if len(teams_) == 1:
                return (None, next(iter(teams_)))
    return (None, None)


def route_thread(thread_id, *, actor_user_id=None, request_id=None):
    """Auto-route a thread to its responsible staff/team from existing assignments; unassigned if unknown."""
    thread = load_thread(thread_id)
    if thread is None:
        raise ValueError("Thread not found")
    user_id, team_id = suggest_assignment(person_id=thread["person_id"], household_id=thread["household_id"],
                                          organization_id=thread["organization_id"], topic=thread["topic"])
    if user_id is None and team_id is None:
        return {"thread_id": thread_id, "assigned_user_id": None, "assigned_team_id": None,
                "state": "unassigned"}
    with engine.begin() as c:
        c.execute(portal_threads.update().where(portal_threads.c.id == thread_id).values(
            assigned_user_id=user_id, assigned_team_id=team_id, updated_at=_now()))
    _audit("portal.thread.routed", thread_id, actor_user_id=actor_user_id, request_id=request_id,
           metadata={"assigned_user_id": user_id, "assigned_team_id": team_id, "auto": True})
    return {"thread_id": thread_id, "assigned_user_id": user_id, "assigned_team_id": team_id,
            "state": "assigned"}


# --- assignable directory (reuses the existing users/teams identity tables) ---

def assignable_users() -> list[dict]:
    """Active employees a conversation may be assigned to — from the existing identity ``users`` table
    (the same source as the employee directory); no second directory."""
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(users.c.id, users.c.display_name).where(
            users.c.status == "active").order_by(users.c.display_name)).mappings().all()]


def assignable_teams() -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(teams.c.id, teams.c.name).order_by(
            teams.c.name)).mappings().all()]


def _validate_assignees(conn, user_id, team_id):
    """Reject anything not actually selectable — a forged/invalid/inactive id is not assignable."""
    if user_id is not None and conn.scalar(select(users.c.id).where(
            users.c.id == user_id, users.c.status == "active")) is None:
        raise ValueError("assigned_user_id is not a selectable active employee")
    if team_id is not None and conn.scalar(select(teams.c.id).where(teams.c.id == team_id)) is None:
        raise ValueError("assigned_team_id is not a valid team")


# --- staff mutations (caller MUST pass record scope; these audit prev/new) ----

def reassign_thread(actor_user_id, thread_id, *, user_id=None, team_id=None, topic=None, request_id=None):
    thread = load_thread(thread_id)
    if thread is None:
        raise ValueError("Thread not found")
    if topic is not None and topic not in TOPICS:
        raise ValueError(f"invalid topic {topic!r}")
    prev = {"assigned_user_id": thread["assigned_user_id"], "assigned_team_id": thread["assigned_team_id"],
            "topic": thread["topic"]}
    values = {"assigned_user_id": user_id, "assigned_team_id": team_id, "updated_at": _now()}
    if topic is not None:
        values["topic"] = topic
    with engine.begin() as c:
        _validate_assignees(c, user_id, team_id)          # only valid, selectable users/teams (else raise)
        c.execute(portal_threads.update().where(portal_threads.c.id == thread_id).values(**values))
    _audit("portal.thread.assigned", thread_id, actor_user_id=actor_user_id, request_id=request_id,
           metadata={"previous": prev, "new": {"assigned_user_id": user_id, "assigned_team_id": team_id,
                                               "topic": topic if topic is not None else thread["topic"]}})
    return {"thread_id": thread_id, "assigned_user_id": user_id, "assigned_team_id": team_id}


def set_thread_state(actor_user_id, thread_id, *, resolved, request_id=None):
    thread = load_thread(thread_id)
    if thread is None:
        raise ValueError("Thread not found")
    prev = thread["status"]
    now = _now()
    values = ({"status": "resolved", "resolved_at": now, "resolved_by_user_id": actor_user_id,
               "updated_at": now} if resolved
              else {"status": "open", "resolved_at": None, "resolved_by_user_id": None, "updated_at": now})
    with engine.begin() as c:
        c.execute(portal_threads.update().where(portal_threads.c.id == thread_id).values(**values))
    _audit("portal.thread.resolved" if resolved else "portal.thread.reopened", thread_id,
           actor_user_id=actor_user_id, request_id=request_id,
           metadata={"previous": prev, "new": "resolved" if resolved else "open"})
    return {"thread_id": thread_id, "status": "resolved" if resolved else "open"}


def mark_thread_read_staff(thread_id, *, actor_user_id):
    """Relationship-level staff read marker (thread-level, not per page view)."""
    with engine.begin() as c:
        c.execute(portal_threads.update().where(portal_threads.c.id == thread_id).values(
            staff_last_read_at=_now()))


# --- request / document linkage ----------------------------------------------

def link_request(actor_user_id, thread_id, request_id_value, *, request_id=None):
    thread = load_thread(thread_id)
    if thread is None:
        raise ValueError("Thread not found")
    with engine.begin() as c:
        req = c.execute(select(portal_document_requests.c.person_id).where(
            portal_document_requests.c.id == request_id_value)).mappings().one_or_none()
        if req is None:
            raise ValueError("Document request not found")
        if req["person_id"] != thread["person_id"]:
            raise PermissionError("Request belongs to a different client")   # no cross-client linkage
        c.execute(portal_document_requests.update().where(
            portal_document_requests.c.id == request_id_value).values(thread_id=thread_id))
    _audit("portal.request.linked", thread_id, actor_user_id=actor_user_id, request_id=request_id,
           metadata={"request_id": request_id_value, "thread_id": thread_id})
    return {"thread_id": thread_id, "request_id": request_id_value}


def create_request_from_thread(actor_user_id, thread_id, *, title, description=None, due_date=None,
                               request_id=None):
    """Turn a conversation into an actionable document request without duplicating the workflow engine."""
    thread = load_thread(thread_id)
    if thread is None:
        raise ValueError("Thread not found")
    new_request_id = create_document_request(
        person_id=thread["person_id"], household_id=thread["household_id"], title=title,
        requested_by_user_id=actor_user_id, description=description, due_date=due_date)
    with engine.begin() as c:
        c.execute(portal_document_requests.update().where(
            portal_document_requests.c.id == new_request_id).values(thread_id=thread_id))
    _audit("portal.request.created_from_thread", thread_id, actor_user_id=actor_user_id,
           request_id=request_id, metadata={"request_id": new_request_id, "thread_id": thread_id})
    return {"thread_id": thread_id, "request_id": new_request_id}


def linked_requests(thread_id) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(select(
            portal_document_requests.c.id, portal_document_requests.c.title,
            portal_document_requests.c.status, portal_document_requests.c.due_date).where(
            portal_document_requests.c.thread_id == thread_id)).mappings().all()
    return [dict(r) for r in rows]


# --- staff work-queue (reuses record scope; NOT a separate CRM inbox) --------

def staff_inbox(principal, *, unread=False, assigned_to_me=False, unassigned=False, topic=None,
                status=None, limit=100) -> list[dict]:
    """Conversations the staff member can service, with unread + last-activity + assignment, filtered."""
    with engine.connect() as c:
        rows = c.execute(
            select(portal_threads.c.id, portal_threads.c.subject, portal_threads.c.topic,
                   portal_threads.c.status, portal_threads.c.person_id, portal_threads.c.household_id,
                   portal_threads.c.organization_id, portal_threads.c.assigned_user_id,
                   portal_threads.c.assigned_team_id, portal_threads.c.last_client_message_at,
                   portal_threads.c.last_staff_message_at, portal_threads.c.staff_last_read_at,
                   portal_threads.c.updated_at, people.c.full_name)
            .select_from(portal_threads.outerjoin(people, people.c.id == portal_threads.c.person_id))
            .order_by(portal_threads.c.updated_at.desc()).limit(500)).mappings().all()
    out = []
    for r in rows:
        if not thread_in_staff_scope(principal, r):
            continue
        lcm, slr = r["last_client_message_at"], r["staff_last_read_at"]
        is_unread = lcm is not None and (slr is None or lcm > slr)
        if unread and not is_unread:
            continue
        if assigned_to_me and r["assigned_user_id"] != principal.user_id:
            continue
        if unassigned and (r["assigned_user_id"] is not None or r["assigned_team_id"] is not None):
            continue
        if topic and r["topic"] != topic:
            continue
        if status and r["status"] != status:
            continue
        out.append({**dict(r), "unread": is_unread})
        if len(out) >= limit:
            break
    return out


def person_threads(principal, *, person_id, household_id=None, limit=50) -> list[dict]:
    """Secure conversations to show on ONE person's client profile, record-scoped.

    Reads the SAME ``portal_threads`` rows the Communication Hub reads — no second store, no copy.

    HOUSEHOLD RULE. A thread anchored to a NAMED person belongs to that person, so it appears on
    their profile and nowhere else; showing one spouse's conversation on the other's profile would
    misattribute it, which is exactly the duplication to avoid. A thread with NO person anchor
    (``person_id IS NULL``) is a household-level conversation and appears on every member's profile
    of that household, flagged ``household_thread`` so staff can see why it is there. Client-side
    household VISIBILITY (``shared_household_ids``) is a separate question governed by
    ``portal.household_enabled`` and is deliberately not re-implemented here.

    Scope is enforced per thread by the same :func:`thread_in_staff_scope` the hub and thread page
    use, so this cannot surface a conversation the principal may not service."""
    anchors = [portal_threads.c.person_id == person_id]
    if household_id is not None:
        anchors.append(and_(portal_threads.c.person_id.is_(None),
                            portal_threads.c.household_id == household_id))
    with engine.connect() as c:
        rows = c.execute(
            select(portal_threads.c.id, portal_threads.c.subject, portal_threads.c.topic,
                   portal_threads.c.status, portal_threads.c.person_id,
                   portal_threads.c.household_id, portal_threads.c.organization_id,
                   portal_threads.c.assigned_user_id, portal_threads.c.assigned_team_id,
                   portal_threads.c.last_client_message_at,
                   portal_threads.c.last_staff_message_at,
                   portal_threads.c.staff_last_read_at, portal_threads.c.updated_at)
            .where(or_(*anchors))
            .order_by(portal_threads.c.updated_at.desc()).limit(limit * 4)).mappings().all()
    out = []
    for r in rows:
        if not thread_in_staff_scope(principal, r):
            continue
        lcm, lsm, slr = (r["last_client_message_at"], r["last_staff_message_at"],
                         r["staff_last_read_at"])
        out.append({
            **dict(r),
            "unread": lcm is not None and (slr is None or lcm > slr),
            # The client has spoken more recently than the firm has — the thread wants a reply.
            "awaiting_reply": lcm is not None and (lsm is None or lcm > lsm),
            "household_thread": r["person_id"] is None,
            "assigned_name": staff_name(r["assigned_user_id"]),
        })
        if len(out) >= limit:
            break
    return out


def staff_thread_messages(thread_id) -> list[dict]:
    """ALL messages including internal notes — staff view (caller enforces record scope)."""
    with engine.connect() as c:
        rows = c.execute(select(portal_messages).where(
            portal_messages.c.thread_id == thread_id).order_by(portal_messages.c.sent_at)).mappings().all()
    return [dict(r) for r in rows]


# --- client-facing read state + conversation list ----------------------------

def client_conversations(principal, scope=None) -> list[dict]:
    """The client's conversations with topic, unread-staff-response flag, and linked-request counts."""
    scope = scope or portal_scope(principal.account_id, permission="messages")
    with engine.connect() as c:
        rows = c.execute(select(
            portal_threads.c.id, portal_threads.c.subject, portal_threads.c.topic,
            portal_threads.c.status, portal_threads.c.last_staff_message_at,
            portal_threads.c.client_last_read_at, portal_threads.c.updated_at).where(
            or_(portal_threads.c.person_id.in_(scope["person_ids"]),
                portal_threads.c.household_id.in_(scope["shared_household_ids"])))
            .order_by(portal_threads.c.updated_at.desc()).limit(50)).mappings().all()
    out = []
    for r in rows:
        lsm, clr = r["last_staff_message_at"], r["client_last_read_at"]
        out.append({**dict(r), "unread": lsm is not None and (clr is None or lsm > clr),
                    "linked_requests": len(linked_requests(r["id"]))})
    return out


def mark_thread_read_client(principal, thread_id):
    """Client read marker; portal-scoped (a client may only mark their own thread read)."""
    scope = portal_scope(principal.account_id, permission="messages")
    with engine.begin() as c:
        allowed = c.scalar(select(portal_threads.c.id).where(
            portal_threads.c.id == thread_id,
            or_(portal_threads.c.person_id.in_(scope["person_ids"]),
                portal_threads.c.household_id.in_(scope["shared_household_ids"]))))
        if not allowed:
            raise PermissionError("Thread is outside portal access scope")
        c.execute(portal_threads.update().where(portal_threads.c.id == thread_id).values(
            client_last_read_at=_now()))


# --- unified relationship communication timeline (read model) ----------------

def relationship_timeline(*, person_ids, household_ids, account_ids=None, include_internal=False,
                          limit=100) -> list[dict]:
    """Chronological communication activity for a relationship, composed from the EXISTING tables
    (messages, document requests, notifications) — no new history store. ``include_internal`` adds
    internal staff notes for the STAFF view; the client view must always pass ``include_internal=False``.
    """
    events: list[dict] = []
    pid_t = tuple(person_ids) or (-1,)
    hh_t = tuple(household_ids) or (-1,)
    with engine.connect() as c:
        thread_ids = [r[0] for r in c.execute(select(portal_threads.c.id).where(
            or_(portal_threads.c.person_id.in_(pid_t),
                portal_threads.c.household_id.in_(hh_t)))).all()]
        if thread_ids:
            vis = ("client", "internal") if include_internal else ("client",)
            for m in c.execute(select(portal_messages).where(
                    portal_messages.c.thread_id.in_(tuple(thread_ids)),
                    portal_messages.c.visibility.in_(vis)).order_by(
                    portal_messages.c.sent_at.desc()).limit(limit)).mappings():
                kind = ("internal_note" if m["visibility"] == "internal"
                        else ("staff_reply" if m["sender_user_id"] else "client_message"))
                events.append({"when": m["sent_at"], "kind": kind, "thread_id": m["thread_id"],
                               "title": m["body"][:120], "channel": m["channel"]})
        for r in c.execute(select(portal_document_requests).where(
                portal_document_requests.c.person_id.in_(pid_t)).order_by(
                portal_document_requests.c.updated_at.desc()).limit(limit)).mappings():
            events.append({"when": r["created_at"], "kind": "document_request",
                           "title": f"Requested: {r['title']}", "request_id": r["id"], "status": r["status"]})
            if r["uploaded_at"]:
                events.append({"when": r["uploaded_at"], "kind": "request_completed",
                               "title": f"Uploaded: {r['title']}", "request_id": r["id"]})
        if account_ids:
            for n in c.execute(select(portal_notifications).where(
                    portal_notifications.c.portal_account_id.in_(tuple(account_ids))).order_by(
                    portal_notifications.c.created_at.desc()).limit(limit)).mappings():
                events.append({"when": n["created_at"], "kind": "notification", "title": n["title"],
                               "channel": n["channel"]})
    events.sort(key=lambda e: e["when"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return events[:limit]


def staff_name(user_id):
    if user_id is None:
        return None
    with engine.connect() as c:
        return c.scalar(select(users.c.display_name).where(users.c.id == user_id))


def message_attachment_ids(message_id) -> list[int]:
    with engine.connect() as c:
        return [r[0] for r in c.execute(select(portal_message_attachments.c.document_id).where(
            portal_message_attachments.c.message_id == message_id)).all()]


# --- staff-initiated conversations -------------------------------------------

#: Longest values accepted from the compose form. The columns are VARCHAR(255) for the subject and
#: TEXT for the body; these bounds stop a single submission becoming a denial-of-service payload and
#: are enforced server-side, never only in the browser.
#: Shown when the compose form is submitted without a client chosen. Matches the invite form's
#: wording so the two staff surfaces read the same way.
NO_CLIENT_SELECTED = "Select a client before starting a conversation."

MAX_SUBJECT = 200
MAX_BODY = 10000


class StaffMessageError(ValueError):
    """A staff-initiated conversation was refused. The message is staff-facing and names no id."""


def client_portal_status(person_id):
    """``(portal_account_id_or_None, human_reason_or_None)`` for one person.

    A conversation needs somewhere for the client to read it, so a person with no ACTIVE portal
    account cannot be messaged. This reports WHY in words staff can act on, and deliberately does
    NOT create or invite anything — invitations remain the explicit, audited workflow on the Client
    Portal Administration screen."""
    with engine.connect() as connection:
        row = connection.execute(select(portal_accounts.c.id, portal_accounts.c.status).where(
            portal_accounts.c.person_id == person_id)
            .order_by(portal_accounts.c.created_at.desc()).limit(1)).mappings().one_or_none()
    if row is None:
        return None, ("This client does not have a portal account yet. Invite them from Client "
                      "Portal Administration first, then start the conversation.")
    if row["status"] == "revoked":
        return None, ("This client's portal access has been revoked. Re-invite them from Client "
                      "Portal Administration before sending a secure message.")
    if row["status"] != "active":
        return None, ("This client has been invited but has not activated their portal account yet. "
                      "They will be able to receive secure messages once they sign in.")
    return row["id"], None


def staff_start_thread(principal, *, person_id, subject, body, topic=None, request_id=None):
    """Open a conversation with a client the staff member is authorised to service.

    Authorisation is derived entirely from the staff Principal: the person id arriving from the
    browser is a CLAIM, re-resolved and re-checked here with ``record_in_scope(..., write=True)``
    before anything is written. Starting a conversation writes to the client's record, so it needs
    write scope, matching the reply route rather than the read-only inbox.

    The opening message is a STAFF message: ``sender_user_id`` is taken from the Principal and the
    browser has no say in it, so ``sender_type`` cannot be forged. The thread is created already
    read for staff (they just wrote it) and unread for the client, which is what the existing
    ``staff_last_read_at`` / ``client_last_read_at`` markers are for.

    Raises :class:`StaffMessageError` with a human-readable reason when the client has no usable
    portal account. Nothing is created in that case — no thread, no account, no invitation."""
    from app.services.timeline import add_timeline_event

    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject:
        raise StaffMessageError("A subject is required.")
    if not body:
        raise StaffMessageError("A message is required.")
    if len(subject) > MAX_SUBJECT:
        raise StaffMessageError(f"The subject is too long (limit {MAX_SUBJECT} characters).")
    if len(body) > MAX_BODY:
        raise StaffMessageError(f"The message is too long (limit {MAX_BODY} characters).")
    if topic is not None and topic not in TOPICS:
        raise StaffMessageError("That topic is not one of the available topics.")

    with engine.connect() as connection:
        person = connection.execute(select(people.c.id, people.c.household_id, people.c.full_name)
                                    .where(people.c.id == person_id)).mappings().one_or_none()
    # Same refusal for "no such person" and "not yours": a staff member must not be able to probe
    # for the existence of records outside their scope by watching the error change.
    if person is None or not record_in_scope(principal, "person", person["id"], write=True):
        raise StaffMessageError("That client is not available to you.")

    account_id, reason = client_portal_status(person["id"])
    if account_id is None:
        raise StaffMessageError(reason)

    now = datetime.now(UTC)
    with engine.begin() as connection:
        thread_id = connection.execute(portal_threads.insert().values(
            person_id=person["id"], household_id=person["household_id"], subject=subject,
            topic=topic, status="open", created_by_user_id=principal.user_id,
            assigned_user_id=principal.user_id,
            last_staff_message_at=now, staff_last_read_at=now,   # the author has read it
            updated_at=now).returning(portal_threads.c.id)).scalar_one()
        connection.execute(portal_thread_participants.insert().values(
            thread_id=thread_id, portal_account_id=account_id, participant_role="client"))
        connection.execute(portal_thread_participants.insert().values(
            thread_id=thread_id, user_id=principal.user_id, participant_role="staff"))
        message_id = connection.execute(portal_messages.insert().values(
            thread_id=thread_id, sender_user_id=principal.user_id, body=body,
            visibility="client").returning(portal_messages.c.id)).scalar_one()

    add_timeline_event(person_id=person["id"], household_id=person["household_id"],
                       source="client_portal", event_type="secure_message",
                       title="Secure staff message", external_id=f"portal-message-{message_id}",
                       event_metadata={"thread_id": thread_id})
    # Metadata identifies actor, thread and client entity — never the subject or the body.
    write_audit_event(action="portal.thread.created", entity_type="portal_thread",
                      entity_id=thread_id, actor_user_id=principal.user_id,
                      request_id=request_id or f"staff-thread-{uuid.uuid4()}",
                      metadata={"person_id": person["id"], "household_id": person["household_id"],
                                "initiated_by": "staff"})
    return thread_id
