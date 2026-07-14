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
