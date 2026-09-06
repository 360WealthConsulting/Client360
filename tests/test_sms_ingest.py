"""Provider-neutral inbound SMS normalization (Batch 5).

The Batch 5 audit found no SMS capability in the repository: no vendor, no credentials, no inbound
webhook, no outbound transport. So this suite covers the vendor-INDEPENDENT half only — turning an
already-parsed SMS payload into the firm's canonical record of it — and several tests exist
specifically to prove the vendor boundary was not crossed.

The three things an SMS normalizer gets wrong, and what pins them here:

  * **Duplicates.** A provider webhook is retried; the same text arrives twice. Identity is the
    provider's message id and NOTHING else, enforced by ``UNIQUE (source_system,
    source_external_id)``. A payload without one is refused rather than deduped on body or
    timestamp — because a client who genuinely texts "ok" twice must not have one of them erased.

  * **Wrong client.** Unlike an email address, a phone number is routinely shared. A number owned by
    two people in different households anchors NOTHING; one shared by a couple anchors their
    household.

  * **A normalizer that matches nothing.** ``people.normalized_phone`` is the only indexed client
    phone column, populated by three importers with one specific convention. A test compares this
    module's normalizer against all three so the two cannot drift apart.

No network call of any kind is made: every payload is a literal dict, and no vendor SDK exists to
mock.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import (
    communication_conversations,
    communication_events,
    communication_message_sources,
    communication_messages,
    communication_recipients,
    engine,
    households,
    people,
    timeline_events,
)
from app.services.communications import sms_ingest
from app.services.communications.sms_ingest import (
    INBOUND,
    OUTBOUND,
    SmsIngestError,
    classify_keyword,
    normalize_phone,
    normalize_sms,
    people_for_numbers,
    resolve_match,
)

PROVIDER = "acme"
FIRM_NUMBER = "5550100000"

_SEEN_PEOPLE: set = set()
_SEEN_HOUSEHOLDS: set = set()
_SEEN_CONVERSATIONS: set = set()


@pytest.fixture(autouse=True)
def _cleanup():
    """Leave the database as this suite found it.

    ``communication_events`` is append-only and RESTRICT-anchors its conversations, so conversations
    survive with their client anchors detached — the same discipline the Batch 4a/4c/4d suites use.
    Everything else is removed.
    """
    yield
    with engine.begin() as c:
        if _SEEN_CONVERSATIONS:
            ids = list(_SEEN_CONVERSATIONS)
            c.execute(communication_messages.delete().where(
                communication_messages.c.conversation_id.in_(ids)))
            # The metadata is cleared along with the anchors. An SMS conversation is keyed by its
            # number pair, so a surviving-but-detached conversation would be re-adopted by the next
            # test that happens to use the same number — the tests would then share a conversation
            # whose anchor had already been stripped.
            c.execute(communication_conversations.update().where(
                communication_conversations.c.id.in_(ids)).values(
                person_id=None, household_id=None, conversation_metadata={}))
        if _SEEN_PEOPLE:
            ids = list(_SEEN_PEOPLE)
            c.execute(timeline_events.delete().where(timeline_events.c.person_id.in_(ids)))
            c.execute(people.delete().where(people.c.id.in_(ids)))
        if _SEEN_HOUSEHOLDS:
            c.execute(households.delete().where(
                households.c.id.in_(list(_SEEN_HOUSEHOLDS))))
    for seen in (_SEEN_PEOPLE, _SEEN_HOUSEHOLDS, _SEEN_CONVERSATIONS):
        seen.clear()


def _household(name="SMS Household"):
    with engine.begin() as c:
        hid = c.execute(insert(households).values(
            name=f"{name} {uuid.uuid4().hex[:8]}").returning(households.c.id)).scalar_one()
    _SEEN_HOUSEHOLDS.add(hid)
    return hid


def _person(phone, household_id=None, name="Ada Client"):
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        pid = c.execute(insert(people).values(
            full_name=f"{name} {tag}", primary_phone=phone,
            normalized_phone=normalize_phone(phone), active=True,
            household_id=household_id).returning(people.c.id)).scalar_one()
    _SEEN_PEOPLE.add(pid)
    return pid


def _payload(*, body="Can you confirm my balance?", from_number=None, to_number=None,
             provider_message_id=None, **extra):
    return {"provider_message_id": provider_message_id or f"SM{uuid.uuid4().hex}",
            "from": from_number or "+1 (555) 020-0000", "to": to_number or FIRM_NUMBER,
            "body": body, "received_at": "2026-09-04T14:00:00Z", **extra}


def _match(payload, people_by_phone):
    return resolve_match(payload, people_by_phone, [FIRM_NUMBER])


def _ingest(payload, people_by_phone, provider=PROVIDER):
    match = _match(payload, people_by_phone)
    with engine.begin() as c:
        message_id = normalize_sms(c, provider=provider, message=payload, match=match)
    if message_id is not None:
        with engine.connect() as c:
            _SEEN_CONVERSATIONS.add(c.execute(select(
                communication_messages.c.conversation_id).where(
                communication_messages.c.id == message_id)).scalar_one())
    return message_id


def _message(message_id):
    with engine.connect() as c:
        return c.execute(select(communication_messages).where(
            communication_messages.c.id == message_id)).mappings().one()


# ============================ THE VENDOR BOUNDARY ============================

def test_no_sms_vendor_is_referenced_anywhere_in_the_repository():
    """Batch 5 stopped at the vendor boundary deliberately: picking one is a procurement and
    compliance decision (10DLC registration, number provisioning, cost, data residency), not a code
    change. If this ever fails, a vendor was integrated without that decision being recorded."""
    import pathlib

    vendors = ("twilio", "telnyx", "bandwidth.com", "messagebird", "plivo", "vonage", "nexmo")
    roots = [pathlib.Path("app"), pathlib.Path("migrations")]
    hits = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            hits += [f"{path}:{v}" for v in vendors if v in text]
    assert hits == [], f"an SMS vendor appears in the codebase: {hits}"


def test_the_module_opens_no_network_connection_and_holds_no_credential():
    import ast
    import pathlib

    source = pathlib.Path("app/services/communications/sms_ingest.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {n.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for n in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert "requests" not in imported and "httpx" not in imported and "socket" not in imported
    assert "os" not in imported, "no environment credential lookup belongs in the domain layer"


def test_the_outbound_sms_notification_hook_is_still_honestly_disabled():
    """The pre-existing placeholder is untouched: it reports provider_not_configured rather than
    pretending a send happened. Nothing in this batch turns it on."""
    from app.portal.providers import NOTIFICATION_PROVIDERS

    result = NOTIFICATION_PROVIDERS["sms"].deliver(
        recipient="x", title="t", body="b", metadata={})
    assert result["delivered"] is False
    assert result["reason"] == "provider_not_configured"


# ============================ PHONE NORMALIZATION ============================

def test_the_normalizer_agrees_with_every_importer_that_populates_normalized_phone():
    """``people.normalized_phone`` is the only indexed client phone column and three importers fill
    it. Matching an inbound text against clients means agreeing with them exactly — a "better"
    E.164 normalizer here would simply match nothing."""
    from app.importers.assetmark import normalize_phone as assetmark
    from app.importers.schwab import normalize_phone as schwab
    from app.importers.wealthbox import normalize_phone as wealthbox

    samples = ["+1 (555) 020-0000", "555-020-0000", "15550200000", "5550200000",
               "(555) 020 0000", " 555.020.0000 ", "", None, "+44 20 7946 0958"]
    for sample in samples:
        mine = normalize_phone(sample)
        assert mine == assetmark(sample) == schwab(sample) == wealthbox(sample), \
            f"drifted from the importers on {sample!r}"


def test_normalization_strips_formatting_and_the_us_country_code():
    assert normalize_phone("+1 (555) 020-0000") == "5550200000"
    assert normalize_phone("555.020.0000") == "5550200000"
    assert normalize_phone(None) is None
    assert normalize_phone("") is None


def test_a_provider_format_and_a_stored_format_resolve_to_the_same_number():
    """The whole point of normalizing on both sides."""
    hid = _household()
    pid = _person("(555) 020-0000", hid)
    with engine.connect() as c:
        found = people_for_numbers(c, ["+15550200000"])
    assert found == {"5550200000": [(pid, hid)]}


def test_the_number_lookup_only_asks_for_the_numbers_in_the_payload():
    """Bounded by construction — it never loads the client base to match one text."""
    _person("(555) 020-1111", _household())
    with engine.connect() as c:
        assert people_for_numbers(c, []) == {}
        assert people_for_numbers(c, ["5559999999"]) == {}


# ============================ IDENTITY + IDEMPOTENCY ============================

def test_a_duplicate_provider_webhook_creates_one_canonical_message():
    hid = _household()
    pid = _person("555-020-0000", hid)
    payload = _payload()
    people_map = {"5550200000": [(pid, hid)]}

    first = _ingest(payload, people_map)
    second = _ingest(payload, people_map)
    assert first == second
    with engine.connect() as c:
        count = c.execute(select(func.count()).select_from(communication_messages).where(
            communication_messages.c.conversation_id.in_(list(_SEEN_CONVERSATIONS)))).scalar()
    assert count == 1


def test_a_provider_retry_writes_nothing_new():
    hid = _household()
    pid = _person("555-020-0000", hid)
    payload = _payload()
    people_map = {"5550200000": [(pid, hid)]}
    message_id = _ingest(payload, people_map)

    with engine.connect() as c:
        events_before = c.execute(select(func.count()).select_from(communication_events).where(
            communication_events.c.message_id == message_id)).scalar()
    _ingest(payload, people_map)
    with engine.connect() as c:
        events_after = c.execute(select(func.count()).select_from(communication_events).where(
            communication_events.c.message_id == message_id)).scalar()
        sources = c.execute(select(func.count()).select_from(
            communication_message_sources).where(
            communication_message_sources.c.message_id == message_id)).scalar()
    assert events_after == events_before == 1
    assert sources == 1


def test_identity_is_the_provider_message_id():
    hid = _household()
    pid = _person("555-020-0000", hid)
    message_id = _ingest(_payload(provider_message_id="SMabc123"), {"5550200000": [(pid, hid)]})
    with engine.connect() as c:
        row = c.execute(select(communication_message_sources).where(
            communication_message_sources.c.message_id == message_id)).mappings().one()
    assert row["source_external_id"] == "SMabc123"
    assert row["source_system"] == f"sms:{PROVIDER}"


def test_the_provider_slug_is_part_of_the_identity_namespace():
    """Two vendors must never collide on a message id, and a vendor change must not retroactively
    reinterpret rows written under the old one."""
    hid = _household()
    pid = _person("555-020-0000", hid)
    people_map = {"5550200000": [(pid, hid)]}
    a = _ingest(_payload(provider_message_id="SHARED-ID"), people_map, provider="acme")
    b = _ingest(_payload(provider_message_id="SHARED-ID"), people_map, provider="other")
    assert a != b, "the same id from two providers is two different messages"


def test_a_payload_without_a_provider_message_id_is_refused():
    """Refused loudly rather than deduped on content — see the module docstring."""
    hid = _household()
    pid = _person("555-020-0000", hid)
    payload = _payload()
    payload.pop("provider_message_id")
    with pytest.raises(SmsIngestError):
        _ingest(payload, {"5550200000": [(pid, hid)]})


def test_two_identical_texts_with_different_ids_are_two_messages():
    """The failure that content-based deduping would cause: a client texting "ok" twice."""
    hid = _household()
    pid = _person("555-020-0000", hid)
    people_map = {"5550200000": [(pid, hid)]}
    first = _ingest(_payload(body="ok"), people_map)
    second = _ingest(_payload(body="ok"), people_map)
    assert first != second


def test_identity_never_uses_the_number_pair_or_the_timestamp():
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(
        "app/services/communications/sms_ingest.py").read_text(encoding="utf-8"))
    function = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "source_external_id")
    body = ast.unparse(function)
    assert "provider_message_id" in body
    for forbidden in ("received_at", "sha256", "hash("):
        assert forbidden not in body


# ============================ MATCHING ============================

def test_one_matched_person_anchors_the_person_and_their_household():
    hid = _household()
    pid = _person("555-020-0000", hid)
    match = _match(_payload(), {"5550200000": [(pid, hid)]})
    assert match.anchored is True
    assert match.person_id == pid and match.household_id == hid


def test_a_number_shared_by_one_household_anchors_the_household():
    """A couple on one mobile is still one family, and the text belongs to that family."""
    hid = _household()
    a, b = _person("555-020-0000", hid, "Ada"), _person("555-020-0000", hid, "Grace")
    match = _match(_payload(), {"5550200000": [(a, hid), (b, hid)]})
    assert match.anchored is True
    assert match.person_id is None and match.household_id == hid


def test_a_number_shared_across_households_anchors_nothing():
    """THE guard this channel needs. Filing a text under whichever owner the query returned first
    would attribute one client's words to another."""
    a_hid, b_hid = _household("A"), _household("B")
    a, b = _person("555-020-0000", a_hid, "Ada"), _person("555-020-0000", b_hid, "Bob")
    match = _match(_payload(), {"5550200000": [(a, a_hid), (b, b_hid)]})
    assert match.ambiguous is True
    assert match.anchored is False
    assert match.person_id is None and match.household_id is None


