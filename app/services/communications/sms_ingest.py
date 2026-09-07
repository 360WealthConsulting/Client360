"""Normalize one inbound SMS into canonical ``communication_*`` records — PROVIDER-NEUTRAL (Batch 5).

WHY THIS EXISTS WITH NO PROVIDER BEHIND IT. The Batch 5 audit found no SMS capability in this
repository at all: no vendor SDK, no credentials, no inbound webhook, no outbound transport. The one
thing carrying the word "sms" is ``NOTIFICATION_PROVIDERS["sms"]``, a ``DisabledNotificationHook``
that honestly answers ``provider_not_configured`` — a placeholder for an outbound *notification*,
not an SMS domain. Choosing a vendor is a procurement and compliance decision (10DLC/campaign
registration, number provisioning, per-message cost, data residency), not one to make inside a code
change, so this module deliberately stops at the vendor boundary.

What it does implement is the half that is the same whichever vendor is chosen: turning a delivered
SMS into the firm's canonical record of it. It takes an already-parsed payload and a connection;
it opens no socket, holds no credential, and names no vendor.

THE SHAPE IS ``email_ingest``'s, ON PURPOSE — same anchoring discipline, same database-enforced
identity, same refusal to guess — because a second, differently-shaped normalizer would be the
thing that eventually disagrees with the first about what a client conversation is.

WHERE SMS GENUINELY DIFFERS FROM EMAIL, and why that changes the code:

  * **A phone number is not a person.** Email addresses are effectively per-person; phone numbers
    are routinely shared — a couple on one mobile, a household landline. So the lookup maps one
    number to a LIST of people, and a number belonging to two people in different households is
    AMBIGUOUS and anchors nothing. Silently filing a text under whichever of two spouses the query
    happened to return first is the specific failure this guards against.

  * **There is no Message-ID and no thread id.** SMS carries neither. Identity is therefore the
    provider's own message id and nothing else: this module REFUSES to ingest a payload without one
    rather than fall back to a body/timestamp/number-pair hash, which would silently merge two
    identical texts a client genuinely sent twice. Threading is the (firm number, counterparty
    number) pair, which is what an SMS conversation actually is.

  * **Opt-out arrives as a message.** A client texting STOP is a compliance event delivered through
    the same channel as ordinary correspondence. This module DETECTS and records the keyword; it
    changes no preference or consent state. Enforcement belongs with the outbound slice that could
    actually violate it — and recording it now means those opt-outs are already on file, rather than
    discovered later in a payload nobody classified.

RETENTION follows ADR-074's inbound decision unchanged: a bounded preview, not full third-party
content. For SMS this rarely truncates at all — a standard segment is 160 characters — so it is a
deliberate alignment with the existing retention posture rather than a loss.

NO TIMELINE EVENT is written here, for the same reason ``email_ingest`` writes none: the D.44
registry classifies by ``(source, event_type)`` and a second row would double-count the exchange.
Whether SMS should have a timeline representation at all is a decision the ingestion caller makes
when it exists; this module deliberately does not make it on that caller's behalf.

NOT IN SCOPE: any vendor, webhook route, credential, outbound send, delivery-receipt ingestion,
quiet hours, or consent enforcement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: Provenance namespace. The provider slug is part of it so two vendors can never collide on a
#: message id, and a vendor change does not retroactively reinterpret old rows.
SOURCE_SYSTEM_PREFIX = "sms"

CHANNEL = "sms"
INBOUND, OUTBOUND = "inbound", "outbound"

#: Same bound ADR-074 set for inbound email. A standard SMS segment is 160 characters, so this
#: almost never truncates; it keeps one retention rule for all inbound third-party content.
PREVIEW_LIMIT = 500

#: Carrier-mandated keywords (US/CTIA). Detected and recorded, never acted on here.
STOP_KEYWORDS = frozenset({"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout",
                           "opt-out"})
START_KEYWORDS = frozenset({"start", "yes", "unstop", "optin", "opt-in"})
HELP_KEYWORDS = frozenset({"help", "info"})


class SmsIngestError(ValueError):
    """The payload cannot be ingested safely. Never raised for an ordinary unmatched message."""


def _now():
    return datetime.now(UTC)


def normalize_phone(value) -> str | None:
    """Digits only, with a leading US country code dropped — the repository's EXISTING convention.

    This is deliberately NOT E.164. ``people.normalized_phone`` is the only indexed client phone
    column in the schema and it is populated by the AssetMark, Schwab and Wealthbox importers, all
    three of which normalize exactly this way. Matching an inbound text against clients means
    agreeing with that column; a "better" E.164 normalizer here would simply match nothing.
    ``tests/test_sms_ingest.py`` pins the agreement with all three importers so the two cannot drift.

    A number that is not a 10-digit NANP number after stripping is returned as its digits, so an
    international sender is still recorded and compared consistently — it just will not match a
    client whose stored number was normalized under the same rule.
    """
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def classify_keyword(body) -> str | None:
    """``"stop"`` / ``"start"`` / ``"help"`` when the whole message is that keyword, else ``None``.

    Only a message that is EXACTLY the keyword counts. Carriers treat it that way, and a client
    writing "please stop sending me statements" is a service request for a human to read, not an
    opt-out to process automatically.
    """
    text = re.sub(r"[^a-z-]", "", str(body or "").strip().lower())
    if not text:
        return None
    if text in STOP_KEYWORDS:
        return "stop"
    if text in START_KEYWORDS:
        return "start"
    if text in HELP_KEYWORDS:
        return "help"
    return None


def source_system(provider: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "", str(provider or "").strip().lower())
    if not slug:
        raise SmsIngestError("An SMS provider slug is required for source identity.")
    return f"{SOURCE_SYSTEM_PREFIX}:{slug}"


def source_external_id(message: dict) -> str:
    """The provider's OWN message id. There is no fallback, and that is the point.

    Deduping on body, timestamp or the number pair would collapse two identical texts a client
    genuinely sent — "ok", twice — into one, losing correspondence. A provider that supplies no
    message id cannot be ingested idempotently, so it is refused loudly instead of quietly risking
    duplicates.
    """
    provider_id = str(message.get("provider_message_id") or "").strip()
    if not provider_id:
        raise SmsIngestError(
            "The SMS payload carries no provider message id, so it cannot be de-duplicated. "
            "Refusing to ingest rather than risk duplicating client correspondence.")
    return provider_id


@dataclass(frozen=True)
class SmsMatch:
    """How one SMS resolves against Client360 people."""

    direction: str
    firm_number: str | None
    counterparty_number: str | None
    person_id: int | None = None
    household_id: int | None = None
    matched_person_ids: frozenset = field(default_factory=frozenset)
    ambiguous: bool = False

    @property
    def anchored(self) -> bool:
        """Whether there is a client to file this text against. Ambiguity is never an anchor."""
        return not self.ambiguous and (self.person_id is not None or self.household_id is not None)


def resolve_match(message: dict, people_by_phone: dict, firm_numbers) -> SmsMatch:
    """Resolve an SMS against ``{normalized phone: [(person_id, household_id), ...]}``.

    ``firm_numbers`` are the numbers this firm sends from. A message FROM one of them is the firm's
    own outbound copy; anything else is inbound. Both sides are normalized before comparison, so a
    provider that reports ``+1 (555) 010-0000`` and a client record holding ``5550100000`` agree.
    """
    firm = {p for p in (normalize_phone(n) for n in (firm_numbers or ())) if p}
    from_number = normalize_phone(message.get("from"))
    to_number = normalize_phone(message.get("to"))

    direction = OUTBOUND if (from_number and from_number in firm) else INBOUND
    firm_number = from_number if direction == OUTBOUND else (to_number if to_number in firm else None)
    counterparty = to_number if direction == OUTBOUND else from_number

    # Candidates are the counterparty side only — the firm's own number is never a client anchor.
    candidates = list(people_by_phone.get(counterparty) or []) if counterparty else []
    by_person = {person_id: household_id for person_id, household_id in candidates}
    households = {h for h in by_person.values() if h is not None}

    person_id = household_id = None
    ambiguous = False
    if len(by_person) == 1:
        person_id, household_id = next(iter(by_person.items()))
    elif len(by_person) > 1:
        # One number, several people. A couple sharing a mobile is ONE household and can be filed
        # there; two people in different households sharing a number cannot be told apart, and
        # picking either would attribute a client's words to someone else.
        if len(households) == 1 and None not in by_person.values():
            household_id = next(iter(households))
        else:
            ambiguous = True

    return SmsMatch(direction=direction, firm_number=firm_number, counterparty_number=counterparty,
                    person_id=person_id, household_id=household_id,
                    matched_person_ids=frozenset(by_person), ambiguous=ambiguous)


def people_for_numbers(conn, numbers) -> dict:
    """``{normalized phone: [(person_id, household_id), ...]}`` for the numbers in one payload.

    Bounded by construction — it looks up only the numbers actually present, never the whole client
    base — and returns a LIST per number so a shared line is visible as shared rather than silently
    resolved to one of its owners.
    """
    from sqlalchemy import select

    from app.db import people

    wanted = {p for p in (normalize_phone(n) for n in (numbers or ())) if p}
    if not wanted:
        return {}
    out: dict[str, list] = {}
    for person_id, household_id, phone in conn.execute(select(
            people.c.id, people.c.household_id, people.c.normalized_phone).where(
            people.c.normalized_phone.in_(tuple(wanted)))).all():
        out.setdefault(phone, []).append((person_id, household_id))
    return out


def _preview(body) -> str | None:
    text = (str(body or "")).strip()
    if not text:
        return None
    return text if len(text) <= PREVIEW_LIMIT else text[: PREVIEW_LIMIT - 3] + "..."


def _conversation_key(match: SmsMatch, provider: str) -> dict:
    """An SMS conversation IS the pair of numbers — there is no thread id to carry one."""
    return {"provider": SOURCE_SYSTEM_PREFIX, "provider_slug": provider,
            "firm_number": match.firm_number, "counterparty_number": match.counterparty_number}


def _find_conversation(conn, key: dict):
    from sqlalchemy import select

    from app.db import communication_conversations as conversations

    if not key.get("counterparty_number"):
        return None
    rows = conn.execute(select(conversations.c.id, conversations.c.conversation_metadata).where(
        conversations.c.channel == CHANNEL)).all()
    for conversation_id, metadata in rows:
        if (metadata or {}) == key:
            return conversation_id
    return None


def normalize_sms(conn, *, provider: str, message: dict, match: SmsMatch) -> int | None:
    """Write the canonical rows for one SMS on the CALLER'S transaction. Returns the message id.

    Returns ``None`` when the text has no client anchor: an unanchored or ambiguous message belongs
    in a review queue, not in a conversation nobody owns. Idempotent — a provider retry, or the same
    webhook delivered twice, returns the existing id and writes nothing.
    """
    from sqlalchemy import select

    from app.db import communication_conversations as conversations
    from app.db import communication_events as events
    from app.db import communication_message_sources as sources
    from app.db import communication_messages as messages
    from app.db import communication_recipients as recipients_table

    system = source_system(provider)
    external_id = source_external_id(message)        # raises rather than dedupe on content

    existing = conn.execute(select(sources.c.message_id).where(
        sources.c.source_system == system,
        sources.c.source_external_id == external_id)).scalar()
    if existing is not None:
        conn.execute(sources.update().where(
            sources.c.source_system == system,
            sources.c.source_external_id == external_id).values(last_synced_at=_now()))
        return existing                              # already ingested — a retry writes nothing

    if not match.anchored:
        return None

    now = _now()
    received_at = _parse_datetime(message.get("received_at"))
    keyword = classify_keyword(message.get("body"))
    key = _conversation_key(match, provider)

    conversation_id = _find_conversation(conn, key)
    if conversation_id is None:
        conversation_id = conn.execute(conversations.insert().values(
            subject=f"SMS · {match.counterparty_number}", category="general", status="open",
            priority="normal", channel=CHANNEL,
            person_id=match.person_id, household_id=match.household_id,
            conversation_metadata=key, last_message_at=received_at,
            created_at=now, updated_at=now).returning(conversations.c.id)).scalar_one()
    else:
        # An existing conversation keeps its FIRST anchor, as email does: re-anchoring on a later
        # message would silently move a thread's owner.
        conn.execute(conversations.update().where(conversations.c.id == conversation_id).values(
            last_message_at=received_at, updated_at=now))

    message_id = conn.execute(messages.insert().values(
        conversation_id=conversation_id, channel=CHANNEL, direction=match.direction,
        priority="normal", category="general",
        subject=f"SMS from {match.counterparty_number}" if match.direction == INBOUND
        else f"SMS to {match.counterparty_number}",
        body=_preview(message.get("body")),
        sender_type="external" if match.direction == INBOUND else "user",
        sender_ref=normalize_phone(message.get("from")),
        status="delivered", sent_at=received_at, delivered_at=received_at,
        message_metadata={
            "provider": provider,
            # Detected, NOT enforced. The outbound slice that could violate an opt-out is the one
            # that must honour it; recording it here means the history is already on file.
            "compliance_keyword": keyword,
            # Provider-reported transport state, kept as provider truth. No communication_deliveries
            # row is written: that ledger records OUR send intent's lifecycle (ADR-075), and inbound
            # SMS has no intent of ours to record.
            "provider_status": message.get("status"),
            "provider_error": message.get("error_code"),
            "segments": message.get("segments"),
        },
        created_at=now, updated_at=now).returning(messages.c.id)).scalar_one()

    recipient_number = normalize_phone(message.get("to"))
    if recipient_number:
        is_client = match.direction == OUTBOUND and match.person_id is not None
        conn.execute(recipients_table.insert().values(
            message_id=message_id,
            recipient_type="person" if is_client else "external",
            recipient_ref=str(match.person_id) if is_client else recipient_number,
            recipient_role="to", display_name=recipient_number,
            delivery_status="delivered", delivered_at=received_at, created_at=now))

    conn.execute(sources.insert().values(
        message_id=message_id, source_system=system, source_external_id=external_id,
        source_metadata={"provider": provider,
                         "from": normalize_phone(message.get("from")),
                         "to": recipient_number,
                         "provider_status": message.get("status")},
        first_seen_at=now, last_synced_at=now))

    # The domain's own append-only ledger. NOT a timeline event — see the module docstring.
    conn.execute(events.insert().values(
        conversation_id=conversation_id, message_id=message_id, event_type="message_ingested",
        payload={"source_system": system, "direction": match.direction,
                 "compliance_keyword": keyword,
                 "matched_person_ids": sorted(match.matched_person_ids)},
        occurred_at=now))
    return message_id


def _parse_datetime(value):
    if not value:
        return _now()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
