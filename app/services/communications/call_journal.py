"""Normalize one COMPLETED phone call into canonical ``communication_*`` records — PBX-NEUTRAL.

THE SHAPE IS ``sms_ingest``'s, ON PURPOSE — same anchoring discipline, same database-enforced
identity, same refusal to guess — because a second, differently-shaped normalizer would be the
thing that eventually disagrees with the first about what a client conversation is. It takes an
already-parsed event and a connection; it opens no socket, holds no credential, and names no
vendor. The 3CX half lives in :mod:`app.integrations.threecx`.

NO MIGRATION WAS NEEDED, and that is the point of writing it here. ``phone_log`` has been a legal
``communication_messages.channel`` since the communications platform shipped, and
``communication_message_sources`` already enforces ``UNIQUE (source_system, source_external_id)``.
A call therefore lands in the same conversation ledger as this client's email and text, visible to
the same feed, governed by the same retention and authorization, with no new table to keep in
step.

WHERE A CALL GENUINELY DIFFERS FROM A TEXT, and why that changes the code:

  * **There is no content, and none is wanted.** A call carries no body. This module stores no
    recording, no transcript, no summary and no agent notes — not because the PBX cannot supply
    some of them, but because journaling a call is a record that it happened, and the moment this
    ledger holds what was SAID it becomes a different thing under a different retention rule.
    ``body`` is therefore always ``None``.

  * **The number is metadata, never a label.** The counterparty number is stored once, normalized,
    where threading needs it. Every human-readable surface this module writes — subject line,
    audit metadata, log line — carries only
    :func:`~app.services.communications.phone_numbers.mask_number`'s last-four form, so a full
    client number never reaches a log file or an error message.

  * **Identity may have to be DERIVED, and the caller is told so.** ``sms_ingest`` can refuse a
    payload with no provider message id because every SMS gateway issues one. Not every PBX does:
    3CX v20's call-journaling template exposes no call-id variable at all (see
    :mod:`app.integrations.threecx.template`). So this module prefers a vendor id when one exists
    and otherwise derives a stable one from the tuple that identifies a call anyway — start
    instant, agent, counterparty, direction — and records WHICH of the two it used, so nobody
    later mistakes a derived identity for a vendor-guaranteed one.

  * **Only completed calls.** ``missed`` and ``notanswered`` are refused rather than journaled.
    They are real events, but they are a notification concern with a different lifecycle, and
    admitting them here would silently turn "calls with this client" into "call attempts".

NO TIMELINE EVENT is written, for the same reason ``email_ingest`` and ``sms_ingest`` write none:
the D.44 registry classifies by ``(source, event_type)`` and a second row would double-count the
exchange.

NOT IN SCOPE: any vendor, credential, outbound dialling, recording or transcript retrieval, live
call state, or presence.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import communication_conversations as conversations
from app.db import communication_events as events
from app.db import communication_message_sources as sources
from app.db import communication_messages as messages
from app.db import communication_recipients as recipients_table
from app.services.communications.phone_numbers import mask_number, normalize_phone

#: An allowed ``communication_messages.channel`` since the communications platform migration.
CHANNEL = "phone_log"

INBOUND, OUTBOUND = "inbound", "outbound"
#: The only outcomes this module journals. A call 3CX reports as ``missed`` or ``notanswered`` did
#: not complete and is refused — see the module docstring.
COMPLETED_DIRECTIONS = (INBOUND, OUTBOUND)

#: Provenance namespace. The PBX slug is part of it so two systems can never collide on a call
#: identity, and replacing a PBX does not retroactively reinterpret rows written by the old one.
SOURCE_SYSTEM_PREFIX = "call"

#: How identity was established for a journal row. Recorded on every row so a later reader can
#: tell a vendor-guaranteed id from one this module composed.
IDENTITY_VENDOR = "vendor_call_id"
IDENTITY_DERIVED = "derived_call_tuple"

#: A ceiling on a single call, used only to reject a nonsense duration. 24 hours.
MAX_DURATION_SECONDS = 24 * 60 * 60


class CallJournalError(ValueError):
    """The event cannot be journaled safely. Never raised for an ordinary unmatched caller.

    Message text reaches a PBX administrator's request log, so it must never contain a full phone
    number — use :func:`mask_number`.
    """


def _now():
    return datetime.now(UTC)


def source_system(provider_slug: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "", str(provider_slug or "").strip().lower())
    if not slug:
        raise CallJournalError("A PBX slug is required for source identity.")
    return f"{SOURCE_SYSTEM_PREFIX}:{slug}"


def parse_direction(call_type) -> str:
    """Map a PBX call outcome onto this ledger's direction vocabulary.

    3CX reports ``Inbound`` / ``Outbound`` / ``Missed`` / ``Notanswered``; the first two are
    completed calls and the last two are refused. Comparison is case-folded because the exact
    casing is a vendor display choice, not a contract.
    """
    value = str(call_type or "").strip().lower()
    if value in COMPLETED_DIRECTIONS:
        return value
    if value in ("missed", "notanswered", "not answered", "unanswered"):
        raise CallJournalError(
            f"Call outcome {value!r} is not a completed call; this endpoint journals "
            f"{' and '.join(COMPLETED_DIRECTIONS)} calls only.")
    raise CallJournalError(f"Unrecognised call outcome {value or '<empty>'!r}.")


def parse_duration(value) -> int | None:
    """Seconds from a PBX duration. Accepts ``hh:mm:ss``, ``mm:ss``, or a plain second count.

    Returns ``None`` for an absent duration — a journal row without one is still a true record of
    the call — but REFUSES a malformed or absurd one rather than storing a number that would later
    be summed into a talk-time report nobody could reconcile.
    """
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    if raw.isdigit():
        seconds = int(raw)
    else:
        parts = raw.split(":")
        if len(parts) not in (2, 3) or not all(p.strip().isdigit() for p in parts):
            raise CallJournalError(f"Unrecognised call duration {raw!r}; expected hh:mm:ss.")
        numbers = [int(p) for p in parts]
        while len(numbers) < 3:
            numbers.insert(0, 0)
        hours, minutes, secs = numbers
        if minutes > 59 or secs > 59:
            raise CallJournalError(f"Unrecognised call duration {raw!r}; expected hh:mm:ss.")
        seconds = hours * 3600 + minutes * 60 + secs
    if seconds > MAX_DURATION_SECONDS:
        raise CallJournalError(f"Call duration {raw!r} exceeds the {MAX_DURATION_SECONDS}s ceiling.")
    return seconds


def parse_started_at(value) -> datetime:
    """The call's start instant, as an aware UTC datetime. Required — it is half of a derived
    identity, so a missing or unparseable one must fail loudly rather than default to "now" and
    make every retry look like a new call."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    raw = str(value or "").strip()
    if not raw:
        raise CallJournalError("The call event carries no start time, so it cannot be identified.")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CallJournalError(
            f"Unrecognised call start time {raw!r}; expected ISO 8601 (yyyy-MM-ddTHH:mm:ssZ).") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class CallEvent:
    """One completed call, already parsed and already anchored to a client.

    ``person_id`` is resolved by the CALLER, not here. Anchoring a call is the same authorization
    question as anchoring a text, and the caller is the layer that knows whether the number
    resolved to exactly one person — see :mod:`app.integrations.threecx.lookup`.
    """

    provider_slug: str
    direction: str
    counterparty_number: str
    started_at: datetime
    person_id: int
    agent: str | None = None
    duration_seconds: int | None = None
    household_id: int | None = None
    #: The PBX's OWN call id, when it has one. ``None`` is normal and expected: 3CX v20's
    #: ReportCall scenario exposes no such variable.
    call_id: str | None = None

    @property
    def identity_kind(self) -> str:
        return IDENTITY_VENDOR if self.call_id else IDENTITY_DERIVED

    @property
    def masked_number(self) -> str | None:
        return mask_number(self.counterparty_number)