def test_an_ambiguous_number_writes_no_communication_record():
    a_hid, b_hid = _household("A"), _household("B")
    a, b = _person("555-020-0000", a_hid), _person("555-020-0000", b_hid)
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(communication_messages)).scalar()
    assert _ingest(_payload(), {"5550200000": [(a, a_hid), (b, b_hid)]}) is None
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(communication_messages)).scalar() == before


def test_an_unknown_number_writes_no_communication_record():
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(communication_messages)).scalar()
    assert _ingest(_payload(), {}) is None
    with engine.connect() as c:
        assert c.execute(select(func.count()).select_from(communication_messages)).scalar() == before


def test_a_person_with_no_household_still_anchors_to_themselves():
    pid = _person("555-020-0000", None)
    match = _match(_payload(), {"5550200000": [(pid, None)]})
    assert match.anchored is True and match.person_id == pid


# ============================ DIRECTION ============================

def test_a_message_from_a_client_is_inbound():
    hid = _household()
    pid = _person("555-020-0000", hid)
    match = _match(_payload(), {"5550200000": [(pid, hid)]})
    assert match.direction == INBOUND
    assert match.counterparty_number == "5550200000"
    assert match.firm_number == FIRM_NUMBER


def test_a_message_from_the_firms_own_number_is_outbound():
    hid = _household()
    pid = _person("555-020-0000", hid)
    payload = _payload(from_number=FIRM_NUMBER, to_number="555-020-0000")
    match = _match(payload, {"5550200000": [(pid, hid)]})
    assert match.direction == OUTBOUND
    assert match.counterparty_number == "5550200000"
    assert match.person_id == pid


