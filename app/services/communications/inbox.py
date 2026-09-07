"""Cross-client staff communications inbox (Batch 4e) — a DERIVED operational work queue.

WHAT THIS IS. Batch 4d answered "what has this client said to us?" on one client's profile. This
answers the question a staff member actually starts the day with: **across every client I service,
what is waiting for me?** It is a work queue, not a store: every row is derived at read time from the
two authoritative correspondence stores plus the operational state they already carry.

  * ``portal_threads`` / ``portal_messages`` — secure client messaging (D.43), which already owns
    unread (``staff_last_read_at`` vs ``last_client_message_at``), assignment
    (``assigned_user_id`` / ``assigned_team_id``) and resolution (``status``).
  * ``communication_*`` — canonical email (ADR-074 inbound, ADR-075 outbound).

Nothing is written. No third store, no copying between the two, no timeline event, no notification.
Loading this page is a pure read, and four tests assert exactly that.

ATTENTION IS DERIVED DETERMINISTICALLY, NEVER GUESSED.

  Portal — the store already knows. A thread wants attention when the client has spoken since the
  firm last read it (``unread``) or since the firm last replied (``awaiting_reply``), and the thread
  is still open. These are the existing semantics the Messages queue uses; they are not re-invented
  here.

  Email — compared WITHIN one canonical conversation only: the newest inbound message's timestamp
  against the newest outbound one. Later inbound → waiting. Later outbound → answered. Never subject
  matching, never across conversations. Because ADR-074 anchors a conversation to a client only when
  the match is unambiguous, an unanchored or ambiguous email is not in this queue at all — it stays
  in the existing review workflow, which is where a human decides who it belongs to.

WHAT IS DELIBERATELY ABSENT. Email has no unread state here. Outlook's ``isRead`` is one mailbox
owner's flag, not a firm-wide fact, and ADR-074 normalized no firm-level equivalent — so `unread` is
``None`` for email rather than ``False``, and the template can tell "no such concept" from "read".
Email also has no assignment of its own; rather than invent one, the owner is READ from the
authoritative ``record_assignments`` on the client, or left unassigned.

BOUNDED BY CONSTRUCTION. The query count is fixed — scope resolution, then a handful of batched
statements per store — and does not grow with the number of clients, conversations or messages. A
test seeds a larger set and asserts the statement count does not move. Record scope is resolved ONCE
into id sets and applied as a SQL filter, rather than asking the authorization layer per row, which
is what would turn a firm-wide queue into thousands of queries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select

from .engagement.feed import (
    EMAIL,
    EMAIL_REPLY_CAPABILITY,
    PORTAL_REPLY_CAPABILITY,
    READ_CAPABILITY,
    SECURE_MESSAGE,
    preview_of,
)

#: Filters a staff member actually uses to triage. No saved searches, no query builder.
FILTER_ALL = "all"
FILTER_MINE = "mine"
FILTER_UNASSIGNED = "unassigned"
FILTER_ATTENTION = "attention"
FILTER_UNREAD = "unread"
FILTER_RESOLVED = "resolved"
FILTERS = (FILTER_ALL, FILTER_MINE, FILTER_UNASSIGNED, FILTER_ATTENTION, FILTER_UNREAD,
           FILTER_RESOLVED)
FILTER_LABELS = {FILTER_ALL: "All", FILTER_MINE: "My items", FILTER_UNASSIGNED: "Unassigned",
                 FILTER_ATTENTION: "Needs reply", FILTER_UNREAD: "Unread",
                 FILTER_RESOLVED: "Resolved"}

#: Per-store read window before merging. A firm-wide queue must never scan all history.
WINDOW = 250
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

# Work-record anchors that grant a READ of a client, mirroring
# ``security.authorization._WORK_ANCHORS``. Resolved here as ONE batched query per anchor instead of
# four per candidate row.
_WORK_ANCHORS = (("task", "tasks", None), ("exception", "exceptions", None),
                 ("workflow_instance", "workflow_instances", None),
                 ("tax_return", "tax_engagement_returns", "tax_engagements"))


@dataclass(frozen=True)
class QueueItem:
    """One thing waiting, normalized across channels."""

    item_id: str                      # "<channel>:<thread or conversation id>"
    channel: str
    subject: str
    preview: str
    sender: str
    direction: str                    # direction of the latest relevant message
    timestamp: datetime | None        # when that message arrived
    #: True when this row is waiting on the firm. The reason differs per channel and is named in
    #: ``attention_reason`` so the UI never has to guess why a row is here.
    attention: bool = False
    attention_reason: str | None = None
    unread: bool | None = None        # None where the channel has no firm-wide read state (email)
    status: str | None = None
    assigned_name: str | None = None
    assigned_user_id: int | None = None
    assigned_team_id: int | None = None
    assignment_source: str | None = None   # "thread" (real) | "record" (derived) | None
    person_id: int | None = None
    household_id: int | None = None
    client_name: str | None = None
    client_url: str | None = None
    conversation_url: str | None = None
    reply_url: str | None = None
    reply_label: str | None = None
    attachment_count: int = 0
    topic: str | None = None

    @property
    def channel_label(self) -> str:
        return "Secure Message" if self.channel == SECURE_MESSAGE else "Email"

    @property
    def age_days(self) -> int | None:
        if self.timestamp is None:
            return None
        return max(0, (datetime.now(UTC) - _aware(self.timestamp)).days)

    @property
    def age_label(self) -> str:
        days = self.age_days
        if days is None:
            return ""
        if days == 0:
            return "Today"
        return f"{days} day{'s' if days != 1 else ''}"


def _aware(dt):
    if dt is None:
        return datetime.min.replace(tzinfo=UTC)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# --- record scope, resolved once ---------------------------------------------------------------

@dataclass
class Scope:
    """Which clients this principal may service. ``unrestricted`` short-circuits every filter."""

    unrestricted: bool = False
    person_ids: set = field(default_factory=set)
    household_ids: set = field(default_factory=set)
    organization_ids: set = field(default_factory=set)

    def empty(self) -> bool:
        return not (self.unrestricted or self.person_ids or self.household_ids
                    or self.organization_ids)


def resolve_scope(conn, principal) -> Scope:
    """Every client anchor the principal may READ, as id sets, in a fixed number of queries.

    This is the same authority ``record_in_scope`` grants — direct user/team assignment, plus the
    work-derived read path — resolved in bulk instead of one call per candidate row. Restricting the
    SQL by these sets is what keeps a firm-wide queue bounded; asking the authorization layer per
    thread is what a cross-client page cannot afford.
    """
    from app.db import people, record_assignments
    from app.security.authorization import _active, team_ids

    if principal.can("record.read_all"):
        return Scope(unrestricted=True)

    tids = team_ids(conn, principal)
    mine = or_(record_assignments.c.user_id == principal.user_id,
               record_assignments.c.team_id.in_(tuple(tids)) if tids else False)
    active = _active(record_assignments)

    def _assigned(entity_type):
        return set(conn.scalars(select(record_assignments.c.entity_id).where(
            record_assignments.c.entity_type == entity_type, active, mine)).all())

    scope = Scope(person_ids=_assigned("person"), household_ids=_assigned("household"),
                  organization_ids=_assigned("organization"))
    scope.person_ids |= _work_derived(conn, principal, "person_id")
    scope.household_ids |= _work_derived(conn, principal, "household_id")
    # A household assignment carries its members, exactly as the per-client reads treat it.
    if scope.household_ids:
        scope.person_ids |= set(conn.scalars(select(people.c.id).where(
            people.c.household_id.in_(tuple(scope.household_ids)))).all())
    return scope


def _work_derived(conn, principal, owner_col) -> set:
    """Clients reachable because the principal is assigned to a work record they own.

    One statement per anchor table (four), not four per row.
    """
    from app.db import (
        exceptions,
        record_assignments,
        tasks,
        tax_engagement_returns,
        tax_engagements,
        workflow_instances,
    )
    from app.security.authorization import _active

    tables = {"tasks": tasks, "exceptions": exceptions,
              "workflow_instances": workflow_instances,
              "tax_engagement_returns": tax_engagement_returns, "tax_engagements": tax_engagements}
    out: set = set()
    for assignment_type, record_table, via in _WORK_ANCHORS:
        record = tables[record_table]
        owner = tables[via] if via else record
        if owner_col not in owner.c:
            continue
        source = record if via is None else record.join(
            tables[via], tables[via].c.id == record.c.tax_engagement_id)
        query = (select(owner.c[owner_col]).select_from(source.join(
            record_assignments,
            and_(record_assignments.c.entity_id == record.c.id,
                 record_assignments.c.entity_type == assignment_type)))
            .where(record_assignments.c.user_id == principal.user_id,
                   _active(record_assignments), owner.c[owner_col].is_not(None)).distinct())
        out |= set(conn.scalars(query).all())
    return out


def _anchor_filter(table, scope: Scope):
    """SQL restricting a table's client anchor to what the principal may read."""
    if scope.unrestricted:
        return None
    clauses = []
    if scope.person_ids:
        clauses.append(table.c.person_id.in_(tuple(scope.person_ids)))
    if scope.household_ids:
        clauses.append(table.c.household_id.in_(tuple(scope.household_ids)))
    if scope.organization_ids and "organization_id" in table.c:
        clauses.append(table.c.organization_id.in_(tuple(scope.organization_ids)))
    return or_(*clauses) if clauses else None