def source_external_id(event: CallEvent) -> str:
    """The idempotency key for one call, paired with ``source_system`` by a UNIQUE constraint.

    A vendor call id is used verbatim when present — it is the strongest identity available and
    the one a future 3CX release, or a CDR-driven poster, would supply.

    Otherwise the key is a SHA-256 over the tuple that already identifies a call: its start
    instant to the second, the handling extension, the normalized counterparty number, and the
    direction. Re-reporting the SAME call renders the same tuple and so writes nothing; two
    genuinely different calls would have to share an agent, a counterparty, a direction and a
    start second to collide, which is the same call reported twice.

    It is HASHED rather than concatenated so the stored identifier — which appears in query
    output, exports and error text — does not itself contain a client's phone number.
    """
    if event.call_id:
        return f"{IDENTITY_VENDOR}:{event.call_id}"
    material = "|".join((
        event.started_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        str(event.agent or ""),
        normalize_phone(event.counterparty_number) or "",
        event.direction,
    ))
    return f"{IDENTITY_DERIVED}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _conversation_key(event: CallEvent) -> dict:
    """A call conversation is this client and this number on this PBX.

    Deliberately NOT keyed by agent: several staff call the same client, and one thread per
    advisor would fragment a single relationship across the feed.
    """
    return {"provider": SOURCE_SYSTEM_PREFIX, "provider_slug": event.provider_slug,
            "counterparty_number": normalize_phone(event.counterparty_number)}


