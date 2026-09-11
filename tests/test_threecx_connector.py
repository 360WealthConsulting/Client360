"""The 3CX Phone System v20 custom-CRM connector.

Four things a telephony connector gets wrong, and what pins each of them here:

  * **It pops the wrong client.** A screen-pop is acted on before anyone speaks, so a wrong answer
    discloses one client's identity to whoever actually rang. Exactness is pinned from both sides:
    formatting variants of the SAME number must match, and a near-miss number, a substring, and an
    inactive record must NOT. A number held by two people returns nothing at all.

  * **It duplicates calls.** 3CX v20's call-journaling scenario exposes no call id (3CX support:
    available from the CDR, not from ReportCall), so identity has to be derived. The suite pins
    that a re-reported call writes nothing, that a genuinely different call does write, and that a
    vendor call id takes precedence the moment one is supplied.

  * **It leaks phone numbers into places that outlive the call.** Subjects, audit metadata,
    message metadata, source metadata and log lines are all asserted to carry the masked form
    only, and the message body is asserted to stay empty — a call journal records THAT a call
    happened, never what was said.

  * **Its template drifts from its endpoints.** The XML 3CX executes names the URLs and the JSON
    paths. The checked-in operator copy is asserted byte-identical to what the renderer produces
    from the live route constants, so a renamed field cannot ship as a silent absence of
    screen-pops.

No network call of any kind is made, to 3CX or anywhere else: the connector is the SERVER half and
never dials out. Every payload here is a literal dict.
"""
from __future__ import annotations

import pathlib
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from app.db import (
    audit_events,
    communication_conversations,
    communication_events,
    communication_message_sources,
    communication_messages,
    communication_recipients,
    engine,
    households,
    people,
)
from app.integrations.threecx import config as threecx_config
from app.integrations.threecx.template import ARTEFACT_PATH, render
from app.routes.threecx import JOURNAL_PATH, LOOKUP_PATH
from app.services.communications import call_journal, sms_ingest
from app.services.communications.phone_numbers import dial_uri, mask_number, normalize_phone

SECRET = "k" * 40                     # comfortably over MIN_SECRET_LENGTH
BASE_URL = "https://client360.example.com"
CLIENT_NUMBER = "5550100000"
#: Deliberately in the PAST. ``communication_events`` is immutable and RESTRICT-anchors its
#: conversations, so a call conversation created here survives the suite with its anchor detached.
#: A future-dated call would sort those survivors above every genuinely new conversation, and
#: ``communications.list_conversations`` pages at 50 — which quietly pushed a neighbouring suite's
#: freshly created conversation off its first page.
STARTED_AT = "2026-03-04T14:03:00Z"
LATER_SAME_DAY = "2026-03-04T16:20:00Z"
#: Where cleanup parks the conversations it cannot delete, so they sort below everything real.
SUNK_AT = datetime(1990, 1, 1, tzinfo=UTC)

_SEEN_PEOPLE: set = set()
_SEEN_HOUSEHOLDS: set = set()
_SEEN_CONVERSATIONS: set = set()


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    """The connector ON with a usable secret — the posture most tests need.

    Set per test rather than in the environment, so the OFF-by-default posture stays the real
    default and the tests that assert it can simply unset these.
    """
    monkeypatch.setenv("CLIENT360_3CX_ENABLED", "true")
    monkeypatch.setenv("CLIENT360_3CX_INTEGRATION_SECRET", SECRET)
    monkeypatch.setenv("CLIENT360_3CX_INSTANCE", "testpbx")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE_URL)


@pytest.fixture(autouse=True)
def _cleanup():
    """Leave the database as this suite found it.

    ``communication_events`` is append-only and RESTRICT-anchors its conversations, so
    conversations survive with their anchors and metadata detached — the same discipline
    ``tests/test_sms_ingest.py`` uses. Clearing the metadata matters here too: a call conversation
    is keyed by its counterparty number, so a surviving-but-detached row would be re-adopted by
    the next test that happens to use the same number.
    """
    yield
    with engine.begin() as c:
        if _SEEN_CONVERSATIONS:
            ids = list(_SEEN_CONVERSATIONS)
            c.execute(communication_messages.delete().where(
                communication_messages.c.conversation_id.in_(ids)))
            c.execute(communication_conversations.update().where(
                communication_conversations.c.id.in_(ids)).values(
                person_id=None, household_id=None, conversation_metadata={},
                # Sink the survivors. They are ordered by last_message_at in every conversation
                # listing, and a detached test row near the top of page 1 is a defect in whichever
                # suite runs next, not in this one.
                last_message_at=SUNK_AT))
        if _SEEN_PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(list(_SEEN_PEOPLE))))
        if _SEEN_HOUSEHOLDS:
            c.execute(households.delete().where(households.c.id.in_(list(_SEEN_HOUSEHOLDS))))
    for seen in (_SEEN_PEOPLE, _SEEN_HOUSEHOLDS, _SEEN_CONVERSATIONS):
        seen.clear()


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


# ------------------------------------------------------------------ helpers