def test_direction_is_recorded_on_the_canonical_message():
    hid = _household()
    pid = _person("555-020-0000", hid)
    people_map = {"5550200000": [(pid, hid)]}
    inbound = _message(_ingest(_payload(), people_map))
    outbound = _message(_ingest(
        _payload(from_number=FIRM_NUMBER, to_number="555-020-0000"), people_map))
    assert inbound["direction"] == INBOUND and inbound["sender_type"] == "external"
    assert outbound["direction"] == OUTBOUND and outbound["sender_type"] == "user"


def test_the_firms_number_is_never_the_client_anchor():
    """Even when the firm's own number happens to exist as a person record."""
    hid = _household()
    firm_person = _person(FIRM_NUMBER, hid)
    client = _person("555-020-0000", hid)
    match = _match(_payload(), {FIRM_NUMBER: [(firm_person, hid)],
                                "5550200000": [(client, hid)]})
    assert match.person_id == client


# ============================ THE CANONICAL RECORD ============================

def test_the_message_lands_on_the_sms_channel():
    hid = _household()
    pid = _person("555-020-0000", hid)
    row = _message(_ingest(_payload(), {"5550200000": [(pid, hid)]}))
    assert row["channel"] == "sms"
    assert row["sender_ref"] == "5550200000"


def test_the_conversation_is_the_pair_of_numbers():
    """SMS carries no thread id; the number pair is what an SMS conversation actually is."""
    hid = _household()
    pid = _person("555-020-0000", hid)
    people_map = {"5550200000": [(pid, hid)]}
    first = _ingest(_payload(body="one"), people_map)
    second = _ingest(_payload(body="two"), people_map)
    assert _message(first)["conversation_id"] == _message(second)["conversation_id"]
    with engine.connect() as c:
        metadata = c.execute(select(communication_conversations.c.conversation_metadata).where(
            communication_conversations.c.id == _message(first)["conversation_id"])).scalar()
    assert metadata["counterparty_number"] == "5550200000"
    assert metadata["firm_number"] == FIRM_NUMBER
    assert metadata["provider_slug"] == PROVIDER


