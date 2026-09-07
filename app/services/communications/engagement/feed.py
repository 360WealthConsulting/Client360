"""Unified staff communications feed (Batch 4d) — one chronological history across channels.

WHAT THIS IS. A read-only COMPOSITION, in the ADR-049 sense, over the two AUTHORITATIVE stores that
own client correspondence:

  * ``portal_threads`` / ``portal_messages``  — secure client-portal messaging (D.43)
  * ``communication_*``                       — canonical email records (ADR-074 inbound, ADR-075 outbound)

Portal and email remain SEPARATE canonical stores. Nothing here copies a portal message into
``communication_messages`` or the reverse; a row in this feed is a reference to a record that stays
owned by its own subsystem, and every action on it deep-links back to that subsystem's authorized
route.

WHY THIS DOES NOT READ THE ACTIVITY TIMELINE. ``engagement_timeline`` composes over
``activity_timeline``, which is the right spine for a *relationship* timeline but cannot answer the
questions this surface exists for. The timeline holds a row per sender-matched INBOUND email and
none at all for outbound replies or recipient-matched inbound (ADR-074/075 deliberately do not write
one — one email, one timeline row). It also has no per-message direction, sender, attachment count or
conversation identity. Composing email from the canonical store instead gives the full exchange AND
avoids the duplication that reading both would cause: a sender-matched inbound email exists in both
places, and this feed takes it from exactly one of them.

This surface therefore emits NO timeline events and adds NO timeline source. Rendering it is a pure
read.

ONE DISPLAY MODEL. Both channels normalize onto :class:`FeedEntry`, so the template never branches on
"is this portal or email" to find a sender or a timestamp. What the channels genuinely do not share
is represented as absence, not as a fake value: an email has no unread state here (``unread`` is
``None``, not ``False``), and an inbound email has no full body (``body`` is ``None`` and
``body_retained`` is ``False``) because ADR-074 retains a bounded preview only.

BOUNDED BY CONSTRUCTION. Each adapter reads one bounded window and the composition paginates it. The
adapters issue a fixed number of batched queries regardless of how many messages come back — see
``_WINDOW`` and the query-count tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Channels. These are the two stores, named as staff would say them — never "portal vs canonical".
SECURE_MESSAGE = "secure_message"
EMAIL = "email"
CHANNEL_LABELS = {SECURE_MESSAGE: "Secure Message", EMAIL: "Email"}

# Direction, in the canonical vocabulary the stores already use (4a/4c).
INBOUND = "inbound"
OUTBOUND = "outbound"
INTERNAL_NOTE = "internal_note"
DIRECTION_LABELS = {INBOUND: "Inbound", OUTBOUND: "Outbound", INTERNAL_NOTE: "Internal note"}

#: Capability required to read message CONTENT in this feed. The same gate the secure-message work
#: queue uses, because this surface shows the same bodies.
READ_CAPABILITY = "communications.message.read"
#: Capability each channel's reply action requires. Checked again by the route that performs it.
PORTAL_REPLY_CAPABILITY = "communications.message.write"
EMAIL_REPLY_CAPABILITY = "communications.send"

#: Per-channel read window before composition. Bounded so a twenty-year client cannot be asked for.
WINDOW = 100
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
PREVIEW_CHARS = 220


def preview_of(text: str | None) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= PREVIEW_CHARS else s[: PREVIEW_CHARS - 1] + "…"


@dataclass(frozen=True)
class FeedEntry:
    """One communication, normalized for display. References its source; never owns it."""

    entry_id: str                       # "<channel>:<source row id>" — stable, source-qualified
    channel: str                        # secure_message | email
    direction: str                      # inbound | outbound | internal_note
    timestamp: datetime | None
    sender: str                         # who actually sent it — never "system" for a real person
    subject: str
    preview: str
    thread_key: str                     # "portal:12" / "email:34" — conversation identity, per store
    thread_label: str
    thread_url: str | None = None
    #: Full content, present only where the source actually retained it (portal messages, outbound
    #: email). ``None`` means the store holds a preview only — never fabricate the rest.
    body: str | None = None
    body_retained: bool = False
    attachments: tuple[dict, ...] = field(default_factory=tuple)
    #: ``None`` where the channel has no meaningful staff read-state (email). Not ``False``.
    unread: bool | None = None
    status: str | None = None
    #: Set only when the reply will actually work — capability, scope and transport all confirmed.
    reply_url: str | None = None
    reply_label: str | None = None

    @property
    def channel_label(self) -> str:
        return CHANNEL_LABELS.get(self.channel, self.channel)

    @property
    def direction_label(self) -> str:
        return DIRECTION_LABELS.get(self.direction, self.direction)

    @property
    def attachment_count(self) -> int:
        return len(self.attachments)

    @property
    def sort_key(self):
        return (self.timestamp or datetime.min.replace(tzinfo=UTC), self.entry_id)

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id, "channel": self.channel,
            "channel_label": self.channel_label, "direction": self.direction,
            "direction_label": self.direction_label,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "sender": self.sender, "subject": self.subject, "preview": self.preview,
            "thread_key": self.thread_key, "thread_label": self.thread_label,
            "thread_url": self.thread_url, "body": self.body,
            "body_retained": self.body_retained, "attachments": list(self.attachments),
            "attachment_count": self.attachment_count, "unread": self.unread,
            "status": self.status, "reply_url": self.reply_url, "reply_label": self.reply_label,
        }


def _aware(dt):
    """Portal and communication timestamps differ in tz-awareness; one comparable key for sorting."""
    if dt is None:
        return datetime.min.replace(tzinfo=UTC)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def client_communications(principal, *, person_id=None, household_id=None, member_ids=(),
                          channel=None, direction=None, page=1,
                          page_size=DEFAULT_PAGE_SIZE) -> dict:
    """The unified feed for one client (or household), newest first.

    Fails CLOSED on the read capability: without it no store is queried at all, so an unauthorized
    principal cannot reach message content by asking for a later page or a narrower filter.
    """
    from .adapters.email_feed import email_entries
    from .adapters.portal_feed import portal_entries

    if not principal.can(READ_CAPABILITY):
        return {"authorized": False, "rows": [], "total": 0, "page": 1,
                "page_size": page_size, "pages": 0, "counts": {}, "filters": {}}
    if person_id is None and household_id is None:
        return {"authorized": True, "rows": [], "total": 0, "page": 1, "page_size": page_size,
                "pages": 0, "counts": {SECURE_MESSAGE: 0, EMAIL: 0}, "filters": {}}

    entries = []
    # Each adapter is fail-closed on its own: one store being unavailable degrades the feed to the
    # other rather than erroring the whole client profile.
    entries += portal_entries(principal, person_id=person_id, household_id=household_id,
                              member_ids=member_ids)
    entries += email_entries(principal, person_id=person_id, household_id=household_id)

    counts = {SECURE_MESSAGE: sum(1 for e in entries if e.channel == SECURE_MESSAGE),
              EMAIL: sum(1 for e in entries if e.channel == EMAIL)}

    if channel in (SECURE_MESSAGE, EMAIL):
        entries = [e for e in entries if e.channel == channel]
    if direction in (INBOUND, OUTBOUND, INTERNAL_NOTE):
        entries = [e for e in entries if e.direction == direction]

    entries.sort(key=lambda e: (_aware(e.timestamp), e.entry_id), reverse=True)

    page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    page = max(1, int(page or 1))
    total = len(entries)
    start = (page - 1) * page_size
    return {
        "authorized": True,
        "rows": entries[start:start + page_size],
        "total": total, "page": page, "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "counts": counts,
        "filters": {"channel": channel or "", "direction": direction or ""},
        # True when a store hit its read window, so the page footer can say the history is bounded
        # rather than implying this is everything that ever happened.
        "window_reached": counts[SECURE_MESSAGE] >= WINDOW or counts[EMAIL] >= WINDOW,
    }