def _household(name="3CX Household"):
    with engine.begin() as c:
        hid = c.execute(insert(households).values(
            name=f"{name} {uuid.uuid4().hex[:8]}").returning(households.c.id)).scalar_one()
    _SEEN_HOUSEHOLDS.add(hid)
    return hid


def _person(phone=CLIENT_NUMBER, *, active=True, household_id=None,
            first="Ada", last="Client", preferred=None):
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        pid = c.execute(insert(people).values(
            first_name=first, last_name=last, full_name=f"{first} {last} {tag}",
            preferred_name=preferred, primary_phone=phone,
            normalized_phone=normalize_phone(phone), primary_email=f"{tag}@example.com",
            active=active, household_id=household_id).returning(people.c.id)).scalar_one()
    _SEEN_PEOPLE.add(pid)
    return pid


def _auth(secret=SECRET):
    return {"Authorization": f"Bearer {secret}"}


def _lookup(client, number, **headers):
    return client.post(LOOKUP_PATH, json={"number": number}, headers=headers or _auth())


def _journal_payload(**overrides):
    payload = {"call_type": "Inbound", "number": CLIENT_NUMBER, "agent": "101",
               "duration": "00:03:21", "started_at_utc": STARTED_AT, "entity_id": ""}
    payload.update(overrides)
    return payload


def _journal(client, **overrides):
    response = client.post(JOURNAL_PATH, json=_journal_payload(**overrides), headers=_auth())
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if body.get("message_id"):
        with engine.connect() as c:
            _SEEN_CONVERSATIONS.add(c.execute(select(
                communication_messages.c.conversation_id).where(
                communication_messages.c.id == body["message_id"])).scalar_one())
    return response


def _message(message_id):
    with engine.connect() as c:
        return c.execute(select(communication_messages).where(
            communication_messages.c.id == message_id)).mappings().one()


# =============================== THE VENDOR BOUNDARY ===============================

def test_client360_never_calls_3cx_and_holds_no_3cx_credential():
    """The connector is the SERVER half only: 3CX calls in, Client360 never calls out.

    If this fails, someone added an outbound client — which would mean a 3CX credential to store,
    a recording endpoint within reach, and a live-call dependency in a request path. All three are
    explicitly out of scope.
    """
    package = pathlib.Path("app/integrations/threecx")
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("httpx.", "requests.", "urlopen", "aiohttp"):
            assert forbidden not in source, f"{path} appears to call out to 3CX ({forbidden})"


def test_no_recording_or_transcript_is_accepted_or_stored():
    """3CX can offer a recording link, a transcription and a sentiment score to ReportCall. The
    template asks for none of them and the journal stores none: a call ledger that holds what was
    SAID is a different artefact under a different retention rule."""
    xml = render()
    journal_source = pathlib.Path("app/services/communications/call_journal.py").read_text(
        encoding="utf-8")
    for variable in ("[RecordingUrl]", "[RecordingHyperLink]", "[Transcription]", "[Summary]",
                     "[Sentiment]"):
        assert variable not in xml, f"the template requests {variable}"
    for column in ('"recording', '"transcript', '"summary"'):
        assert column not in journal_source


# ================================ FAIL CLOSED ================================

def test_connector_is_off_by_default(monkeypatch):
    """A deployment that sets nothing exposes nothing."""
    monkeypatch.delenv("CLIENT360_3CX_ENABLED", raising=False)
    monkeypatch.delenv("CLIENT360_3CX_INTEGRATION_SECRET", raising=False)
    assert threecx_config.connector_enabled() is False


@pytest.mark.parametrize("path", [LOOKUP_PATH, JOURNAL_PATH])
def test_disabled_connector_404s_rather_than_401s(client, monkeypatch, path):
    """Switched off, the endpoints behave as though they do not exist: a probe must not learn that
    this deployment has a telephony surface at all."""
    monkeypatch.setenv("CLIENT360_3CX_ENABLED", "false")
    assert client.post(path, json={}, headers=_auth()).status_code == 404


@pytest.mark.parametrize("path", [LOOKUP_PATH, JOURNAL_PATH])
def test_enabled_without_a_secret_stays_404(client, monkeypatch, path):
    """Half a rollout must not leave an unauthenticated surface live. The enable flag alone does
    nothing without a provisioned secret."""
    monkeypatch.delenv("CLIENT360_3CX_INTEGRATION_SECRET", raising=False)
    assert client.post(path, json={}, headers=_auth()).status_code == 404


def test_a_too_short_secret_counts_as_no_secret(client, monkeypatch):
    """A placeholder left in an env file switches the connector OFF instead of guarding it weakly.
    Failing closed is the only safe reading of "this secret is too short to be one"."""
    monkeypatch.setenv("CLIENT360_3CX_INTEGRATION_SECRET", "short")
    assert threecx_config.integration_secret() is None
    assert client.post(LOOKUP_PATH, json={}, headers=_auth("short")).status_code == 404


