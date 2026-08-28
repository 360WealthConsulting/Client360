from app.jobs.microsoft_calendar_sync import (
    build_person_email_index,
    calendar_external_id,
    process_calendar_events,
)


def sample_event():
    return {
        "id": "event-123",
        "subject": "Annual planning meeting",
        "bodyPreview": "Review goals and next steps.",
        "start": {"dateTime": "2026-07-20T14:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-07-20T15:00:00Z", "timeZone": "UTC"},
        "location": {"displayName": "Conference Room A"},
        "organizer": {
            "emailAddress": {
                "name": "Advisor",
                "address": "advisor@example.com",
            }
        },
        "attendees": [
            {
                "emailAddress": {
                    "name": "Known Client",
                    "address": " Client@Example.com ",
                },
                "type": "required",
                "status": {"response": "accepted"},
            },
            {
                "emailAddress": {
                    "name": "Unknown Guest",
                    "address": "unknown@example.com",
                },
                "type": "optional",
                "status": {"response": "tentativelyAccepted"},
            },
        ],
        "isCancelled": False,
        "isOnlineMeeting": True,
        "onlineMeeting": {"joinUrl": "https://teams.example/join"},
        "webLink": "https://outlook.example/event-123",
        "responseStatus": {"response": "accepted"},
    }


def test_build_person_email_index_normalizes_addresses():
    index = build_person_email_index(
        [
            {
                "id": 42,
                "primary_email": " Client@Example.com ",
                "normalized_email": None,
            }
        ]
    )

    assert index == {"client@example.com": 42}


def test_build_person_email_index_excludes_ambiguous_addresses():
    index = build_person_email_index(
        [
            {
                "id": 42,
                "primary_email": "same@example.com",
                "normalized_email": None,
            },
            {
                "id": 84,
                "primary_email": "same@example.com",
                "normalized_email": None,
            },
        ]
    )

    assert index == {}


def test_process_calendar_events_publishes_matches_and_queues_unmatched():
    published = []
    queued = []

    result = process_calendar_events(
        [sample_event()],
        owner_email="advisor@example.com",
        person_by_email={"client@example.com": 42},
        publish=lambda **values: published.append(values),
        queue_unmatched=lambda **values: queued.append(values),
        resolve_match=lambda **values: None,
    )

    assert result == {
        "events_reviewed": 1,
        "matched_events": 1,
        "unmatched_attendees": 1,
        "cancelled_events": 0,
        "published_events": 1,
    }
    assert published[0]["person_id"] == 42
    assert published[0]["external_id"] == calendar_external_id(
        "event-123", 42
    )
    metadata = published[0]["event_metadata"]
    assert metadata["organizer"]["email"] == "advisor@example.com"
    assert metadata["online_meeting_link"] == "https://teams.example/join"
    assert metadata["body_preview"] == "Review goals and next steps."
    assert metadata["attendees"][0]["response_status"] == "accepted"
    assert queued[0]["participant"]["email"] == "unknown@example.com"


def test_repeated_sync_uses_same_deduplication_key():
    timeline_by_external_id = {}

    def upsert_timeline(**values):
        timeline_by_external_id[values["external_id"]] = values

    for _ in range(2):
        process_calendar_events(
            [sample_event()],
            owner_email="advisor@example.com",
            person_by_email={"client@example.com": 42},
            publish=upsert_timeline,
            queue_unmatched=lambda **values: None,
            resolve_match=lambda **values: None,
        )

    assert list(timeline_by_external_id) == [
        "outlook-calendar-event-123-person-42"
    ]


def test_cancelled_events_are_not_published_or_queued():
    event = sample_event()
    event["isCancelled"] = True
    published = []
    queued = []

    result = process_calendar_events(
        [event],
        owner_email="advisor@example.com",
        person_by_email={"client@example.com": 42},
        publish=lambda **values: published.append(values),
        queue_unmatched=lambda **values: queued.append(values),
        resolve_match=lambda **values: None,
    )

    assert result["cancelled_events"] == 1
    assert published == []
    assert queued == []


# --- null onlineMeeting (Graph sends the key with an explicit null) -------------------------
#
# Graph returns "onlineMeeting": null for an event that is not an online meeting. The key is
# PRESENT, so `event.get("onlineMeeting", {})` returns None rather than the {} default, and
# chaining `.get("joinUrl")` onto it raised AttributeError — which failed the whole calendar sync,
# not just that one event. Both metadata builders read this field, so both are covered here.

import uuid

import pytest
from sqlalchemy import select