# --- portal half ---------------------------------------------------------------------------------

def _portal_items(conn, principal, scope: Scope, *, can_reply: bool) -> list[QueueItem]:
    """Secure threads, using the operational state the portal store already maintains."""
    from app.db import people, portal_message_attachments, portal_messages, portal_threads

    anchor = _anchor_filter(portal_threads, scope)
    query = (select(portal_threads, people.c.full_name.label("client_name"))
             .select_from(portal_threads.outerjoin(
                 people, people.c.id == portal_threads.c.person_id))
             .order_by(portal_threads.c.updated_at.desc()).limit(WINDOW))
    if anchor is not None:
        query = query.where(anchor)
    threads = conn.execute(query).mappings().all()
    if not threads:
        return []
    thread_ids = tuple(t["id"] for t in threads)

    # Newest CLIENT-VISIBLE message per thread, in one statement. An internal note is staff talking
    # to staff and must not present as the client's latest word.
    latest = {}
    for m in conn.execute(select(portal_messages).where(
            portal_messages.c.thread_id.in_(thread_ids),
            portal_messages.c.visibility == "client").order_by(
            portal_messages.c.thread_id, portal_messages.c.sent_at.desc()).distinct(
            portal_messages.c.thread_id)).mappings():
        latest[m["thread_id"]] = m

    counts = dict(conn.execute(select(
        portal_messages.c.thread_id, func.count(portal_message_attachments.c.id)).select_from(
        portal_messages.join(portal_message_attachments,
                             portal_message_attachments.c.message_id == portal_messages.c.id))
        .where(portal_messages.c.thread_id.in_(thread_ids))
        .group_by(portal_messages.c.thread_id)).all())

    staff = _staff_names(conn, {t["assigned_user_id"] for t in threads if t["assigned_user_id"]})

    out = []
    for t in threads:
        lcm, lsm, slr = (t["last_client_message_at"], t["last_staff_message_at"],
                         t["staff_last_read_at"])
        unread = lcm is not None and (slr is None or lcm > slr)
        # The store's OWN semantics, unchanged: the client has spoken since the firm last replied.
        awaiting = lcm is not None and (lsm is None or lcm > lsm)
        resolved = t["status"] == "resolved"
        attention = (unread or awaiting) and not resolved
        reason = None
        if attention:
            reason = "Unread client message" if unread else "Awaiting your reply"
        message = latest.get(t["id"])
        out.append(QueueItem(
            item_id=f"{SECURE_MESSAGE}:{t['id']}",
            channel=SECURE_MESSAGE,
            subject=t["subject"] or "Secure message",
            preview=preview_of(message["body"] if message else ""),
            sender=(t["client_name"] or "Client") if (message and not message["sender_user_id"])
            else (staff.get(message["sender_user_id"], "Staff") if message else "—"),
            direction="inbound" if (message and not message["sender_user_id"]) else "outbound",
            timestamp=lcm or t["updated_at"],
            attention=attention, attention_reason=reason,
            unread=unread, status=t["status"],
            assigned_name=staff.get(t["assigned_user_id"]) or (
                "Team" if t["assigned_team_id"] else None),
            assigned_user_id=t["assigned_user_id"], assigned_team_id=t["assigned_team_id"],
            assignment_source="thread",
            person_id=t["person_id"], household_id=t["household_id"],
            client_name=t["client_name"],
            client_url=_client_url(t["person_id"], t["household_id"]),
            conversation_url=f"/admin/client-portal/threads/{t['id']}",
            reply_url=f"/admin/client-portal/threads/{t['id']}" if can_reply else None,
            reply_label="Open thread to reply",
            attachment_count=counts.get(t["id"], 0), topic=t["topic"]))
    return out