@pytest.mark.parametrize("headers", [
    pytest.param({}, id="no-header"),
    pytest.param({"Authorization": "Bearer wrong-but-long-enough-value-here-xxxx"}, id="wrong-secret"),
    pytest.param({"Authorization": SECRET}, id="no-bearer-scheme"),
    pytest.param({"Authorization": "Basic " + SECRET}, id="wrong-scheme"),
])
def test_a_request_without_the_integration_secret_is_401(client, headers):
    response = client.post(LOOKUP_PATH, json={"number": CLIENT_NUMBER}, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_the_401_body_never_says_why_authentication_failed(client):
    body = client.post(LOOKUP_PATH, json={}, headers=_auth("x" * 40)).json()
    assert "required" in body["detail"].lower()
    for leak in ("length", "expired", "expect", "mismatch", SECRET):
        assert leak not in body["detail"]


def test_the_secret_is_not_a_staff_credential(client):
    """A Client360 session cookie must not reach these endpoints, and the integration secret must
    not be reusable anywhere else. The endpoints honour no ambient credential at all — which is
    also why listing them as session-exempt is not a CSRF hole."""
    response = client.post(LOOKUP_PATH, json={"number": CLIENT_NUMBER},
                           cookies={"session": "anything"})
    assert response.status_code == 401


@pytest.mark.parametrize("path", [LOOKUP_PATH, JOURNAL_PATH])
def test_endpoints_are_session_exempt_so_the_route_can_authenticate_them_itself(path):
    from app.security.middleware import PUBLIC_EXACT
    assert path in PUBLIC_EXACT


@pytest.mark.parametrize("path", [LOOKUP_PATH, JOURNAL_PATH])
def test_endpoints_accept_post_only(client, path):
    """A GET would put a client's phone number in the request line, where every access log and
    proxy along the path records it."""
    assert client.get(path, headers=_auth()).status_code == 405


def test_an_oversized_body_is_refused_before_it_is_parsed(client):
    response = client.post(LOOKUP_PATH, content=b"{" + b"x" * 20000, headers=_auth())
    assert response.status_code == 422


def test_a_non_json_body_is_refused_without_a_500(client):
    assert client.post(LOOKUP_PATH, content=b"not json", headers=_auth()).status_code == 422
    assert client.post(LOOKUP_PATH, content=b'"a string"', headers=_auth()).status_code == 422


# =========================== PHONE NORMALIZATION ===========================

def test_the_connector_uses_the_repositorys_one_phone_convention():
    """``people.normalized_phone`` is the only indexed client phone column, and matching anything
    against a client means agreeing with it EXACTLY. This pins that moving the normalizer out of
    ``sms_ingest`` kept one definition rather than creating a second."""
    assert sms_ingest.normalize_phone is normalize_phone
    for raw in ("+1 (555) 010-0000", "555-010-0000", "15550100000", "5550100000"):
        assert normalize_phone(raw) == CLIENT_NUMBER


def test_mask_never_reveals_more_than_the_last_four_digits():
    assert mask_number("+1 (555) 010-0000") == "***0000"
    assert mask_number("555") == "***"          # too short to leave a suffix: mask everything
    assert mask_number("") is None
    assert mask_number(None) is None
    # The masked form must never contain the number it came from.
    assert CLIENT_NUMBER not in mask_number(CLIENT_NUMBER)


def test_dial_uri_produces_something_the_3cx_handler_can_actually_dial():
    """Punctuation-free digits with a country code. A raw ``tel:(555) 010-0000`` dials on some
    desks and silently does nothing on others."""
    assert dial_uri("(555) 010-0000") == "tel:+15550100000"
    assert dial_uri("1-555-010-0000") == "tel:+15550100000"
    assert dial_uri("+44 20 7946 0000", "callto") == "callto:+442079460000"
    assert dial_uri("") is None
    assert dial_uri("not a number") is None


def test_dial_uri_does_not_guess_a_country_code_for_an_international_number():
    """Prefixing +1 onto a number that is not NANP would dial the wrong country, which is worse
    than not dialling."""
    assert dial_uri("00 44 20 7946 0000") == "tel:00442079460000"
    # A record that already carried a "+" keeps it: that is the caller's own country code, not a
    # guess this code made.
    assert dial_uri("+61 2 9374 4000") == "tel:+61293744000"


def test_the_profile_link_uses_the_filter_not_a_hand_built_tel_href():
    """The client profile must not concatenate ``tel:`` with a stored number — see
    :func:`app.services.communications.phone_numbers.dial_uri` for why the punctuation matters."""
    markup = pathlib.Path("app/templates/people/workspace.html").read_text(encoding="utf-8")
    assert "tel:{{" not in markup
    assert markup.count("|dial") == 2       # the header contact line and the details list


def test_the_dial_filter_is_registered_on_every_templates_instance():
    """63 route modules build their own Jinja2Templates; a filter installed on one of them works
    on some pages and raises on the rest."""
    from app.routes.people import templates
    assert "dial" in templates.env.filters
    assert templates.env.filters["dial"]("(555) 010-0000") == "tel:+15550100000"


# ================================== LOOKUP ==================================

def test_one_exact_match_returns_the_client_and_a_screen_pop_url(client):
    person_id = _person(preferred="Bill")
    body = _lookup(client, "+1 (555) 010-0000").json()

    assert body["found"] is True and body["match_count"] == 1
    contact = body["contacts"][0]
    assert contact["person_id"] == person_id
    assert contact["entity_id"] == str(person_id) and contact["entity_type"] == "person"
    # The preferred name wins: a client who goes by Bill should not be greeted as William.
    assert contact["display_name"] == "Bill"
    assert contact["contact_url"] == f"{BASE_URL}/people/{person_id}"


def test_a_formatting_difference_still_matches(client):
    """The PBX reports ``+15550100000``; the importer stored ``5550100000``. They are one number."""
    _person("(555) 010-0000")
    for reported in ("+15550100000", "1 555 010 0000", "555.010.0000"):
        assert _lookup(client, reported).json()["found"] is True


def test_an_unknown_number_pops_nothing_and_discloses_nothing(client):
    body = _lookup(client, "5559999999").json()
    assert body == {"found": False, "match_count": 0, "contacts": []}


def test_matching_is_exact_and_never_partial(client):
    """No prefix, suffix, substring or last-N-digits matching. Each of these is one plausible
    "helpful" relaxation, and each would pop a stranger's profile."""
    _person(CLIENT_NUMBER)
    for near_miss in ("5550100001", "555010000", "15550100000123", "0100000"):
        body = _lookup(client, near_miss).json()
        assert body["found"] is False, f"{near_miss} must not match {CLIENT_NUMBER}"


def test_two_people_on_one_number_pop_nothing_at_all(client):
    """A couple shares a mobile. There is no single profile to open and no single name to greet,
    so 3CX is told the count and nothing else — not even that the two are in one household."""
    household_id = _household()
    _person(CLIENT_NUMBER, household_id=household_id, first="Ada")
    _person(CLIENT_NUMBER, household_id=household_id, first="Grace")

    body = _lookup(client, CLIENT_NUMBER).json()
    assert body["found"] is False
    assert body["match_count"] == 2 and body["ambiguous"] is True
    assert body["contacts"] == []
    # No name, no email, no id, no URL anywhere in the response.
    assert "Ada" not in response_text(body) and "Grace" not in response_text(body)


def response_text(body):
    import json
    return json.dumps(body)


def test_an_inactive_client_is_not_popped(client):
    """A former client's record still holds their number. Popping it would present a closed
    relationship as a live one to whoever answers."""
    _person(CLIENT_NUMBER, active=False)
    assert _lookup(client, CLIENT_NUMBER).json()["match_count"] == 0


def test_an_unusable_number_is_answered_not_crashed(client):
    for junk in ("", None, "abc", "+++"):
        body = client.post(LOOKUP_PATH, json={"number": junk}, headers=_auth()).json()
        assert body["found"] is False and body["contacts"] == []


def test_the_screen_pop_url_fails_closed_without_a_canonical_origin(client, monkeypatch):
    """3CX opens this URL on a staff desktop. A host-header-derived one would open against
    whatever host an attacker put in the request; failing closed costs the pop and nothing else."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    _person(CLIENT_NUMBER)
    contact = _lookup(client, CLIENT_NUMBER).json()["contacts"][0]
    assert contact["contact_url"] == ""
    assert contact["display_name"]          # the name still comes back


def test_a_lookup_writes_no_client_rows(client):
    """The lookup READS. It must not create, touch or annotate a person."""
    _person(CLIENT_NUMBER)
    with engine.connect() as c:
        before = c.execute(select(people.c.id)).scalars().all()
    _lookup(client, CLIENT_NUMBER)
    _lookup(client, "5559999999")
    with engine.connect() as c:
        assert c.execute(select(people.c.id)).scalars().all() == before


# ============================== CALL JOURNALING ==============================

def test_a_completed_inbound_call_is_journaled_against_the_client(client):
    person_id = _person(CLIENT_NUMBER)
    body = _journal(client).json()

    assert body["journaled"] is True and body["created"] is True
    assert body["person_id"] == person_id

    row = _message(body["message_id"])
    assert row["channel"] == "phone_log" and row["direction"] == "inbound"
    assert row["status"] == "delivered"
    assert row["message_metadata"]["duration_seconds"] == 201
    assert row["message_metadata"]["agent_extension"] == "101"


def test_an_outbound_call_is_journaled_with_the_staff_member_as_sender(client):
    _person(CLIENT_NUMBER)
    body = _journal(client, call_type="Outbound").json()
    row = _message(body["message_id"])
    assert row["direction"] == "outbound" and row["sender_type"] == "user"
    assert row["sender_ref"] == "101"


def test_a_call_journal_never_records_what_was_said(client):
    """A call journal records THAT a call happened. The moment it holds content it becomes a
    different artefact under a different retention rule."""
    _person(CLIENT_NUMBER)
    row = _message(_journal(client).json()["message_id"])
    assert row["body"] is None


def test_the_call_lands_in_the_same_conversation_ledger_as_email_and_sms(client):
    """No new table: ``phone_log`` was already a legal channel and the call is visible to the same
    feed, governed by the same retention and authorization."""
    person_id = _person(CLIENT_NUMBER)
    message_id = _journal(client).json()["message_id"]
    with engine.connect() as c:
        conversation = c.execute(select(communication_conversations).where(
            communication_conversations.c.id == _message(message_id)["conversation_id"]
        )).mappings().one()
        assert conversation["channel"] == "phone_log"
        assert conversation["person_id"] == person_id
        assert c.execute(select(communication_events.c.event_type).where(
            communication_events.c.message_id == message_id)).scalar() == "call_journaled"
        assert c.execute(select(communication_recipients.c.message_id).where(
            communication_recipients.c.message_id == message_id)).scalar() == message_id


def test_several_calls_with_one_client_share_one_thread(client):
    """Keyed by the client and the number, deliberately NOT by the agent: one thread per advisor
    would fragment a single relationship across the feed."""
    _person(CLIENT_NUMBER)
    first = _journal(client, started_at_utc=STARTED_AT, agent="101").json()
    second = _journal(client, started_at_utc=LATER_SAME_DAY, agent="102").json()
    assert _message(first["message_id"])["conversation_id"] == \
           _message(second["message_id"])["conversation_id"]


# ------------------------------------------------------ de-duplication

def test_the_same_call_reported_twice_is_stored_once(client):
    """3CX retries. Without this, every retry becomes a second call in the client's history."""
    _person(CLIENT_NUMBER)
    first = _journal(client).json()
    second = _journal(client).json()

    assert second["message_id"] == first["message_id"]
    assert second["created"] is False and second["duplicate"] is True
    with engine.connect() as c:
        assert c.execute(select(communication_messages.c.id).where(
            communication_messages.c.conversation_id ==
            _message(first["message_id"])["conversation_id"])).scalars().all() == \
            [first["message_id"]]


def test_two_genuinely_different_calls_are_both_kept(client):
    """The mirror image of the test above, and the reason de-duplication cannot be sloppy: a
    client who really did call twice must not have one of those calls erased."""
    _person(CLIENT_NUMBER)
    first = _journal(client, started_at_utc="2026-03-04T14:03:00Z").json()
    second = _journal(client, started_at_utc="2026-03-04T14:03:01Z").json()
    assert first["message_id"] != second["message_id"]
    assert second["created"] is True


@pytest.mark.parametrize("changed", [
    pytest.param({"agent": "102"}, id="different-agent"),
    pytest.param({"call_type": "Outbound"}, id="different-direction"),
])
def test_each_part_of_the_derived_identity_actually_distinguishes_a_call(client, changed):
    _person(CLIENT_NUMBER)
    first = _journal(client).json()
    second = _journal(client, **changed).json()
    assert second["message_id"] != first["message_id"]


def test_a_vendor_call_id_takes_precedence_when_3cx_ever_supplies_one(client):
    """v20's ReportCall has no call-id variable, so identity is derived. ``call_id`` is still
    accepted, so a future release — or a CDR-driven poster — needs no server change."""
    _person(CLIENT_NUMBER)
    first = _journal(client, call_id="pbx-call-991").json()
    # Same vendor id, everything else different: still the same call.
    second = _journal(client, call_id="pbx-call-991", agent="999",
                      started_at_utc="2026-09-11T19:00:00Z").json()
    assert second["message_id"] == first["message_id"] and second["duplicate"] is True

    with engine.connect() as c:
        source = c.execute(select(communication_message_sources).where(
            communication_message_sources.c.message_id == first["message_id"])).mappings().one()
    assert source["source_metadata"]["identity_kind"] == call_journal.IDENTITY_VENDOR
    assert source["source_external_id"].endswith("pbx-call-991")


def test_the_row_records_which_identity_rule_produced_its_dedup_key(client):
    """A reader auditing for duplicates has to be able to tell a vendor-guaranteed id from one
    this codebase inferred. Left implicit, a derived key looks like a promise it is not."""
    _person(CLIENT_NUMBER)
    row = _message(_journal(client).json()["message_id"])
    assert row["message_metadata"]["identity_kind"] == call_journal.IDENTITY_DERIVED


def test_the_stored_dedup_key_does_not_itself_contain_a_phone_number(client):
    """The identifier appears in query output, exports and error text, so it is hashed rather than
    concatenated from the tuple it stands for."""
    _person(CLIENT_NUMBER)
    message_id = _journal(client).json()["message_id"]
    with engine.connect() as c:
        external_id = c.execute(select(communication_message_sources.c.source_external_id).where(
            communication_message_sources.c.message_id == message_id)).scalar_one()
    assert CLIENT_NUMBER not in external_id


def test_the_database_itself_enforces_call_identity():
    """The in-code lookup is the fast path. The UNIQUE constraint is what makes a duplicate
    impossible under concurrency, which is exactly when a PBX retry arrives."""
    person_id = _person(CLIENT_NUMBER)
    event = call_journal.CallEvent(
        provider_slug="3cx-testpbx", direction="inbound", counterparty_number=CLIENT_NUMBER,
        started_at=call_journal.parse_started_at(STARTED_AT), person_id=person_id, agent="101")
    with engine.begin() as c:
        message_id, created = call_journal.journal_call(c, event)
    with engine.connect() as c:
        _SEEN_CONVERSATIONS.add(c.execute(select(
            communication_messages.c.conversation_id).where(
            communication_messages.c.id == message_id)).scalar_one())
    assert created is True

    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(communication_message_sources.insert().values(
                message_id=message_id,
                source_system=call_journal.source_system("3cx-testpbx"),
                source_external_id=call_journal.source_external_id(event),
                source_metadata={}))


# ------------------------------------------------------ what is refused

@pytest.mark.parametrize("call_type", ["Missed", "Notanswered", "Unanswered"])
def test_an_incomplete_call_is_refused_rather_than_journaled(client, call_type):
    """Real events, but a notification concern with a different lifecycle. Admitting them would
    silently turn "calls with this client" into "call attempts"."""
    _person(CLIENT_NUMBER)
    response = client.post(JOURNAL_PATH, json=_journal_payload(call_type=call_type),
                           headers=_auth())
    assert response.status_code == 422
    assert "completed" in response.json()["detail"]


def test_the_template_also_skips_incomplete_calls():
    """Belt and braces: the server refuses them, and the template does not send them. A 3CX build
    that evaluates the condition differently still cannot get one into the ledger."""
    xml = render()
    assert '[CallType]==&quot;Missed&quot;' in xml
    assert '[CallType]==&quot;Notanswered&quot;' in xml


@pytest.mark.parametrize("payload,fragment", [
    pytest.param({"started_at_utc": ""}, "start time", id="missing-start"),
    pytest.param({"started_at_utc": "last tuesday"}, "ISO 8601", id="unparseable-start"),
    pytest.param({"duration": "3 minutes"}, "duration", id="unparseable-duration"),
    pytest.param({"duration": "00:99:00"}, "duration", id="impossible-duration"),
    pytest.param({"call_type": "Sideways"}, "Unrecognised", id="unknown-outcome"),
])
def test_a_malformed_field_is_refused_with_a_reason(client, payload, fragment):
    """A start time that silently defaulted to "now" would make every retry look like a new call,
    and a duration nobody could parse would be summed into a talk-time report nobody could
    reconcile."""
    _person(CLIENT_NUMBER)
    response = client.post(JOURNAL_PATH, json=_journal_payload(**payload), headers=_auth())
    assert response.status_code == 422
    assert fragment.lower() in response.json()["detail"].lower()


def test_a_call_from_an_unknown_number_is_accepted_but_not_filed(client):
    """There is nothing for the PBX to retry, so a 4xx would only make it keep trying. Nothing is
    written: an unanchored call belongs in a review decision, not a thread nobody owns."""
    response = client.post(JOURNAL_PATH, json=_journal_payload(number="5559999999"),
                           headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["journaled"] is False and body["reason"] == "no_match"
    with engine.connect() as c:
        assert c.execute(select(communication_messages.c.id).where(
            communication_messages.c.channel == "phone_log",
            communication_messages.c.subject.like("%9999%"))).scalars().all() == []


def test_a_call_on_a_shared_number_is_not_filed_against_a_guess(client):
    """The same rule as the screen-pop: two people on one number is not an anchor."""
    household_id = _household()
    _person(CLIENT_NUMBER, household_id=household_id)
    _person(CLIENT_NUMBER, household_id=household_id)
    body = client.post(JOURNAL_PATH, json=_journal_payload(), headers=_auth()).json()
    assert body["journaled"] is False and body["reason"] == "ambiguous_match"
    assert body["match_count"] == 2


def test_a_client_id_the_pbx_hands_back_is_checked_never_trusted(client):
    """Without this, anyone holding the integration secret could file a call against any client in
    the book by changing one field."""
    caller_id = _person(CLIENT_NUMBER)
    other_id = _person("5550200000")

    response = client.post(JOURNAL_PATH,
                           json=_journal_payload(entity_id=str(other_id)), headers=_auth())
    assert response.status_code == 422
    assert "different client" in response.json()["detail"]

    # Nothing was filed — against the client who was named, or against the one who actually rang.
    with engine.connect() as c:
        anchored = c.execute(select(communication_conversations.c.id).where(
            communication_conversations.c.channel == "phone_log",
            communication_conversations.c.person_id.in_((caller_id, other_id)))).scalars().all()
    assert anchored == []


def test_a_matching_entity_id_is_accepted(client):
    """The check is a consistency check, not a rejection of the field: the normal 3CX flow echoes
    back the id the lookup just returned."""
    person_id = _person(CLIENT_NUMBER)
    body = _journal(client, entity_id=str(person_id)).json()
    assert body["journaled"] is True


# =============================== PRIVACY ===============================

def _capture(logger_name):
    """Everything a logger emits, as text.

    ``caplog`` is not used: the application configures its own handlers, so a route's log line
    does not necessarily reach pytest's capturing handler, and a privacy assertion that silently
    inspects an empty string would pass forever while numbers leaked.
    """
    import logging

    class _Sink(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.DEBUG)
            self.lines: list[str] = []

        def emit(self, record):
            self.lines.append(self.format(record))

    logger = logging.getLogger(logger_name)
    sink = _Sink()
    logger.addHandler(sink)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    return logger, sink, previous


def test_no_stored_or_logged_surface_carries_a_full_phone_number(client):
    """Subjects are rendered in feeds, exports and notifications; audit metadata and log lines
    outlive the call. All of them carry the masked last-four form only."""
    logger, sink, previous = _capture("client360.threecx")
    person_id = _person(CLIENT_NUMBER)
    # The audit chain is append-only and shared with every other test in the run, so only the
    # entries this test appends are inspected.
    with engine.connect() as c:
        watermark = c.execute(select(audit_events.c.id).order_by(
            audit_events.c.id.desc()).limit(1)).scalar() or 0
    try:
        _lookup(client, CLIENT_NUMBER)
        message_id = _journal(client).json()["message_id"]
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous)

    row = _message(message_id)
    assert CLIENT_NUMBER not in (row["subject"] or "")
    assert "0000" in row["subject"]                      # still identifiable to a human
    assert CLIENT_NUMBER not in str(row["message_metadata"])

    with engine.connect() as c:
        conversation = c.execute(select(communication_conversations.c.subject).where(
            communication_conversations.c.id == row["conversation_id"])).scalar_one()
        source_metadata = c.execute(select(communication_message_sources.c.source_metadata).where(
            communication_message_sources.c.message_id == message_id)).scalar_one()
        entries = c.execute(select(audit_events.c.action, audit_events.c.metadata).where(
            audit_events.c.action.like("threecx.%"),
            audit_events.c.id > watermark)).mappings().all()
    assert CLIENT_NUMBER not in conversation
    assert CLIENT_NUMBER not in str(source_metadata)

    assert {"threecx.lookup", "threecx.call_journaled"} <= {e["action"] for e in entries}
    for entry in entries:
        assert CLIENT_NUMBER not in str(entry["metadata"])
        assert entry["metadata"].get("number_masked") == "***0000"

    logged = "\n".join(sink.lines)
    assert logged, "the connector should log what it did"
    assert CLIENT_NUMBER not in logged
    assert "***0000" in logged
    assert person_id is not None


def test_the_integration_secret_never_reaches_a_log_or_a_response(client):
    """Including on the REJECTION path, which is where a "helpfully" verbose message would put
    the presented credential into a log file."""
    logger, sink, previous = _capture("client360.threecx")
    try:
        body = client.post(LOOKUP_PATH, json={"number": CLIENT_NUMBER}, headers=_auth()).text
        rejected = client.post(LOOKUP_PATH, json={}, headers=_auth("x" * 40)).text
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous)
    logged = "\n".join(sink.lines)
    assert SECRET not in logged and SECRET not in body
    assert "x" * 40 not in logged and "x" * 40 not in rejected