def test_a_different_client_gets_a_different_conversation():
    a_hid, b_hid = _household("A"), _household("B")
    a, b = _person("555-020-0000", a_hid), _person("555-020-1111", b_hid)
    first = _ingest(_payload(), {"5550200000": [(a, a_hid)]})
    second = _ingest(_payload(from_number="555-020-1111"), {"5550201111": [(b, b_hid)]})
    assert _message(first)["conversation_id"] != _message(second)["conversation_id"]


def test_an_existing_conversation_keeps_its_first_anchor():
    hid = _household()
    pid = _person("555-020-0000", hid)
    people_map = {"5550200000": [(pid, hid)]}
    first = _ingest(_payload(), people_map)
    conversation_id = _message(first)["conversation_id"]
    other_hid = _household("Other")
    other = _person("555-020-9999", other_hid)
    # A later message on the SAME number pair, resolving to someone else, must not re-anchor.
    _ingest(_payload(), {"5550200000": [(other, other_hid)]})
    with engine.connect() as c:
        row = c.execute(select(communication_conversations).where(
            communication_conversations.c.id == conversation_id)).mappings().one()
    assert row["person_id"] == pid


def test_the_recipient_is_recorded_as_the_receiving_number():
    hid = _household()
    pid = _person("555-020-0000", hid)
    message_id = _ingest(_payload(), {"5550200000": [(pid, hid)]})
    with engine.connect() as c:
        row = c.execute(select(communication_recipients).where(
            communication_recipients.c.message_id == message_id)).mappings().one()
    assert row["recipient_ref"] == FIRM_NUMBER
    assert row["recipient_type"] == "external"