from app.db import engine, microsoft_unmatched_calendar_attendees
from app.jobs.microsoft_calendar_sync import (
    build_timeline_metadata,
    queue_unmatched_calendar_attendee,
)

#: The four shapes Graph actually sends, and the join URL each must produce.
ONLINE_MEETING_CASES = [
    pytest.param({}, None, id="key-absent"),
    pytest.param({"onlineMeeting": None}, None, id="explicit-null"),
    pytest.param({"onlineMeeting": {}}, None, id="empty-object"),
    pytest.param({"onlineMeeting": {"joinUrl": "https://example.test/meeting"}},
                 "https://example.test/meeting", id="real-join-url"),
]


@pytest.mark.parametrize("overlay,expected", ONLINE_MEETING_CASES)
def test_build_timeline_metadata_handles_every_online_meeting_shape(overlay, expected):
    """Site 1: build_timeline_metadata (app/jobs/microsoft_calendar_sync.py:133)."""
    event = sample_event()
    event.pop("onlineMeeting", None)
    event.update(overlay)

    metadata = build_timeline_metadata(event)          # must not raise

    assert metadata["online_meeting_link"] == expected


@pytest.mark.parametrize("overlay,expected", ONLINE_MEETING_CASES)
def test_queue_unmatched_calendar_attendee_handles_every_online_meeting_shape(overlay, expected):
    """Site 2: queue_unmatched_calendar_attendee (app/jobs/microsoft_calendar_sync.py:253).

    Asserts on the PERSISTED row, so this covers the real write path rather than a return value."""
    event = sample_event()
    event.pop("onlineMeeting", None)
    event.update(overlay)
    event["id"] = f"evt-{uuid.uuid4().hex[:12]}"

    queue_unmatched_calendar_attendee(                 # must not raise
        event=event,
        participant={"email": f"guest-{uuid.uuid4().hex[:8]}@example.test",
                     "name": "Unknown Guest", "role": "optional",
                     "response_status": "none"},
        metadata={"source": "test"})

    with engine.connect() as connection:
        stored = connection.execute(select(
            microsoft_unmatched_calendar_attendees.c.online_meeting_link).where(
            microsoft_unmatched_calendar_attendees.c.microsoft_event_id == event["id"])
        ).scalars().one()
    assert stored == expected


def test_an_explicit_null_online_meeting_does_not_disturb_the_other_metadata():
    """Requirement 5: normal calendar metadata behaviour is unchanged by the fix."""
    event = sample_event()
    baseline = build_timeline_metadata(event)
    event["onlineMeeting"] = None

    metadata = build_timeline_metadata(event)

    assert metadata["online_meeting_link"] is None
    for field in ("microsoft_event_id", "subject", "body_preview", "organizer", "attendees",
                  "start", "end", "location", "web_link", "response_status"):
        assert metadata[field] == baseline[field], f"{field} changed"
    # isOnlineMeeting is a separate Graph field and is reported independently of the join URL.
    assert metadata["is_online_meeting"] is True


def test_both_online_meeting_reads_are_null_safe_in_source():
    """Guards against either site regressing to the `{}`-default form."""
    import inspect

    from app.jobs import microsoft_calendar_sync

    src = inspect.getsource(microsoft_calendar_sync)
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    assert 'get("onlineMeeting", {})' not in code, "an unguarded onlineMeeting read came back"
    assert code.count('(event.get("onlineMeeting") or {}).get("joinUrl")') == 2


# --- nullable Graph objects (hardening pass) ------------------------------------------------
#
# Same defect class as the onlineMeeting fix, on every other nested object this module reads.
# Graph distinguishes ABSENT from EXPLICITLY NULL: an event with no location arrives as
# "location": null with the key present, so a `{}` default never applies and the chained .get()
# raises AttributeError — taking down the whole sync, not one field. A null "attendees" is the
# same bug one type over: iterating None raises TypeError.

from datetime import datetime

from app.jobs.microsoft_calendar_sync import event_participants

#: The four shapes every nullable nested object must survive.
NESTED_SHAPES = [
    pytest.param("missing", id="key-missing"),
    pytest.param("null", id="explicit-null"),
    pytest.param("empty", id="empty"),
    pytest.param("populated", id="populated"),
]


def _apply_shape(event, key, shape, *, empty, populated):
    """Put `key` into one of the four states, in place."""
    event.pop(key, None)
    if shape == "null":
        event[key] = None
    elif shape == "empty":
        event[key] = empty
    elif shape == "populated":
        event[key] = populated
    return event