def test_an_error_body_never_carries_the_number_it_is_about(client):
    """Refusal text reaches a PBX administrator's request log."""
    _person(CLIENT_NUMBER)
    detail = client.post(JOURNAL_PATH, json=_journal_payload(duration="3 minutes"),
                         headers=_auth()).json()["detail"]
    assert CLIENT_NUMBER not in detail


def test_an_unmatched_lookup_is_audited_without_becoming_a_call_detail_record(client):
    """The gap stays visible, but an audit chain that recorded every number a PBX ever asked about
    would BE a call-detail record, which is not what it is for."""
    _lookup(client, "5559999999")
    with engine.connect() as c:
        entry = c.execute(select(audit_events).where(
            audit_events.c.action == "threecx.lookup").order_by(
            audit_events.c.id.desc()).limit(1)).mappings().one()
    assert entry["outcome"] == "no_match"
    assert entry["metadata"]["number_masked"] == "***9999"
    assert "5559999999" not in str(entry["metadata"])


# ============================ THE 3CX XML CONTRACT ============================

def test_the_checked_in_template_matches_the_renderer():
    """The operator uploads the file; the renderer knows the endpoints. If these disagree, the
    integration breaks as a silent absence of screen-pops on a Monday morning rather than as a red
    test here."""
    on_disk = pathlib.Path(ARTEFACT_PATH).read_text(encoding="utf-8")
    assert on_disk == render(), (
        f"{ARTEFACT_PATH} is stale. Re-render it from app/integrations/threecx/template.py.")