def test_the_body_follows_the_inbound_retention_bound():
    """The same 500-character bound ADR-074 set for inbound email. A standard segment is 160
    characters, so this almost never truncates — it keeps ONE retention rule for inbound
    third-party content rather than a second one for SMS."""
    hid = _household()
    pid = _person("555-020-0000", hid)
    short = _message(_ingest(_payload(body="Short and complete."), {"5550200000": [(pid, hid)]}))
    assert short["body"] == "Short and complete."
    long_body = "x" * 900
    long_row = _message(_ingest(_payload(body=long_body), {"5550200000": [(pid, hid)]}))
    assert len(long_row["body"]) == sms_ingest.PREVIEW_LIMIT
    assert long_row["body"].endswith("...")


def test_provider_transport_state_is_kept_as_provider_truth_not_as_a_delivery_row():
    """``communication_deliveries`` records OUR send intent's lifecycle (ADR-075). Inbound SMS has
    no intent of ours, so a delivery row there would assert something we never did."""
    from app.db import communication_deliveries

    hid = _household()
    pid = _person("555-020-0000", hid)
    message_id = _ingest(_payload(status="received", error_code=None, segments=1),
                         {"5550200000": [(pid, hid)]})
    row = _message(message_id)
    assert row["message_metadata"]["provider_status"] == "received"
    assert row["message_metadata"]["segments"] == 1
    with engine.connect() as c:
        deliveries = c.execute(select(func.count()).select_from(communication_deliveries).where(
            communication_deliveries.c.message_id == message_id)).scalar()
    assert deliveries == 0


def test_the_domain_ledger_records_the_ingest():
    hid = _household()
    pid = _person("555-020-0000", hid)
    message_id = _ingest(_payload(), {"5550200000": [(pid, hid)]})
    with engine.connect() as c:
        row = c.execute(select(communication_events).where(
            communication_events.c.message_id == message_id)).mappings().one()
    assert row["event_type"] == "message_ingested"
    assert row["payload"]["source_system"] == f"sms:{PROVIDER}"


# ============================ COMPLIANCE KEYWORDS ============================

def test_stop_is_detected_and_recorded():
    """Detected, NOT enforced: the outbound slice that could violate an opt-out is the one that must
    honour it. Recording it now means the opt-out is already on file when that slice exists."""
    hid = _household()
    pid = _person("555-020-0000", hid)
    row = _message(_ingest(_payload(body="STOP"), {"5550200000": [(pid, hid)]}))
    assert row["message_metadata"]["compliance_keyword"] == "stop"