# --- event-level nested objects -------------------------------------------------------------

@pytest.mark.parametrize("shape", NESTED_SHAPES)
def test_location_is_null_safe(shape):
    event = _apply_shape(sample_event(), "location", shape,
                         empty={}, populated={"displayName": "Room 9"})

    metadata = build_timeline_metadata(event)              # must not raise

    assert metadata["location"] == ("Room 9" if shape == "populated" else None)


@pytest.mark.parametrize("shape", NESTED_SHAPES)
def test_response_status_is_null_safe(shape):
    event = _apply_shape(sample_event(), "responseStatus", shape,
                         empty={}, populated={"response": "declined"})

    metadata = build_timeline_metadata(event)              # must not raise

    assert metadata["response_status"] == ("declined" if shape == "populated" else None)


@pytest.mark.parametrize("shape", NESTED_SHAPES)
def test_organizer_is_null_safe(shape):
    event = _apply_shape(
        sample_event(), "organizer", shape, empty={},
        populated={"emailAddress": {"name": "Boss", "address": "boss@example.test"}})

    metadata = build_timeline_metadata(event)              # must not raise
    participants = event_participants(event)               # must not raise

    if shape == "populated":
        assert metadata["organizer"]["email"] == "boss@example.test"
        assert any(p["role"] == "organizer" for p in participants)
    else:
        # normalize_email(None) -> "" is the module's pre-existing contract, unchanged here.
        assert metadata["organizer"] == {"name": None, "email": ""}
        assert not any(p["role"] == "organizer" for p in participants)


@pytest.mark.parametrize("shape", NESTED_SHAPES)
def test_a_null_organizer_email_address_is_null_safe(shape):
    """The SECOND level: organizer present, emailAddress null."""
    event = sample_event()
    event["organizer"] = _apply_shape(
        {}, "emailAddress", shape, empty={},
        populated={"name": "Boss", "address": "boss@example.test"})

    metadata = build_timeline_metadata(event)              # must not raise
    event_participants(event)                              # must not raise

    assert metadata["organizer"]["email"] == (
        "boss@example.test" if shape == "populated" else "")


@pytest.mark.parametrize("shape", NESTED_SHAPES)
def test_attendees_collection_is_null_safe(shape):
    """A null list is the same defect one type over: iterating None raises TypeError."""
    event = _apply_shape(
        sample_event(), "attendees", shape, empty=[],
        populated=[{"emailAddress": {"name": "Guest", "address": "guest@example.test"},
                    "type": "required", "status": {"response": "accepted"}}])

    metadata = build_timeline_metadata(event)              # must not raise
    participants = event_participants(event)               # must not raise

    expected = 1 if shape == "populated" else 0
    assert len(metadata["attendees"]) == expected
    assert len([p for p in participants if p["role"] != "organizer"]) == expected


# --- attendee-level nested objects ----------------------------------------------------------

@pytest.mark.parametrize("shape", NESTED_SHAPES)
def test_attendee_email_address_is_null_safe(shape):
    event = sample_event()
    event["attendees"] = [_apply_shape(
        {"type": "required", "status": {"response": "accepted"}}, "emailAddress", shape,
        empty={}, populated={"name": "Guest", "address": "guest@example.test"})]

    metadata = build_timeline_metadata(event)              # must not raise
    participants = event_participants(event)               # must not raise

    if shape == "populated":
        assert metadata["attendees"][0]["email"] == "guest@example.test"
        assert {p["role"] for p in participants} == {"organizer", "required"}
    else:
        assert metadata["attendees"][0]["email"] == ""     # normalize_email(None) -> ""
        # An attendee with no resolvable address is skipped, never guessed at.
        assert all(p["role"] == "organizer" for p in participants)


@pytest.mark.parametrize("shape", NESTED_SHAPES)
def test_attendee_status_is_null_safe(shape):
    event = sample_event()
    event["attendees"] = [_apply_shape(
        {"emailAddress": {"name": "Guest", "address": "guest@example.test"},
         "type": "required"}, "status", shape,
        empty={}, populated={"response": "tentativelyAccepted"})]

    metadata = build_timeline_metadata(event)              # must not raise
    participants = event_participants(event)               # must not raise

    guest = [p for p in participants if p["email"] == "guest@example.test"][0]
    if shape == "populated":
        assert metadata["attendees"][0]["response_status"] == "tentativelyAccepted"
        assert guest["response_status"] == "tentativelyAccepted"
    else:
        assert metadata["attendees"][0]["response_status"] is None
        assert guest["response_status"] == "none"          # documented default, not None