# --- email half ----------------------------------------------------------------------------------

def _email_items(conn, principal, scope: Scope, *, can_reply: bool) -> list[QueueItem]:
    """Canonical email conversations, with attention decided inside each conversation."""
    from app.db import communication_attachments as attachments
    from app.db import communication_conversations as conversations
    from app.db import communication_message_sources as sources
    from app.db import communication_messages as messages
    from app.db import people

    anchor = _anchor_filter(conversations, scope)
    query = (select(conversations, people.c.full_name.label("client_name"))
             .select_from(conversations.outerjoin(people, people.c.id == conversations.c.person_id))
             .where(conversations.c.channel == "email")
             .order_by(conversations.c.last_message_at.desc().nullslast()).limit(WINDOW))
    if anchor is not None:
        query = query.where(anchor)
    rows = conn.execute(query).mappings().all()
    if not rows:
        return []
    conversation_ids = tuple(r["id"] for r in rows)

    # THE ATTENTION RULE, in one statement: the newest message per direction, per conversation.
    # Comparing these two timestamps is the whole of "does this need a reply" — no subject matching,
    # and never a comparison across conversations.
    extremes: dict[int, dict] = {}
    for cid, direction, newest in conn.execute(select(
            messages.c.conversation_id, messages.c.direction, func.max(messages.c.created_at))
            .where(messages.c.conversation_id.in_(conversation_ids),
                   messages.c.channel == "email")
            .group_by(messages.c.conversation_id, messages.c.direction)).all():
        extremes.setdefault(cid, {})[direction] = newest

    latest = {}
    for m in conn.execute(select(messages).where(
            messages.c.conversation_id.in_(conversation_ids),
            messages.c.channel == "email").order_by(
            messages.c.conversation_id, messages.c.created_at.desc()).distinct(
            messages.c.conversation_id)).mappings():
        latest[m["conversation_id"]] = m

    message_ids = tuple(m["id"] for m in latest.values())
    counts = dict(conn.execute(select(
        attachments.c.message_id, func.count(attachments.c.id)).where(
        attachments.c.message_id.in_(message_ids or (-1,))).group_by(
        attachments.c.message_id)).all()) if message_ids else {}
    # A reply needs a stored provider identity (ADR-075) — offering it without one is a button that
    # predictably fails.
    repliable = set(conn.scalars(select(sources.c.message_id).where(
        sources.c.message_id.in_(message_ids or (-1,)),
        sources.c.source_system == "microsoft_graph")).all()) if message_ids else set()

    owners = _record_owners(conn, rows)

    out = []
    for r in rows:
        message = latest.get(r["id"])
        if message is None:
            continue                                  # a conversation with no email is not work
        newest = extremes.get(r["id"], {})
        newest_in, newest_out = newest.get("inbound"), newest.get("outbound")
        waiting = newest_in is not None and (newest_out is None
                                             or _aware(newest_in) > _aware(newest_out))
        direction = message["direction"]
        owner = owners.get((r["person_id"], r["household_id"]))
        out.append(QueueItem(
            item_id=f"{EMAIL}:{r['id']}",
            channel=EMAIL,
            subject=r["subject"] or message["subject"] or "(no subject)",
            preview=preview_of((message["message_metadata"] or {}).get("body_preview")
                               or message["body"]),
            sender=message["sender_ref"] or ("Client" if direction == "inbound" else "360Plus"),
            direction=direction,
            timestamp=message["created_at"],
            attention=waiting,
            attention_reason="Latest message is inbound" if waiting else None,
            # Deliberately absent: Outlook's read flag belongs to one mailbox, not the firm, and
            # ADR-074 normalized no firm-level unread state. None, never False.
            unread=None,
            status=r["status"],
            assigned_name=owner[1] if owner else None,
            assigned_user_id=owner[0] if owner else None,
            # Email has no assignment model of its own; this is READ from the authoritative record
            # assignment rather than invented here.
            assignment_source="record" if owner else None,
            person_id=r["person_id"], household_id=r["household_id"],
            client_name=r["client_name"],
            client_url=_client_url(r["person_id"], r["household_id"]),
            conversation_url=f"/communications/{r['id']}",
            reply_url=(f"/communications/messages/{message['id']}/reply"
                       if can_reply and direction == "inbound" and message["id"] in repliable
                       else None),
            reply_label="Reply by email",
            attachment_count=counts.get(message["id"], 0)))
    return out