def test_the_template_is_well_formed_xml():
    root = ET.fromstring(render())
    assert root.tag == "Crm"
    assert root.get("Name") == threecx_config.TEMPLATE_NAME
    assert root.get("Version") == str(threecx_config.TEMPLATE_VERSION)


def test_the_template_declares_the_two_scenarios_3cx_reserves():
    """``Id=""`` is contact lookup by number and ``Id="ReportCall"`` is call journaling; 3CX runs
    exactly one ReportCall scenario."""
    scenarios = ET.fromstring(render()).find("Scenarios").findall("Scenario")
    assert [s.get("Id") for s in scenarios] == ["", "ReportCall"]


def test_the_template_calls_this_servers_actual_endpoints():
    scenarios = ET.fromstring(render()).find("Scenarios").findall("Scenario")
    assert scenarios[0].find("Request").get("Url") == f"[BaseUrl]{LOOKUP_PATH}"
    assert scenarios[1].find("Request").get("Url") == f"[BaseUrl]{JOURNAL_PATH}"


def test_the_template_reads_the_json_paths_the_lookup_actually_returns(client):
    """The XPath-style ``Path`` attributes are the contract. This walks them against a real
    response rather than trusting that two files were edited together."""
    _person(CLIENT_NUMBER)
    contact = _lookup(client, CLIENT_NUMBER).json()["contacts"][0]

    lookup_scenario = ET.fromstring(render()).find("Scenarios").find("Scenario")
    assert lookup_scenario.find("Rules").find("Rule").text == "contacts"
    for variable in lookup_scenario.find("Variables").findall("Variable"):
        container, _, field = variable.get("Path").partition(".")
        assert container == "contacts"
        assert field in contact, f"the template reads contacts.{field}, which is not returned"