@pytest.mark.parametrize("body,expected", [
    ("STOP", "stop"), ("stop", "stop"), (" Stop ", "stop"), ("UNSUBSCRIBE", "stop"),
    ("CANCEL", "stop"), ("START", "start"), ("Yes", "start"), ("HELP", "help"),
    ("please stop sending me statements", None),
    ("Can you stop the transfer?", None), ("", None), (None, None),
])
def test_only_a_bare_keyword_counts_as_a_compliance_keyword(body, expected):
    """Carriers treat only the exact keyword as an opt-out. A sentence containing "stop" is a
    service request for a human to read, not something to process automatically."""
    assert classify_keyword(body) == expected


def test_detecting_stop_changes_no_preference_or_consent_state():
    """This module records; it does not enforce. Enforcing here would put compliance policy in an
    ingestion path with no way to send anything."""
    from app.db import metadata

    hid = _household()
    pid = _person("555-020-0000", hid)
    preferences = metadata.tables["notification_preferences"]
    consents = metadata.tables["notification_consents"]
    with engine.connect() as c:
        before = (c.execute(select(func.count()).select_from(preferences)).scalar(),
                  c.execute(select(func.count()).select_from(consents)).scalar())
    _ingest(_payload(body="STOP"), {"5550200000": [(pid, hid)]})
    with engine.connect() as c:
        after = (c.execute(select(func.count()).select_from(preferences)).scalar(),
                 c.execute(select(func.count()).select_from(consents)).scalar())
    assert after == before


def test_the_existing_consent_layer_already_covers_sms_as_a_channel():
    """The gap is phone-level opt-out and STOP handling, not consent evaluation: the F5.3 decision
    layer already treats sms as consent-required."""
    from app.services.notification_preferences import CONSENT_REQUIRED_CHANNELS

    assert "sms" in CONSENT_REQUIRED_CHANNELS


# ============================ NO MIGRATION, NO REGRESSION ============================

def test_the_sms_channel_was_already_permitted_by_the_schema():
    """No migration: ``channel = 'sms'`` has been in the CHECK constraint since the communications
    tables were created."""
    from sqlalchemy import text

    from app.database.communication_tables import COMMUNICATION_CHANNELS

    assert "sms" in COMMUNICATION_CHANNELS
    with engine.connect() as c:
        definition = c.execute(text(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'ck_comm_message_channel'")).scalar()
    assert definition is not None and "'sms'" in definition


def test_the_migration_head_is_unchanged():
    """This batch adds no migration. If it ever does, this is where the claim breaks."""
    import pathlib

    heads = []
    for path in pathlib.Path("migrations/versions").glob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if 'down_revision = "emailnorm01"' in text:
            heads.append(path.name)
    assert heads == [], f"something now sits downstream of emailnorm01: {heads}"


def test_sms_writes_no_timeline_event():
    """ADR-049 composition is preserved: normalizing an SMS adds no relationship-timeline row, for
    the same reason email normalization adds none — the D.44 registry would double-count it."""
    hid = _household()
    pid = _person("555-020-0000", hid)
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == pid)).scalar()
    _ingest(_payload(), {"5550200000": [(pid, hid)]})
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(timeline_events).where(
            timeline_events.c.person_id == pid)).scalar()
    assert after == before


def test_the_client_communications_feed_is_unchanged_by_this_batch():
    """Batch 4d asserted the UI implies no SMS channel. With no ingestion path in production, an SMS
    filter would advertise a channel that cannot receive anything, so the surfaces are untouched."""
    from app.services.communications.engagement import feed

    assert set(feed.CHANNEL_LABELS) == {"secure_message", "email"}


def test_the_communications_inbox_is_unchanged_by_this_batch():
    from app.services.communications import inbox

    assert inbox.FILTERS == ("all", "mine", "unassigned", "attention", "unread", "resolved")


def test_email_normalization_is_untouched():
    from app.services.communications import email_ingest

    assert email_ingest.SOURCE_SYSTEM == "microsoft_graph"
    assert email_ingest.PREVIEW_LIMIT == 500