def _record_owners(conn, rows) -> dict:
    """Authoritative owner per (person, household) anchor — one batched query, never per row."""
    from app.db import record_assignments, users
    from app.security.authorization import _active

    people_ids = tuple({r["person_id"] for r in rows if r["person_id"]})
    household_ids = tuple({r["household_id"] for r in rows if r["household_id"]})
    if not people_ids and not household_ids:
        return {}
    clauses = []
    if people_ids:
        clauses.append(and_(record_assignments.c.entity_type == "person",
                            record_assignments.c.entity_id.in_(people_ids)))
    if household_ids:
        clauses.append(and_(record_assignments.c.entity_type == "household",
                            record_assignments.c.entity_id.in_(household_ids)))
    found = {}
    for entity_type, entity_id, user_id, name in conn.execute(select(
            record_assignments.c.entity_type, record_assignments.c.entity_id,
            record_assignments.c.user_id, users.c.display_name).select_from(
            record_assignments.outerjoin(users, users.c.id == record_assignments.c.user_id))
            .where(or_(*clauses), _active(record_assignments),
                   record_assignments.c.user_id.is_not(None))).all():
        found.setdefault((entity_type, entity_id), (user_id, name))
    out = {}
    for r in rows:
        owner = found.get(("person", r["person_id"])) or found.get(
            ("household", r["household_id"]))
        if owner:
            out[(r["person_id"], r["household_id"])] = owner
    return out