def test_the_template_sends_exactly_the_fields_the_journal_endpoint_reads():
    """A field the endpoint ignores is dead weight on every call; a field it needs and the
    template omits is data lost silently."""
    journal_scenario = ET.fromstring(render()).find("Scenarios").findall("Scenario")[1]
    sent = {v.get("Key") for v in journal_scenario.find("Request").iter("Value")
            if v.get("Key") != "Authorization"}
    assert sent == {"call_type", "number", "agent", "duration", "started_at_utc", "entity_id"}

    handler = pathlib.Path("app/routes/threecx.py").read_text(encoding="utf-8")
    for field in sent:
        assert f'payload.get("{field}")' in handler, f"the template sends {field}, unread by the route"


@pytest.mark.parametrize("index", [0, 1])
def test_a_json_body_is_built_with_postvalues_not_a_hand_escaped_message(index):
    """3CX's specification: ``Message`` and ``<PostValues>`` are mutually exclusive for a JSON
    body, and ``RequestContentType`` stays empty because ``RequestEncoding="Json"`` sets
    ``application/json`` itself. Hand-escaping JSON into an XML attribute is the older style and
    the source of an entire class of quoting bug."""
    request = ET.fromstring(render()).find("Scenarios").findall("Scenario")[index].find("Request")
    assert request.get("RequestEncoding") == "Json"
    assert request.get("RequestType") == "Post"
    assert request.get("RequestContentType") == ""
    assert request.get("Message") == ""
    post_values = request.find("PostValues")
    assert post_values is not None
    # A top-level JSON object: one <Object Key=""> holding the fields.
    assert [child.tag for child in post_values] == ["Object"]
    assert post_values.find("Object").get("Key") == ""