def _find_conversation(conn, event: CallEvent, key: dict):
    """The existing call thread for this client and number, or ``None``.

    Scoped to the anchored person, so the scan is bounded by one client's call threads rather than
    by every phone conversation in the firm.
    """
    rows = conn.execute(select(conversations.c.id, conversations.c.conversation_metadata).where(
        conversations.c.channel == CHANNEL,
        conversations.c.person_id == event.person_id)).all()
    for conversation_id, metadata in rows:
        if (metadata or {}) == key:
            return conversation_id
    return None


def journal_call(conn, event: CallEvent) -> tuple[int, bool]:
    """Write the canonical rows for one completed call on the CALLER'S transaction.

    Returns ``(message_id, created)``. ``created`` is ``False`` when the call was already on file:
    a PBX retry, or the same journal request delivered twice, returns the existing id and writes
    no second row. Idempotency is enforced by the database's
    ``UNIQUE (source_system, source_external_id)``, not by this check alone — the lookup below is
    the fast path, and the constraint is what makes a concurrent duplicate impossible.
    """
    if event.direction not in COMPLETED_DIRECTIONS:
        raise CallJournalError(f"Refusing to journal a {event.direction!r} call.")
    number = normalize_phone(event.counterparty_number)
    if not number:
        raise CallJournalError("The call event carries no usable counterparty number.")

    system = source_system(event.provider_slug)
    external_id = source_external_id(event)

    existing = conn.execute(select(sources.c.message_id).where(
        sources.c.source_system == system,
        sources.c.source_external_id == external_id)).scalar()
    if existing is not None:
        conn.execute(sources.update().where(
            sources.c.source_system == system,
            sources.c.source_external_id == external_id).values(last_synced_at=_now()))
        return existing, False

    now = _now()
    started_at = event.started_at.astimezone(UTC)
    key = _conversation_key(event)
    masked = event.masked_number

    conversation_id = _find_conversation(conn, event, key)
    if conversation_id is None:
        conversation_id = conn.execute(conversations.insert().values(
            subject=f"Calls · {masked}", category="general", status="open", priority="normal",
            channel=CHANNEL, person_id=event.person_id, household_id=event.household_id,
            conversation_metadata=key, last_message_at=started_at,
            created_at=now, updated_at=now).returning(conversations.c.id)).scalar_one()
    else:
        # An existing thread keeps its FIRST anchor, as email and SMS do: re-anchoring on a later
        # call would silently move a thread's owner.
        conn.execute(conversations.update().where(conversations.c.id == conversation_id).values(
            last_message_at=started_at, updated_at=now))

    message_id = conn.execute(messages.insert().values(
        conversation_id=conversation_id, channel=CHANNEL, direction=event.direction,
        priority="normal", category="general",
        # Masked in the subject because a subject line is rendered in feeds, exports and
        # notification text — every surface a full number should not reach.
        subject=("Inbound call from " if event.direction == INBOUND else "Outbound call to ") + str(masked),
        # Always None. A call journal records THAT a call happened, never what was said.
        body=None,
        sender_type="external" if event.direction == INBOUND else "user",
        sender_ref=number if event.direction == INBOUND else (event.agent or None),
        status="delivered", sent_at=started_at, delivered_at=started_at,
        message_metadata={
            "pbx": event.provider_slug,
            "agent_extension": event.agent,
            "duration_seconds": event.duration_seconds,
            "started_at": started_at.isoformat(),
            # Which identity rule produced this row's dedup key. A reader auditing for duplicates
            # needs to know whether the PBX guaranteed uniqueness or this module inferred it.
            "identity_kind": event.identity_kind,
        },
        created_at=now, updated_at=now).returning(messages.c.id)).scalar_one()

    conn.execute(recipients_table.insert().values(
        message_id=message_id,
        recipient_type="person" if event.direction == OUTBOUND else "external",
        recipient_ref=str(event.person_id) if event.direction == OUTBOUND else number,
        recipient_role="to", display_name=masked,
        delivery_status="delivered", delivered_at=started_at, created_at=now))

    conn.execute(sources.insert().values(
        message_id=message_id, source_system=system, source_external_id=external_id,
        source_metadata={"pbx": event.provider_slug, "identity_kind": event.identity_kind,
                         "agent_extension": event.agent, "counterparty": masked},
        first_seen_at=now, last_synced_at=now))

    # The domain's own append-only ledger. NOT a timeline event — see the module docstring.
    conn.execute(events.insert().values(
        conversation_id=conversation_id, message_id=message_id, event_type="call_journaled",
        payload={"source_system": system, "direction": event.direction,
                 "identity_kind": event.identity_kind,
                 "duration_seconds": event.duration_seconds},
        occurred_at=now))
    return message_id, True