def _staff_names(conn, user_ids) -> dict:
    from app.db import users
    ids = tuple(u for u in user_ids if u)
    if not ids:
        return {}
    return dict(conn.execute(select(users.c.id, users.c.display_name).where(
        users.c.id.in_(ids))).all())


def _client_url(person_id, household_id) -> str | None:
    if person_id:
        return f"/client/{person_id}"
    if household_id:
        return f"/client/household/{household_id}"
    return None


# --- the queue -----------------------------------------------------------------------------------

def _sort_key(item: QueueItem):
    """THE ORDERING RULE, stated once.

    1. Everything needing attention comes before everything that does not.
    2. Within attention: OLDEST first — the client who has waited longest is the most urgent, and a
       newest-first queue quietly buries exactly those.
    3. Within the rest: NEWEST first — that section is a recency view, not a backlog.
    """
    stamp = _aware(item.timestamp)
    if item.attention:
        return (0, stamp.timestamp(), item.item_id)
    return (1, -stamp.timestamp(), item.item_id)


def _matches(item: QueueItem, name: str, principal) -> bool:
    if name == FILTER_MINE:
        return item.assigned_user_id == principal.user_id
    if name == FILTER_UNASSIGNED:
        return item.assigned_user_id is None and item.assigned_team_id is None
    if name == FILTER_ATTENTION:
        return item.attention
    if name == FILTER_UNREAD:
        return item.unread is True
    if name == FILTER_RESOLVED:
        return item.status == "resolved"
    return True


def staff_communications_inbox(principal, *, view=FILTER_ALL, channel=None, page=1,
                               page_size=DEFAULT_PAGE_SIZE) -> dict:
    """The cross-client queue. Fails closed on the read capability before touching any store."""
    from app.db import engine

    if not principal.can(READ_CAPABILITY):
        return {"authorized": False, "rows": [], "total": 0, "page": 1, "page_size": page_size,
                "pages": 0, "counts": {}, "filters": {"view": FILTER_ALL, "channel": ""}}

    can_portal_reply = principal.can(PORTAL_REPLY_CAPABILITY)
    can_email_reply = principal.can(EMAIL_REPLY_CAPABILITY)
    items: list[QueueItem] = []
    with engine.connect() as conn:
        scope = resolve_scope(conn, principal)
        # A principal assigned to nothing services nobody: no store is queried, and "unassigned"
        # cannot become a way to enumerate the firm.
        if not scope.empty():
            items += _portal_items(conn, principal, scope, can_reply=can_portal_reply)
            items += _email_items(conn, principal, scope, can_reply=can_email_reply)

    counts = {
        "total": len(items),
        SECURE_MESSAGE: sum(1 for i in items if i.channel == SECURE_MESSAGE),
        EMAIL: sum(1 for i in items if i.channel == EMAIL),
        FILTER_ATTENTION: sum(1 for i in items if i.attention),
        FILTER_UNREAD: sum(1 for i in items if i.unread is True),
        FILTER_MINE: sum(1 for i in items if i.assigned_user_id == principal.user_id),
        FILTER_UNASSIGNED: sum(1 for i in items
                               if i.assigned_user_id is None and i.assigned_team_id is None),
    }

    view = view if view in FILTERS else FILTER_ALL
    rows = [i for i in items if _matches(i, view, principal)]
    if channel in (SECURE_MESSAGE, EMAIL):
        rows = [i for i in rows if i.channel == channel]
    rows.sort(key=_sort_key)

    page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    page = max(1, int(page or 1))
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "authorized": True,
        "rows": rows[start:start + page_size],
        "total": total, "page": page, "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "counts": counts,
        "filters": {"view": view, "channel": channel or ""},
        "window_reached": counts[SECURE_MESSAGE] >= WINDOW or counts[EMAIL] >= WINDOW,
    }