# --- start / end -----------------------------------------------------------------------------

@pytest.mark.parametrize("shape", NESTED_SHAPES)
@pytest.mark.parametrize("key", ["start", "end"])
def test_start_and_end_are_null_safe_on_the_queue_path(shape, key):
    """Site: queue_unmatched_calendar_attendee — asserts on the persisted row."""
    event = _apply_shape(sample_event(), key, shape, empty={},
                         populated={"dateTime": "2026-09-01T10:00:00Z", "timeZone": "UTC"})
    event["id"] = f"evt-{uuid.uuid4().hex[:12]}"
    column = {"start": microsoft_unmatched_calendar_attendees.c.starts_at,
              "end": microsoft_unmatched_calendar_attendees.c.ends_at}[key]

    queue_unmatched_calendar_attendee(                     # must not raise
        event=event,
        participant={"email": f"guest-{uuid.uuid4().hex[:8]}@example.test",
                     "name": "Guest", "role": "optional", "response_status": "none"},
        metadata={"source": "test"})

    with engine.connect() as connection:
        stored = connection.execute(select(column).where(
            microsoft_unmatched_calendar_attendees.c.microsoft_event_id == event["id"])
        ).scalars().one()
    # parse_graph_datetime falls back to "now" for a missing value, so a timestamp always exists.
    assert isinstance(stored, datetime)
    if shape == "populated":
        assert stored.year == 2026 and stored.month == 9


@pytest.mark.parametrize("shape", NESTED_SHAPES)
def test_a_null_start_does_not_break_publishing(shape):
    """Site: the publish path inside process_calendar_events."""
    event = _apply_shape(sample_event(), "start", shape, empty={},
                         populated={"dateTime": "2026-09-01T10:00:00Z", "timeZone": "UTC"})
    published = []

    result = process_calendar_events(                      # must not raise
        [event], owner_email="advisor@example.com",
        person_by_email={"client@example.com": 42},
        publish=lambda **values: published.append(values),
        queue_unmatched=lambda **values: None,
        resolve_match=lambda **values: None)

    assert result["published_events"] == 1
    assert isinstance(published[0]["event_time"], datetime)


# --- the whole payload at once ----------------------------------------------------------------

def test_an_event_whose_every_nested_object_is_null_still_processes():
    """The realistic worst case: a minimal Graph event with explicit nulls throughout."""
    event = {"id": "evt-all-null", "subject": None, "bodyPreview": None,
             "start": None, "end": None, "location": None, "organizer": None,
             "attendees": None, "isCancelled": False, "isOnlineMeeting": None,
             "onlineMeeting": None, "webLink": None, "responseStatus": None}

    metadata = build_timeline_metadata(event)              # must not raise
    participants = event_participants(event)               # must not raise

    assert participants == []
    assert metadata["subject"] == "(No subject)"
    assert metadata["attendees"] == []
    assert metadata["location"] is None
    assert metadata["online_meeting_link"] is None
    assert metadata["response_status"] is None
    assert metadata["is_online_meeting"] is False


def test_a_fully_populated_event_is_completely_unchanged_by_the_hardening():
    """Requirement: normal dictionary payloads behave exactly as before."""
    metadata = build_timeline_metadata(sample_event())

    assert metadata["location"] == "Conference Room A"
    assert metadata["response_status"] == "accepted"
    assert metadata["organizer"] == {"name": "Advisor", "email": "advisor@example.com"}
    assert metadata["online_meeting_link"] == "https://teams.example/join"
    assert len(metadata["attendees"]) == 2
    assert metadata["attendees"][0]["email"] == "client@example.com"
    assert metadata["attendees"][0]["response_status"] == "accepted"
    assert metadata["attendees"][1]["response_status"] == "tentativelyAccepted"
    assert [p["role"] for p in event_participants(sample_event())] == [
        "organizer", "required", "optional"]


def test_no_unsafe_nested_read_remains_in_the_module():
    """Guards every site at once against a regression to the `{}`/`[]`-default form."""
    import inspect
    import re

    from app.jobs import microsoft_calendar_sync

    src = inspect.getsource(microsoft_calendar_sync)
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    unsafe = re.findall(r'\.get\("[^"]+",\s*(?:\{\}|\[\])\)', code)
    # The only permitted survivor is the OData collection envelope, which is never null on a 2xx.
    assert unsafe == ['.get("value", [])'], f"unsafe nested reads: {unsafe}"