def test_the_template_invents_no_call_id_variable():
    """3CX v20's ReportCall exposes no call id — 3CX support states it is available from the CDR
    only. Writing ``[CallID]`` into the template would render as an empty string and quietly
    destroy de-duplication, so the derived identity is used instead."""
    xml = render()
    for invented in ("[CallID]", "[CallId]", "[UniqueCallId]", "[CallUUID]"):
        assert invented not in xml


def test_the_template_uses_only_documented_reportcall_variables():
    """The documented set for call journaling. Anything outside it renders empty at runtime, which
    is a failure that shows up as missing data weeks later rather than as an error."""
    documented = {"[CallType]", "[Number]", "[Name]", "[Agent]", "[Duration]", "[DateTime]",
                  "[CallStartTimeLocal]", "[CallStartTimeUTC]", "[EntityId]", "[EntityType]",
                  # Template parameters, which resolve the same way.
                  "[BaseUrl]", "[IntegrationSecret]", "[ReportCallEnabled]"}
    import re
    journal_scenario = ET.fromstring(render()).find("Scenarios").findall("Scenario")[1]
    blob = ET.tostring(journal_scenario, encoding="unicode")
    for token in re.findall(r"\[[A-Za-z]\w*\]", blob):
        assert token in documented, f"{token} is not a documented ReportCall variable"


def test_the_template_carries_the_secret_by_reference_never_as_a_literal():
    """The file is committed and shared with a PBX administrator. A baked-in secret would be
    disclosed by the artefact itself."""
    xml = pathlib.Path(ARTEFACT_PATH).read_text(encoding="utf-8")
    assert "Bearer [IntegrationSecret]" in xml
    assert SECRET not in xml
    assert 'Authentication Type="No"' in xml     # header auth, not Basic


def test_the_template_bounds_what_the_pbx_can_open():
    """A PBX under load must not be able to open an unbounded number of connections to the
    application server."""
    assert int(ET.fromstring(render()).find("Connection").get("MaxConcurrentRequests")) <= 4
